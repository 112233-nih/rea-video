"""Production-method classification: deterministic forensics + optional VLM, fused.

Base layer labels (what produced the moving picture of the shot):
  live_action    实拍（摄像机/手机拍摄）
  ai_video       AI 生成视频（文生/图生/参考生视频）
  avatar         数字人口播（AI 驱动的说话人）
  cg_3d          3D 渲染（Blender/C4D/UE 等）
  code_render    代码/模板渲染（MG、文字、UI、图表、PPT 式卡片）
  screen_capture 屏幕录制/网页截图
  still_motion   静态图片 + 推拉摇移（Ken Burns 图片动效）
  static_hold    静帧停留（图片或卡片定格，无运动）
Overlays (composited on top): subtitle / title / label text, static graphics, layout template.

Deterministic evidence is decisive for *temporal* questions (is anything moving? is the
whole frame a rigid zoom of one still image? exact frozen pixels?) and for *rendered vs
photographic* texture. It is weak for live-action vs AI vs 3D, which the VLM decides;
fusion weights reflect that."""
from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

LABELS = ["live_action", "ai_video", "avatar", "cg_3d", "code_render", "screen_capture", "still_motion", "static_hold"]
LABEL_ZH = {
    "live_action": "实拍",
    "ai_video": "AI 生成视频",
    "avatar": "数字人口播",
    "cg_3d": "3D 渲染",
    "code_render": "代码/模板渲染",
    "screen_capture": "屏幕录制/截图",
    "still_motion": "静态图 + 推拉动效",
    "static_hold": "静帧停留",
}
IMAGE_SOURCE_ZH = {"ai_image": "AI 生成图", "photo": "真实照片", "graphic": "设计/渲染图形", "screenshot": "截图", "unknown": "未知"}
# how much to trust the deterministic layer per label (VLM weight is 1.0)
DET_WEIGHT = {"static_hold": 1.6, "still_motion": 1.4, "code_render": 1.0, "screen_capture": 0.6,
              "cg_3d": 0.5, "live_action": 0.6, "ai_video": 0.6, "avatar": 0.4}


# weight of the model's opinion (VLM or an agent's review) relative to the deterministic layer
# calibrated on the first real-Gemini run (eval/articles.yaml, 2026-10-30): 1.0 let a confident
# but wrong VLM override 24fps/transcode evidence on AI videos; 0.7 keeps its gains on 3D / UI shots
VLM_WEIGHT = 0.7


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _norm(d: dict[str, float]) -> dict[str, float]:
    s = sum(max(v, 0.0) for v in d.values()) or 1.0
    return {k: max(v, 0.0) / s for k, v in d.items()}


# Photographic-picture guards: platform transcodes make real footage look "rendered" (flat blocks, small palette).
# Each guard needs evidence a render cannot fake easily. Thresholds were measured over every shot of eval/*.yaml
# (labels, holdout, holdout2, articles, koubo); see eval/RESULTS.md 2026-10-30 for the distributions.
RULES = {
    # dim footage: near-black blocks that still carry grain (a rendered dark background is one exact colour,
    # block std 0), lit parts not flat, and a face on screen (dark UI / 3D on black have no face)
    "lowlight": {"dark_ratio": 0.15, "dark_std": 0.3, "dark_flat_max": 0.65, "flat_lit_max": 0.3, "faces": 0.5},
    # a face held through the shot: cartoon / plush / illustrated faces in eval cover < 6% of the frame
    "big_face": {"area": 0.06, "presence": 0.5, "graphic_factor": 0.4},
    # lines of small OCR text (glyph height < 2% of the frame): 3D shots carry <= 2, screenshots 5-14
    "ui_text": {"height": 0.02, "mid": 4.5, "width": 0.7},
    # an avatar is a fixed frame; a moving / shaking camera argues for someone filming
    "avatar_moving_camera": 0.3,
}


def lowlight_photo(f: dict, faces: float = 0.0) -> bool:
    """Dim footage with a person in it (see RULES["lowlight"]): crushed shadows shrink the palette and add flat
    blocks, so the "rendered texture" cues do not apply."""
    r = RULES["lowlight"]
    return (f.get("dark_ratio", 0.0) > r["dark_ratio"] and f.get("dark_std", 0.0) > r["dark_std"]
            and f.get("dark_flat", 1.0) < r["dark_flat_max"] and f.get("flat_lit", 1.0) < r["flat_lit_max"] and faces >= r["faces"])


