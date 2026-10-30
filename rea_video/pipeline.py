"""decompose(): the end-to-end reverse-engineering pass for one video."""
from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import SCHEMA, __version__
from .assets import detect_embedded, material_summary
from .audio import analyze_audio, merge_audio_with_vlm
from .sound_source import MUSIC_ZH, classify_sound_sources, per_clip_audio
from .classify import LABEL_ZH, RULES, deterministic, fuse, video_context, image_source_guess, overlay_layers, top, verdict_text
from .features import collect_samples, shot_features
from .media import cut_clip, extract_frame, require_tools, video_info
from .overlay import ocr_frames, persistent_template, static_overlays, summarize_text
from .plan import Planner
from .probe import provenance
from .shots import build_track, detect_shots
from .util import LOG, fmt_time, round_floats, write_json
from . import aspects
from .faces import shot_faces
from .visual import shot_sheet, storyboard
from .vlm import SHOT_PROMPT, WHOLE_PROMPT, VLMClient, VLMConfig, normalize_shot_result, shot_hints, vlm_note


def _sha256_head(path: Path, limit: int = 64 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        h.update(f.read(limit))
    return h.hexdigest()


def decompose(video: str | Path, out_dir: str | Path, *, use_vlm: bool = False, whole_vlm: bool = True,
              ocr: bool = True, workers: int = 3, engine: str = "seedance2.0", max_shots_vlm: int = 60,
              platform_meta: dict | None = None, stems: bool = True, memory: bool = True) -> dict:
    require_tools()
    t0 = time.time()
    video = Path(video).expanduser().resolve()
    out = Path(out_dir).expanduser().resolve()
    (out / "shots").mkdir(parents=True, exist_ok=True)
    info = video_info(video)
    prov = provenance(info)
    LOG.info("video %s %dx%d %.2ffps %.2fs", video.name, info.width, info.height, info.fps, info.duration)

    track = build_track(str(video), info)
    shots = detect_shots(track)
    LOG.info("detected %d shots", len(shots))
    marks = [b / s.fps for s in shots for b in s.beats]
    audio = analyze_audio(str(video), [s.start for s in shots[1:]], marks) if info.has_audio else {"has_audio": False}
    stem_info: dict = {}
    if info.has_audio and stems:  # strip the soundtrack: original.wav / vocals.wav / music.wav
        from .audio import refine_with_stems
        from .stems import extract_stems

        LOG.info("separating stems (vocals / music)")
        stem_info = extract_stems(video, out)
        if stem_info.get("ok"):
            refine_with_stems(audio, stem_info["vocals"])

    shot_records: list[dict] = []
    template_frames: list[np.ndarray] = []
    samples_by_shot = {}
    for s in shots:
        smp = collect_samples(str(video), info, s)
        samples_by_shot[s.index] = smp
        if smp.frames:
            template_frames.extend([smp.frames[0], smp.frames[-1]])
    template = persistent_template(template_frames[:60]) if len(shots) >= 2 else {"frozen_ratio": 0.0, "boxes": []}

    for s in shots:
        smp = samples_by_shot[s.index]
        feats = shot_features(smp, info.fps)
        ov = static_overlays(smp.frames)
        text = summarize_text(ocr_frames(smp.frames, smp.frame_times)) if ocr else {"tracks": [], "text_coverage": 0.0, "items": []}
        embedded = detect_embedded(smp.frames, text.get("items"))
        fd = shot_faces(smp.frames)
        faces = fd["presence"]
        areas = [p["face"]["area"] for p in fd["frames"] if p.get("face")]
        feats["face_presence"] = faces
        feats["face_area"] = float(np.median(areas)) if areas else 0.0
        asp = aspects.measure_shot(smp.frames, fd, feats, s.duration)
        probs, evidence, facts = deterministic(feats, ov, text, faces, audio, prov, fps=info.fps)
        layers = overlay_layers(ov, text, template)
        sid = f"shot_{s.index:03d}"
        shot_sheet(smp.frames, smp.frame_times, out / "shots" / f"{sid}.jpg")
        extract_frame(video, s.start + min(0.05, s.duration / 4), out / "shots" / f"{sid}_first.jpg", width=min(1080, info.width or 1080))
        extract_frame(video, max(s.start, s.end - min(0.08, s.duration / 4)), out / "shots" / f"{sid}_last.jpg", width=min(1080, info.width or 1080))
        rec = {
            "id": sid, **s.to_dict(), "features": round_floats(feats), "faces": round(faces, 3),
            "aspects": round_floats(asp), "overlay": round_floats(ov), "text": text, "layers": layers, "embedded_assets": round_floats(embedded),
            "deterministic": {"probs": round_floats(probs), "evidence": evidence, "facts": facts},
            "assets": {"sheet": f"shots/{sid}.jpg", "first_frame": f"shots/{sid}_first.jpg", "last_frame": f"shots/{sid}_last.jpg"},
            "vlm": None,
        }
        shot_records.append(rec)
        dl, dp = top(probs)
        LOG.info("%s %s-%s det=%s(%.0f%%) %s", sid, fmt_time(s.start), fmt_time(s.end), dl, dp * 100, facts.get("temporal_family"))

    # ---------------- VLM pass ------------------------------------------------------
    # "used" means at least one VLM answer actually came back; "status" says why not otherwise.
    vlm_info = {"used": False, "requested": use_vlm, "status": "off"}
    whole = None
    if use_vlm:
        cfg = VLMConfig.from_env()
        if cfg is None:
            LOG.warning("--vlm requested but no VLM config found (REA_VLM_* / VIDEO_ANALYSIS_* / GEMINI3_*); continuing without it")
            vlm_info = {"used": False, "requested": True, "status": "not_configured", "error": "no_config"}
        else:
            client = VLMClient(cfg, out / "vlm_cache")
            vlm_info = {"used": False, "requested": True, "status": "failed", "model": cfg.describe(), "media": cfg.media, "errors": []}

            def run_shot(rec: dict) -> None:
                media = []
                if cfg.media in ("both", "video"):
                    clip = cut_clip(video, rec["start"], min(rec["duration"], 12.0), out / "clips" / f"{rec['id']}.mp4", width=480)
                    media.append((clip, "video/mp4"))
                if cfg.media in ("both", "frames"):
                    media += [(out / rec["assets"]["first_frame"], "image/jpeg"), (out / rec["assets"]["sheet"], "image/jpeg"),
                              (out / rec["assets"]["last_frame"], "image/jpeg")]
                dl, dp = top(rec["deterministic"]["probs"])
                prompt = SHOT_PROMPT.format(hints=shot_hints(rec["features"], rec["deterministic"]["facts"], dl, dp))
                try:
                    rec["vlm"] = normalize_shot_result(client.ask_json(prompt, media))
                except Exception as exc:  # keep going; deterministic result stands
                    if cfg.media == "both":
                        try:
                            frames_only = [m for m in media if m[1] != "video/mp4"]
                            rec["vlm"] = normalize_shot_result(client.ask_json(prompt, frames_only))
                            rec["vlm"]["_fallback"] = "frames_only"
                            return
                        except Exception as exc2:  # pragma: no cover
                            exc = exc2
                    vlm_info["errors"].append(f"{rec['id']}: {str(exc)[:160]}")

            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                list(pool.map(run_shot, shot_records[:max_shots_vlm]))
            if whole_vlm:
                try:
                    low = cut_clip(video, 0, info.duration, out / "clips" / "whole_480p.mp4", width=480, audio=True, crf=30)
                    cuts = ", ".join(f"{r['start']:.2f}" for r in shot_records)
                    whole = client.ask_json(WHOLE_PROMPT.format(shots=cuts), [(low, "video/mp4")], max_tokens=6000)
                except Exception as exc:
                    vlm_info["errors"].append(f"whole: {str(exc)[:160]}")
            asked = shot_records[:max_shots_vlm]
            ok_shots = sum(1 for r in asked if r.get("vlm"))
            vlm_info.update(ok_shots=ok_shots, asked_shots=len(asked), whole_ok=whole is not None)
            if ok_shots == len(asked) and (whole is not None or not whole_vlm):
                vlm_info.update(used=True, status="ok")
            elif ok_shots or whole is not None:
                vlm_info.update(used=True, status="partial")
            else:
                LOG.warning("VLM: every call failed (%s); result is deterministic only", (vlm_info["errors"] or ["?"])[0])

    breakdown = {
        "schema": SCHEMA, "tool": {"name": "rea-video", "version": __version__},
        "source": {"path": str(video), "filename": video.name, "sha256_head": _sha256_head(video), "width": info.width,
                   "height": info.height, "fps": round(info.fps, 3), "duration": round(info.duration, 3), "vcodec": info.vcodec,
                   "aspect_ratio": info.aspect_ratio, "has_audio": info.has_audio, "rotation": info.rotation},
        "provenance": {k: v for k, v in prov.items() if k != "tags"}, "summary": {},
        "audio": round_floats(audio), "global_layers": round_floats(template), "whole_video_vlm": whole,
        "shots": shot_records, "analysis": {"vlm": vlm_info, "ocr": ocr, "seconds": round(time.time() - t0, 1)},
        "assets": {"storyboard": "storyboard.jpg"},
        "platform_meta": platform_meta or {},
        "stems": {k: (Path(v).name if k in ("original", "vocals", "music") else v) for k, v in stem_info.items()},
        "per_clip_audio": per_clip_audio(str(video), [s.start for s in shots[1:]], info.duration) if info.has_audio else {},
    }
    if memory:
        from .memory import reapply

        n = reapply(breakdown)
        if n:
            LOG.info("re-applied %d earlier user correction(s) for this file", n)
    finalize(breakdown, out, engine=engine)
    LOG.info("done in %.1fs -> %s", time.time() - t0, out)
    return breakdown


DOUBT_CAP = 0.7


def _ai_or_live_doubt(label: str, facts: dict, prov: dict, vctx: dict) -> str | None:
    """On platform-transcoded files only two things settle AI vs live action: an AIGC label (AI) or a
    native-30fps camera signature (live). Without them, cap the confidence and say why.
    24fps-source pulldown alone is not decisive: AI video, stop motion and cinema cameras all produce it."""
    if label not in ("ai_video", "live_action") or not facts.get("transcode_degraded"):
        return None
    if prov.get("aigc"):
        return None
    if label == "live_action" and vctx.get("native30") and not facts.get("pulldown"):
        return None
    if label == "ai_video":
        return "实拍 / 定格还是 AI 待确认：没有 AIGC 标识；24fps 源痕迹在 AI、定格动画、电影机拍摄里都会出现"
    return "实拍还是 AI 待确认：画面源自 24fps 素材且没有 AIGC 标识，不是手机原生 30fps 的特征"


def _user_real(material: dict, real: bool) -> None:
    """Apply the user's answer to "does this shot use real (user-supplied) material?"."""
    if real and not material["has_real_material"]:
        material["items"].append({"kind": "user_marked", "kind_zh": "用户标注的真实素材", "region": "unknown", "real_material": True,
                                  "source": "user"})
    if not real:
        for it in material["items"]:
            if it.get("real_material"):
                it["real_material"] = False
                it["note"] = "用户纠正：不是真实素材"
    material["has_real_material"] = real
    if real and material["origin"] in ("ai_generated", "rendered", "unknown"):
        material["origin"], material["origin_zh"] = "mixed", "混合（含真实素材）"
    if not real and material["origin"] in ("real", "mixed"):
        material["origin"], material["origin_zh"] = "unknown", "待确认（用户说不是真实素材）"
    material["user_corrected"] = True


def visual_opinion(rec: dict) -> dict | None:
    """What a model saw in the shot: the VLM answer, an agent's review (rea-video apply-review), or both averaged."""
    v, a = rec.get("vlm"), rec.get("review")
    if not (v and a):
        return v or a
    merged = dict(v)
    merged["label_probs"] = {k: (v["label_probs"].get(k, 0.0) + a["label_probs"].get(k, 0.0)) / 2 for k in LABEL_ZH}
    merged["embedded_assets"] = (v.get("embedded_assets") or []) + (a.get("embedded_assets") or [])
    return merged


AI_LIVE_AXIS = ("ai_video", "live_action", "avatar")


def _vlm_gate(rec: dict, vprobs: dict | None, vctx: dict) -> dict | None:
    """When only one image reader answered (the VLM, or an agent review, not both), let it decide only where
    image readers have proved reliable. Measured on eval/articles + holdout + holdout2: they fix 3D vs
    code-render vs screen-capture confusions, but on the AI / live-action / avatar axis they are confidently
    wrong, where the tools have frame-rate and container evidence. With both readers, _majority arbitrates.
    Returns the probs to fuse, or None to ignore them."""
    if not vprobs or (rec.get("review") and rec.get("vlm")):
        return vprobs
    facts = rec["deterministic"]["facts"]
    ranked = sorted(rec["deterministic"]["probs"].items(), key=lambda kv: -kv[1])
    (d, dp), (_, sp) = ranked[0], ranked[1]
    v = top(vprobs)[0]
    # a photographic answer on a shot with a person in it (a big face, a dim shot with a face, or a face over
    # RULES["big_face"]["area"]) overrides texture cues that rank "rendered" first: plain walls, crushed shadows and
    # screenshots pasted over the person all look flat after a transcode
    # Only when the tools' top two include a rendered label: inside the AI / live / avatar axis the tools' frame-rate
    # evidence still decides (eval holdout2 dual_role_realcat: Gemini calls an AI presenter "avatar").
    rendered_top = d not in AI_LIVE_AXIS or ranked[1][0] not in AI_LIVE_AXIS
    person = (v in AI_LIVE_AXIS and rendered_top
              and bool(facts.get("big_face") or facts.get("lowlight") or facts.get("face_area", 0) >= RULES["big_face"]["area"]))
    if v not in (d, ranked[1][0]) and not person:  # it must back one of the tools' two best guesses
        return None
    # the tools' confidence only protects the AI / live axis (frame-rate evidence), not a "rendered" texture call
    if (d in AI_LIVE_AXIS or v in AI_LIVE_AXIS) and not (person and d not in AI_LIVE_AXIS):
        if dp >= 0.65 and dp - sp >= 0.25:  # tools are sure: frame-rate evidence beats "looks real / looks like a presenter"
            return None
    if v == "live_action" and facts.get("transcode_degraded") and not vctx.get("native30"):
        return None  # transcoded 24fps-source footage never becomes live action on looks alone
    return vprobs


def _majority(rec: dict) -> str | None:
    """The tools' top label when one of the two image readers (VLM, agent review) agrees with it and the other
    does not. Not the reverse: VLM and agent tend to misread the same picture the same way (measured on
    eval/articles.yaml and holdout2), so their agreement against the tools is left to the normal fusion."""
    v, a = ((rec.get(k) or {}).get("label_probs") for k in ("vlm", "review"))
    if not (v and a and sum(v.values()) > 0 and sum(a.values()) > 0):
        return None
    d, tv, ta = top(rec["deterministic"]["probs"])[0], top(v)[0], top(a)[0]
    return d if (tv == d) != (ta == d) else None


def finalize(breakdown: dict, out: Path, engine: str = "seedance2.0") -> dict:
    """(Re)classify every shot from stored features + VLM answers, plan replication,
    summarise and write breakdown.json / reports. Safe to call again after the
    classifier changes (no re-decoding)."""
    out = Path(out)
    shot_records = breakdown["shots"]
    audio = breakdown.get("audio", {})
    prov = breakdown.get("provenance", {})
    template = breakdown.get("global_layers", {})
    planner = Planner()
    vctx = video_context(shot_records, prov, breakdown["source"].get("fps"))
    breakdown["video_context"] = vctx
    for rec in shot_records:
        probs, evidence, facts = deterministic(rec["features"], rec.get("overlay"), rec.get("text"), rec.get("faces", 0.0), audio, prov,
                                               fps=breakdown["source"].get("fps"), ctx=vctx)
        rec["deterministic"] = {"probs": round_floats(probs), "evidence": evidence, "facts": facts}
        rec["layers"] = overlay_layers(rec.get("overlay"), rec.get("text"), template)
    # ---------------- fuse + plan ----------------------------------------------------
    video_meta = {"aspect_ratio": breakdown["source"]["aspect_ratio"]}
    for rec in shot_records:
        fam = rec["deterministic"]["facts"].get("temporal_family", "dynamic")
        seen = visual_opinion(rec) or {}
        vprobs = _vlm_gate(rec, seen.get("label_probs"), vctx)
        probs = fuse(rec["deterministic"]["probs"], vprobs, fam, majority=_majority(rec))
        label, conf = top(probs)
        src = seen.get("image_source")
        img_src = src if src and src != "unknown" else image_source_guess(rec["features"], rec["deterministic"]["facts"])
        evidence = (list(rec["deterministic"]["evidence"]) + [f"[VLM] {e}" for e in (rec.get("vlm") or {}).get("evidence", [])[:6]]
                    + [f"[{(rec.get('review') or {}).get('_by', 'agent')} 看图] {e}" for e in (rec.get("review") or {}).get("evidence", [])[:6]])
        decided = "fused" if rec.get("vlm") else ("fused_agent" if rec.get("review") else "deterministic")
        user = rec.get("user") or {}
        if user.get("label") in LABEL_ZH:  # the user said what this shot is: that is the answer
            label, conf, decided = user["label"], 1.0, "user"
            probs = {k: (1.0 if k == label else 0.0) for k in LABEL_ZH}
            evidence = [f"[用户纠正] 改为{LABEL_ZH[label]}" + (f"：{user['note']}" if user.get("note") else "")] + evidence
        rec["base"] = {"label": label, "label_zh": LABEL_ZH[label], "confidence": round(conf, 3), "probs": round_floats(probs),
                       "image_source": img_src if label in ("static_hold", "still_motion") else None,
                       "generator_guess": seen.get("generator_guess"),
                       "decided_by": decided, "evidence": evidence}
        doubt = None if decided == "user" else _ai_or_live_doubt(label, rec["deterministic"]["facts"], prov, vctx)
        if doubt:
            rec["base"]["confidence"] = round(min(conf, DOUBT_CAP), 3)
            rec["base"]["uncertain"] = doubt
        if label in ("code_render", "screen_capture"):
            # text/graphics are the rendered content itself here, not a separate overlay layer
            rec["layers"] = [l for l in rec["layers"] if l["kind"] == "layout_template"]
        # imported real material: full-frame from the base label, embedded regions only where
        # the frame is a rendered layout (photographic bases would trip the region detector)
        rendered_frame = label in ("code_render", "screen_capture", "cg_3d") or (label in ("static_hold", "still_motion") and img_src in ("graphic", "screenshot"))
        embedded = rec.get("embedded_assets", []) if rendered_frame else []
        if label == "cg_3d":  # fur/foliage in a 3D render looks photographic; only trust screenshot panels there
            embedded = [e for e in embedded if e["kind"] in ("screenshot", "screen_recording")]
        rec["material"] = material_summary(label, img_src if label in ("static_hold", "still_motion", "screen_capture") else None, embedded,
                                           seen or None, moving=fam != "frozen")
        if user.get("real_material") is not None:
            _user_real(rec["material"], bool(user["real_material"]))
        rec["verdict"] = verdict_text(label, img_src, rec["layers"])
        if rec["material"]["has_real_material"]:
            kinds = []
            for it in rec["material"]["items"]:
                if it.get("real_material") and it["kind_zh"] not in kinds:
                    kinds.append(it["kind_zh"])
            rec["verdict"] += "；引入真实素材（" + "、".join(kinds) + "）"
        rec["replication"] = planner.plan_shot(label, rec, video_meta, engine=engine)

    # ---------------- summary --------------------------------------------------------
    total = sum(r["duration"] for r in shot_records) or 1.0
    mix: dict[str, float] = {}
    for r in shot_records:
        mix[r["base"]["label"]] = mix.get(r["base"]["label"], 0.0) + r["duration"]
    mix_sorted = sorted(mix.items(), key=lambda kv: -kv[1])
    overlay_kinds = sorted({l["kind"] for r in shot_records for l in r["layers"]})
    conf = sum(r["base"]["confidence"] * r["duration"] for r in shot_records) / total
    parts = [f"{LABEL_ZH[k]} {v / total:.0%}" for k, v in mix_sorted]
    kind_zh = {"subtitle": "字幕", "title": "标题", "label": "文字标注", "static_graphic": "图形贴片", "layout_template": "版式模板"}
    verdict = ("混合制作：" if len(mix) > 1 or overlay_kinds else "") + " + ".join(parts)
    unsure = sum(r["duration"] for r in shot_records if r["base"]["label"] in ("ai_video", "live_action")
                 and r["base"]["confidence"] < 0.65
                 and (r.get("deterministic") or {}).get("facts", {}).get("transcode_degraded"))
    if unsure / total > 0.3 and not any(visual_opinion(r) for r in shot_records):
        verdict += f"（其中 {unsure / total:.0%} 时长实拍还是 AI 待确认：平台转码抹掉了噪声证据，需开 VLM 或人工看画面）"
    doubtful = sum(r["duration"] for r in shot_records if r["base"].get("uncertain"))
    if doubtful / total > 0.3 and not (unsure / total > 0.3 and not any(visual_opinion(r) for r in shot_records)):
        verdict += f"（其中 {doubtful / total:.0%} 时长实拍还是 AI 证据不足，待确认）"
    if overlay_kinds:
        verdict += "；叠加层：" + "、".join(kind_zh.get(k, k) for k in overlay_kinds) + "（代码/剪辑软件合成）"
    real_shots = [r for r in shot_records if r.get("material", {}).get("has_real_material")]
    real_kinds: dict[str, float] = {}
    for r in real_shots:
        for k in {it["kind_zh"] for it in r["material"]["items"] if it.get("real_material")}:
            real_kinds[k] = real_kinds.get(k, 0.0) + r["duration"]
    if real_shots:
        verdict += "；引入用户真实素材：" + "、".join(f"{k}（{v:.1f}s）" for k, v in sorted(real_kinds.items(), key=lambda kv: -kv[1]))
    if audio.get("beat_synced_edit"):
        verdict += "；剪辑卡在音乐节拍上"
    summary = {
        "duration": breakdown["source"]["duration"], "shots": len(shot_records), "verdict": verdict,
        "production_mix": [{"label": k, "label_zh": LABEL_ZH[k], "seconds": round(v, 2), "ratio": round(v / total, 3)} for k, v in mix_sorted],
        "overlay_kinds": overlay_kinds, "confidence": round(conf, 3),
        "transitions": {t: sum(1 for r in shot_records if r["transition_in"] == t) for t in sorted({r["transition_in"] for r in shot_records})},
        "avg_shot_s": round(total / max(1, len(shot_records)), 2),
        "real_material": {"shots": [r["index"] for r in real_shots], "seconds": round(sum(r["duration"] for r in real_shots), 2),
                          "ratio": round(sum(r["duration"] for r in real_shots) / total, 3),
                          "kinds": {k: round(v, 2) for k, v in real_kinds.items()}},
        "vlm": vlm_note(breakdown.get("analysis", {}).get("vlm") or {}, breakdown.get("analysis", {}).get("review")),
    }
    sb_items = []
    import cv2

    for r in shot_records:
        img = cv2.imread(str(out / r["assets"]["first_frame"]))
        if img is not None:
            sb_items.append((img, r["base"]["label"], f"#{r['index']} {r['base']['label']} {r['base']['confidence']:.0%}", r["start"], r["end"]))
    storyboard(sb_items, out / "storyboard.jpg")

    aspects.finalize(breakdown)
    breakdown["aspects"] = round_floats(breakdown["aspects"])
    summary["aspects"] = aspects.summary_lines(breakdown)
    for rec in shot_records:  # filmed shots: rebuild by shooting, with this shot's framing / camera / light
        if rec["base"]["label"] == "live_action":
            res = {k: v.get("result") for k, v in (rec.get("aspects") or {}).items() if v.get("result")}
            rec["replication"]["shoot"] = {"summary": aspects.shot_text(rec), **res}

    breakdown["audio_plan"] = merge_audio_with_vlm(audio, breakdown.get("whole_video_vlm"))
    srcs = classify_sound_sources(breakdown)
    breakdown["sound_sources"] = srcs
    for t in breakdown["audio_plan"].get("plan", []):
        if t["track"] == "music" and srcs.get("music"):
            lean = srcs["music"]["leaning"]
            t["source"] = srcs["music"]["source_zh"]
            t["how"] = {"ai_generated": "原曲倾向 AI 生成：用 Suno 等（capsule-cinema UniversalMusicGenerationTool）按风格/BPM/调性重新生成，MV 类连歌词一起按新内容写",
                        "existing_library": "原曲倾向现成曲库配乐：按 BPM/情绪/调性在曲库检索同类曲，或用 AI 生成相近风格的免版权曲",
                        "popular_song": "原曲倾向现成知名歌曲：直接使用有版权风险，需要授权或换成相近风格的曲库/AI 曲"}[lean]
        if t["track"] == "sfx" and srcs.get("sfx"):
            t["source"] = srcs["sfx"]["source_zh"]
            if srcs["sfx"]["leaning"] == "ai_native":
                t["how"] = "原片音效随 AI 视频片段一起生成：复刻时用支持原生音频的视频模型（Seedance 2.x / Veo 3.1 / Kling）直接出声，或视频生音效（HunyuanVideo-Foley）"
        if t["track"] == "voice" and srcs.get("voice"):
            t["source"] = srcs["voice"]["source_zh"]
    from .visual import audio_timeline

    if audio.get("has_audio"):
        audio_timeline(audio, breakdown["audio_plan"].get("sfx_merged", []), [r["start"] for r in shot_records[1:]],
                       breakdown["source"]["duration"], out / "audio.png")
        breakdown["assets"]["audio_timeline"] = "audio.png"
    a_parts = []
    if audio.get("voice", {}).get("present"):
        a_parts.append("人声")
    if audio.get("music", {}).get("present"):
        ms = srcs.get("music") or {}
        a_parts.append(f"BGM {audio['music'].get('tempo_bpm', '?')}BPM（{ms.get('source_zh', '来源待确认')}"
                       + (f"，倾向{MUSIC_ZH[ms['leaning']]}" if ms.get("source") == "unknown" else "") + "）")
    n_sfx = len(breakdown["audio_plan"].get("sfx_merged", []))
    if n_sfx:
        a_parts.append(f"音效 {n_sfx} 处")
    if a_parts:
        summary["verdict"] += "；声音：" + " + ".join(a_parts)
    breakdown["summary"] = summary
    write_json(out / "breakdown.json", breakdown)
    from .report import write_reports

    write_reports(breakdown, out)
    return breakdown