PLATFORM_TRANSCODES = ("WeChat transcode", "WeChat Channels", "Douyin/TikTok", "Douyin", "Kuaishou", "Xiaohongshu")


def video_context(shots: list[dict], provenance: dict | None, fps: float | None) -> dict:
    """Whole-video facts the per-shot classifier needs.
    - transcoded: the file went through a platform re-encode (WeChat / Douyin / TikTok ...). Heavy
      compression wipes sensor noise, so "no noise" is no longer evidence for AI.
    - native30: in a 30 fps file, photographic shots carry no duplicated frames (no 24->30 pulldown).
      Phones record 30 fps natively; video generators mostly emit 24 fps."""
    plat = [h for h in ((provenance or {}).get("hints", {}).get("platform") or []) if h in PLATFORM_TRANSCODES]
    photo_dur = native_dur = 0.0
    for r in shots:
        f = r.get("features") or {}
        photo_like = (f.get("palette95", 0) >= 100 and f.get("flat_ratio", 1) <= 0.2) or lowlight_photo(f, r.get("faces", 0.0))
        if not photo_like or f.get("dup_ratio", 0) > 0.85:
            continue  # rendered graphics or frozen frames say nothing about camera cadence
        d = float(r.get("duration", 0))
        photo_dur += d
        if f.get("dup_ratio", 1) < 0.02 and f.get("sharp_period_ac", 0) < 0.45:
            native_dur += d
    native30 = bool(fps and 29 <= fps <= 31 and photo_dur >= 3 and native_dur / photo_dur >= 0.7)
    return {"transcoded": bool(plat), "platform": plat, "native30": native30,
            "native30_share": round(native_dur / photo_dur, 3) if photo_dur else None}


def deterministic(f: dict, overlay: dict | None = None, text: dict | None = None, faces: float = 0.0,
                  audio: dict | None = None, provenance: dict | None = None, fps: float | None = None,
                  ctx: dict | None = None) -> tuple[dict[str, float], list[str], dict]:
    """Return (probabilities, evidence lines in Chinese, derived facts)."""
    ctx = ctx or {}
    ev: list[str] = []
    facts: dict = {}
    noise, tnoise = f.get("noise_sigma", 0), f.get("temporal_noise", 0)
    flat, pal, axis = f.get("flat_ratio", 0), f.get("palette95", 999), f.get("axis_edge_ratio", 0)
    dup, per, reg = f.get("dup_ratio", 0), f.get("dup_period"), f.get("dup_regularity", 0)
    moving = f.get("moving_pair_ratio", 0)
    kb = f.get("kenburns_score", 0)
    zero = f.get("zero_pixel_ratio", 0)
    text_cov = (text or {}).get("text_coverage", 0.0)

    # ---- temporal family ---------------------------------------------------------
    frozen = dup > 0.85 and f.get("pair_mad_p90", 0) < 0.6
    sparse_anim = dup > 0.6 and moving > 0.01
    if frozen and not sparse_anim and f.get("start_end_mad", 0) < 1.0:
        family = "frozen"
        ev.append(f"画面几乎完全静止：{dup:.0%} 的相邻帧逐像素重复，首尾帧差 {f.get('start_end_mad', 0):.2f}")
    elif kb > 0.45:
        family = "planar"
        ev.append(f"整帧按单一刚体变换运动（拟合内点 {f.get('rigid_inlier_ratio', 0):.0%}，残差 {f.get('rigid_residual', 0):.2f}px，无局部运动）"
                  f"→ 静态图推拉：缩放 {f.get('zoom_per_s', 0) * 100:+.1f}%/s，平移 {f.get('pan_px_per_s', 0):.0f}px/s")
    elif dup > 0.5:
        family = "mostly_static"
        ev.append(f"{dup:.0%} 的相邻帧完全重复，只有局部元素在动（典型的模板/MG 动画或定格后局部动效）")
    else:
        family = "dynamic"
    facts["temporal_family"] = family

    # ---- texture: rendered graphic vs photographic ------------------------------
    # flat colour blocks and a small palette are the most robust "rendered" cues; the
    # noise estimate is unreliable on text-heavy frames (glyph edges look like noise).
    graphic = (0.45 * _sig((flat - 0.20) / 0.05) + 0.35 * _sig((70 - pal) / 15) + 0.20 * _sig((axis - 0.33) / 0.03))
    lowlight = lowlight_photo(f, faces)
    facts["lowlight"] = lowlight
    if lowlight:
        # judge texture on the lit part only; the palette is small because the shadows are crushed
        lit_graphic = 0.75 * _sig((f.get("flat_lit", 0) - 0.20) / 0.05) + 0.25 * _sig((axis - 0.33) / 0.03)
        if lit_graphic < graphic:
            ev.append(f"低照度画面：{f.get('dark_ratio', 0):.0%} 的区域接近全黑但带颗粒（不是纯色底），亮部平坦块只有 {f.get('flat_lit', 0):.0%}、"
                      f"且有人脸：平坦块/少色是暗部被压缩造成的，不按渲染特征算")
            graphic = lit_graphic
    # a large face held through the shot is a photographic picture: plain walls and dark clothes make flat blocks
    # after a platform transcode (RULES["big_face"])
    bf = RULES["big_face"]
    big_face = faces >= bf["presence"] and f.get("face_area", 0.0) >= bf["area"] and family in ("dynamic", "mostly_static")
    facts["big_face"] = big_face
    facts["face_area"] = round(f.get("face_area", 0.0), 3) if faces >= 0.3 else 0.0
    if big_face and graphic > 0.25:
        ev.append(f"人脸占画面 {f.get('face_area', 0):.0%}、{faces:.0%} 的采样帧都有：是人物近景画面，平坦色块多来自墙面/衣服，渲染特征降权")
        graphic *= bf["graphic_factor"]
    # (overlay text is a separate layer, so OCR coverage does not count toward the base)
    photo = 1.0 - graphic
    # fine high-frequency detail on flat ground = small text / UI (screenshots, docs, code cards)
    text_like = _sig((noise - 0.8) / 0.2) * _sig((flat - 0.4) / 0.08)
    # many lines of small OCR text = an interface / document screenshot; the noise cue above misses them on
    # platform transcodes, which smooth glyph edges (RULES["ui_text"])
    ut = RULES["ui_text"]
    small_lines = sum(1 for tr in (text or {}).get("tracks", []) if tr.get("text_height", 1.0) < ut["height"])
    ui_text = _sig((small_lines - ut["mid"]) / ut["width"])
    facts["ui_text"] = round(ui_text, 3)
    if ui_text > 0.5:
        ev.append(f"OCR 读到 {small_lines} 行小字号文字：画面里有界面/文档截图")
    # background pixels exactly unchanged while something moves = 2D compositing (code/MG)
    rsb = _sig((zero - 0.94) / 0.015) * _sig((0.1 - tnoise) / 0.03)
    facts["graphic_score"] = round(graphic, 3)
    facts["text_like"] = round(text_like, 3)
    facts["static_composite"] = round(rsb, 3)
    if graphic > 0.6:
        ev.append(f"纹理呈渲染特征：平坦色块 {flat:.0%}、95% 像素仅用 {pal} 种颜色、横竖直边 {axis:.0%}")
    elif graphic < 0.35:
        ev.append(f"纹理呈照片/视频特征：噪声 σ={noise:.2f}、用色 {pal} 种、平坦块 {flat:.0%}")
    if text_like > 0.6:
        ev.append("平坦底色上有大量细小高频细节（小字号文字/界面），像截图或文档画面")
    if rsb > 0.6 and family != "frozen":
        ev.append(f"背景 {zero:.0%} 像素逐帧完全不变、只有局部元素在动：2D 合成/代码动效特征")

    # ---- frame-rate fingerprints -------------------------------------------------
    pulldown = None
    if 0.12 <= dup <= 0.30 and per in (4, 5, 6) and reg > 0.25:
        pulldown = "24→30"
    elif 0.50 <= dup <= 0.66 and per in (1, 2, 3):
        pulldown = "24→60"
    elif 0.03 <= dup <= 0.10 and per in (5, 6, 7) and reg > 0.25:
        pulldown = "25→30?"
    # platform re-encodes alternate frame quality with their GOP (I/P/B) pattern, which mimics the
    # sharpness rhythm of frame interpolation: only trust this cue on first-generation files
    resampled = (f.get("sharp_alt", 0) > 0.025 or f.get("sharp_period_ac", 0) > 0.45) and not ctx.get("transcoded")
    if resampled and not pulldown and family == "dynamic":
        pulldown = "interpolated"
        ev.append(f"逐帧清晰度呈周期性起伏（周期 {f.get('sharp_period')} 帧，相关 {f.get('sharp_period_ac', 0):.2f}）：画面经过插帧/帧率转换，原始帧率更低")
    facts["pulldown"] = pulldown
    if pulldown:
        ev.append(f"重复帧呈 {pulldown} 规律（{dup:.0%}，周期 {per}）：原始素材为 24/25fps 后转码，常见于 AI 生成视频、3D/动画渲染或电影素材")

    live_hint = 0.0
    live_hint += 1.2 * _sig((noise - 0.9) / 0.2)
    live_hint += 0.6 * _sig((tnoise - 0.8) / 0.15)
    live_hint += 0.3 * _sig((0.5 - zero) / 0.1)
    live_hint += 0.6 * (1.0 if f.get("camera") == "handheld_like" else 0.0)
    live_hint += 0.2 * (1.0 if dup < 0.03 else 0.0)
    live_hint -= 0.9 * (1.0 if pulldown else 0.0)
    # generators emit 24/25/30 fps; a native >=48 fps stream with no duplicated frames was
    # most likely captured by a phone/camera (or screen/game capture)
    native_hfr = bool(fps and fps >= 47 and dup < 0.05 and not pulldown)
    if native_hfr:
        live_hint += 1.0
        ev.append(f"原生 {fps:.0f}fps 且几乎无重复帧：生成模型通常输出 24/30fps，高帧率原生素材多为手机/相机拍摄")
    facts["native_hfr"] = native_hfr
    cam_tags = bool((provenance or {}).get("hints", {}).get("camera"))
    if cam_tags:
        live_hint += 1.0
        ev.append("容器元数据含拍摄设备信息（make/model/location）")
    if ctx.get("native30") and dup < 0.02 and not pulldown:
        live_hint += 0.9
        ev.append(f"整片是原生 30fps、实拍类镜头没有 24fps 源的重复帧（{ctx.get('native30_share', 0):.0%} 时长）：手机直出常见，生成模型多输出 24fps")
    live_hint = min(1.0, max(0.0, live_hint / 2.9))
    gen_tags0 = bool((provenance or {}).get("hints", {}).get("generator"))
    degraded = bool(ctx.get("transcoded") and noise < 0.9)
    facts["transcode_degraded"] = degraded
    if degraded and not pulldown and not gen_tags0 and live_hint < 0.45:
        # compression erased the sensor noise: its absence no longer argues for AI
        live_hint = 0.45
        ev.append("经过平台二次转码（" + "、".join(ctx.get("platform", [])) + "），传感器噪声被压没了：噪声类证据失效，实拍还是 AI 主要看帧率和画面内容")
    if live_hint > 0.6:
        ev.append(f"时域噪声 {tnoise:.2f}、逐像素不变比例 {zero:.0%}：存在传感器噪声，符合实拍")
    gen_tags = bool((provenance or {}).get("hints", {}).get("generator"))
    if gen_tags:
        ev.append("容器元数据包含生成式工具/AIGC 标识：" + ", ".join(provenance["hints"]["generator"][:3]))

    speech = bool((audio or {}).get("speech_likely"))
    if faces > 0.6 and family in ("dynamic", "mostly_static") and f.get("camera") in ("static", "static_with_subject_motion"):
        ev.append(f"{faces:.0%} 的采样帧有正脸且机位固定" + ("，音轨有人声" if speech else ""))

    # ---- assemble scores ---------------------------------------------------------
    s = {k: 0.02 for k in LABELS}
    if family == "frozen":
        # a frozen *rendered* card is code/template output; a frozen photo/AI picture is a still hold
        screenshot_like = graphic > 0.45 and axis > 0.42 and text_cov > 0.05
        s["static_hold"] += 3.0 * (1 - graphic)
        s["code_render"] += 3.0 * graphic * (0.5 if screenshot_like else 1.0)
        s["screen_capture"] += 3.0 * graphic * (0.6 if screenshot_like else 0.1)
    elif family == "planar":
        s["still_motion"] += 3.0 * kb * (0.35 if pulldown else 1.0)
        if pulldown:
            ev.append("但运动带有 24fps 源的帧率转换痕迹：更像生成视频里的缓慢推镜，而不是剪辑软件对静图做推拉")
            s["ai_video"] += 1.2
        s["screen_capture"] += 0.5 * graphic * (1 if abs(f.get("zoom_per_s", 0)) < 0.003 else 0.2)  # pure scroll
        s["ai_video"] += 0.3
    elif family == "mostly_static":
        s["code_render"] += 1.6 * graphic * (1 + 0.5 * rsb) * (1 - 0.5 * text_like * _sig((0.12 - f.get("saturation", 0.3)) / 0.04))
        s["screen_capture"] += 1.5 * graphic * text_like * (0.3 + _sig((0.12 - f.get("saturation", 0.3)) / 0.04))
        s["static_hold"] += 0.5 * photo
        s["cg_3d"] += 0.4 * graphic * (1 - text_like)
        s["ai_video"] += 0.8 * photo * (1.4 if pulldown else 1.0)
        s["live_action"] += 0.3 * photo * live_hint
    else:
        axis_f = 1.2 if axis > 0.38 else 0.8
        # near-grayscale frames built from a handful of colours are UI / documents (white pages, grey
        # cards, dark editors), not shaded 3D renders: shading spreads colours, product 3D is coloured
        ui_gray = _sig((0.04 - f.get("saturation", 0.3)) / 0.01) * _sig((20 - pal) / 3)
        facts["ui_gray"] = round(ui_gray, 3)
        s["code_render"] += 1.5 * graphic * (0.3 + 1.2 * rsb + 1.0 * text_like + 0.8 * ui_gray) * axis_f
        s["cg_3d"] += 1.5 * graphic * max(0.0, 1.1 - rsb - 0.8 * text_like) * (1 - 0.8 * ui_gray) * (1 - 0.8 * ui_text)
        s["screen_capture"] += 1.2 * graphic * ui_text
        if ui_gray > 0.5 and graphic > 0.6:
            ev.append(f"画面几乎无彩色（饱和度 {f.get('saturation', 0):.2f}）、只用 {pal} 种颜色：界面/文档类平面画面，不是有光影的 3D 渲染")
        s["screen_capture"] += 1.2 * graphic * text_like * (0.3 + _sig((0.12 - f.get("saturation", 0.3)) / 0.04))
        s["live_action"] += 1.5 * photo * live_hint
        s["ai_video"] += 1.5 * photo * (1 - live_hint) * (1.4 if pulldown else 1.0)
        # a talking-head avatar is a fixed frame; a shaking handheld camera argues for a person filming
        moving_cam = RULES["avatar_moving_camera"] if f.get("camera") in ("handheld_like", "smooth_move") else 1.0
        s["avatar"] += 0.6 * photo * (1 if faces > 0.6 else 0.1) * (1.0 if speech else 0.4) * (1 - live_hint) * moving_cam
        if pulldown:
            s["cg_3d"] *= 1.2
    if gen_tags:
        s["ai_video"] += 1.0
        s["avatar"] += 0.3
    probs = _norm(s)
    facts["live_hint"] = round(live_hint, 3)
    return probs, ev, facts


def image_source_guess(f: dict, facts: dict) -> str:
    if facts.get("graphic_score", 0) > 0.6:
        return "graphic"
    if f.get("axis_edge_ratio", 0) > 0.45 and f.get("noise_sigma", 1) < 0.2:
        return "screenshot"
    return "unknown"  # photo vs AI image needs the VLM


# Labels that answer the same first question ("how was the picture made?") and differ only in subtype.
# Fusion decides the group first, then the subtype inside it: when the tools say "AI video" and an agent says
# "digital avatar", both are votes for "generated" and must not be outvoted by a third source's "live action".
GROUPS = {"ai_video": "generated", "avatar": "generated", "cg_3d": "rendered", "code_render": "rendered"}


def group_of(label: str) -> str:
    return GROUPS.get(label, label)


def _group_sum(d: dict[str, float]) -> dict[str, float]:
    g: dict[str, float] = {}
    for k, v in d.items():
        g[group_of(k)] = g.get(group_of(k), 0.0) + v
    return g


def _log_pool(det_p: float, vlm_p: float, det_w: float) -> float:
    return math.exp(det_w * 0.6 * math.log(det_p + 1e-4) + VLM_WEIGHT * math.log(vlm_p + 1e-3))


def fuse(det: dict[str, float], vlm: dict[str, float] | None, family: str, majority: str | None = None) -> dict[str, float]:
    """Two stages: which group made the picture (generated / filmed / rendered / screen / still), then which subtype.
    majority: the tools' top label when one of the two image readers backs its group (see pipeline._majority)."""
    if not vlm:
        return det
    vlm = _norm({k: float(vlm.get(k, 0.0)) for k in LABELS})
    # hard temporal constraints: a frozen shot cannot be a generated *video*
    mult = {k: 1.0 for k in LABELS}
    if family == "frozen":
        for k in ("ai_video", "live_action", "avatar", "cg_3d", "still_motion"):
            mult[k] = 0.05
    if family == "planar":
        for k in ("live_action", "avatar"):
            mult[k] = 0.2
    sub = {k: _log_pool(det.get(k, 0.0), vlm.get(k, 0.0), DET_WEIGHT[k]) * mult[k] for k in LABELS}
    raw = {k: _log_pool(det.get(k, 0.0), vlm.get(k, 0.0), DET_WEIGHT[k]) for k in LABELS}
    dg, vg = _group_sum(det), _group_sum(vlm)
    members: dict[str, list[str]] = {}
    for k in LABELS:
        members.setdefault(group_of(k), []).append(k)
    out: dict[str, float] = {}
    for g, ks in members.items():
        # the group trusts the tools as much as their favourite member; constraints scale it like its members
        w = DET_WEIGHT[max(ks, key=lambda k: det.get(k, 0.0))]
        cons = sum(sub[k] for k in ks) / (sum(raw[k] for k in ks) or 1e-12)
        gs = _log_pool(dg.get(g, 0.0), vg.get(g, 0.0), w) * cons
        inner = sum(sub[k] for k in ks) or 1e-12
        for k in ks:
            out[k] = gs * sub[k] / inner
    # the answer must be one that some source actually backs (top-2 of the deterministic layer
    # or of the model, or a member of the group either one ranks first), not a compromise nobody voted for
    backed = {k for src in (det, vlm) for k, _ in sorted(src.items(), key=lambda kv: -kv[1])[:2]}
    backed |= {k for src in (dg, vg) for k in members[max(src, key=src.get)] if det.get(k, 0.0) + vlm.get(k, 0.0) > 0.05}
    best = max(out, key=out.get)
    if best not in backed:
        for k in out:
            if k not in backed:
                out[k] *= 0.1
    # two of three sources agree on the group: one overconfident model must not outvote them (hard constraints above still apply)
    if majority and out[majority] > 0:
        if max(out, key=out.get) != majority:
            for k in out:
                if k != majority:
                    out[k] = min(out[k], out[majority] * 0.8)
        # and as a group: "AI video" + "avatar" together must not outvote it either
        gm = group_of(majority)
        cap = 0.8 * sum(v for k, v in out.items() if group_of(k) == gm)
        for g, ks in members.items():
            tot = sum(out[k] for k in ks)
            if g != gm and tot > cap:
                for k in ks:
                    out[k] *= cap / tot
    return _norm(out)


def top(probs: dict[str, float]) -> tuple[str, float]:
    k = max(probs, key=probs.get)
    return k, probs[k]


def decide(probs: dict[str, float]) -> tuple[str, float]:
    """Final call, two stages: the group with the most probability (how the picture was made), then its best
    subtype. Confidence is the group's probability: "AI video 35% / avatar 26% / live 38%" is a 61% call
    that the picture was generated."""
    g: dict[str, float] = {}
    for k, p in probs.items():
        g[group_of(k)] = g.get(group_of(k), 0.0) + p
    best = max(g, key=g.get)
    k = max((x for x in probs if group_of(x) == best), key=probs.get)
    return k, g[best]


def overlay_layers(overlay: dict | None, text: dict | None, template: dict | None) -> list[dict]:
    layers: list[dict] = []
    for tr in (text or {}).get("tracks", []):
        if not tr.get("axis_aligned", True) and tr["kind"] != "subtitle":
            continue  # tilted text is probably part of the scene
        layers.append({"kind": tr["kind"], "source": "code_or_editor", "band": tr["band"], "texts": tr["texts"],
                       "changing": tr["changing"], "y_center": tr.get("y_center"), "drift": tr.get("drift")})
    for b in (overlay or {}).get("boxes", []):
        if any(l["band"] == b["band"] and l["kind"] in ("subtitle", "title", "label") for l in layers):
            continue
        layers.append({"kind": "static_graphic", "source": "code_or_editor", "band": b["band"], "bbox": b["bbox"]})
    if template and template.get("frozen_edge_ratio", 0) > 0.25 and template.get("boxes"):
        layers.append({"kind": "layout_template", "source": "code_or_editor", "band": "frame",
                       "coverage": round(template.get("frozen_ratio", 0), 3)})
    return layers


# bases whose picture can carry printed words (packaging, signs, screens in the scene) that OCR also reads
# (a still image is often a screenshot or a poster whose words are its content: left alone)
SCENE_TEXT_BASES = ("live_action", "ai_video", "avatar", "cg_3d")
SCENE_TEXT_DRIFT = 0.04  # the same words moved more than 4% of the frame: they sit on an object


def _same_words(ocr: str, overlay: str) -> bool:
    a, b = (re.sub(r"\s+", "", x).lower() for x in (ocr, overlay))
    if len(a) < 2 or not b:
        return False
    if a in b or b in a:
        return True
    m = SequenceMatcher(None, a, b, autojunk=False)
    blocks = m.get_matching_blocks()
    # a contiguous run of the same characters, not letters scattered across the line ("Aon" in "GAME CHANGER")
    return max(blk.size for blk in blocks) >= max(2, len(a) // 2) and sum(blk.size for blk in blocks) / len(a) >= 0.6


def drop_scene_text(layers: list[dict], label: str, vlm_overlay: list | None) -> tuple[list[dict], list[str]]:
    """Remove OCR text that belongs to the scene (logos and labels on products, signs) from the overlay layers.
    vlm_overlay: the image reader's list of post-production text (None when it did not answer). It is asked to
    leave out words printed on objects, so OCR tracks it does not mention are scene text.
    Without it, only text that moves with the picture is dropped."""
    if label not in SCENE_TEXT_BASES:
        return layers, []
    keep, dropped = [], []
    for l in layers:
        if not l.get("texts"):
            keep.append(l)
            continue
        if isinstance(vlm_overlay, list):
            scene = not any(_same_words(t, str(o)) for t in l["texts"] for o in vlm_overlay)
        else:
            scene = (l.get("drift") or 0.0) > SCENE_TEXT_DRIFT
        (dropped if scene else keep).append(l)
    return keep, [t for l in dropped for t in l["texts"][:3]]


def verdict_text(label: str, image_source: str | None, layers: list[dict]) -> str:
    base = LABEL_ZH[label]
    if label in ("static_hold", "still_motion") and image_source and image_source != "unknown":
        base += f"（{IMAGE_SOURCE_ZH.get(image_source, image_source)}）"
    kinds = []
    for l in layers:
        name = {"subtitle": "字幕", "title": "标题", "label": "文字标注", "static_graphic": "图形贴片", "layout_template": "版式模板"}.get(l["kind"], l["kind"])
        if name not in kinds:
            kinds.append(name)
    if kinds:
        return f"{base} + 代码/剪辑叠加（{'、'.join(kinds)}）"
    return base
