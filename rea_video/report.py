"""Human-readable reports (Markdown + standalone HTML) from a breakdown. Language: see i18n (breakdown["lang"])."""
from __future__ import annotations

import html
from pathlib import Path

from . import aspects
from .classify import LABEL_ZH
from .i18n import LangDict, T, join, lang, semi
from .util import fmt_time
from .visual import LABEL_COLOR
from .production import report_md as production_md
from .vlm import vlm_note

KIND_ZH = LangDict({"subtitle": "字幕", "title": "标题", "label": "文字", "static_graphic": "图形贴片", "layout_template": "版式模板",
                    "huazi": "花字", "animated_sticker": "动态贴纸/GIF", "static_sticker": "静态贴纸", "pip": "画中画"},
                   en={"subtitle": "subtitle", "title": "title", "label": "text", "static_graphic": "graphic", "layout_template": "layout template",
                       "huazi": "styled text", "animated_sticker": "animated sticker / GIF", "static_sticker": "static sticker", "pip": "picture-in-picture"})
CAMERA_ZH = LangDict({"static": "固定", "static_with_subject_motion": "固定（主体在动）", "planar_zoom_pan": "整图推拉/平移",
                      "handheld_like": "手持晃动", "smooth_move": "平滑运镜", "unknown": "—"},
                     en={"static": "static", "static_with_subject_motion": "static (subject moves)", "planar_zoom_pan": "whole-frame zoom / pan",
                         "handheld_like": "handheld shake", "smooth_move": "smooth move", "unknown": "—"})
ANIM_ZH = LangDict({"cut": "直接出现", "pop": "弹出", "fade": "淡入", "slide": "滑入", "reveal": "逐字", "cut_out": "直接消失",
                    "pop_out": "缩小消失", "fade_out": "淡出", "slide_out": "滑出"},
                   en={"cut": "hard in", "pop": "pop", "fade": "fade in", "slide": "slide in", "reveal": "type-on", "cut_out": "hard out",
                       "pop_out": "shrink out", "fade_out": "fade out", "slide_out": "slide out"})
GENDER_ZH = LangDict({"male": "偏男声", "female": "偏女声"}, en={"male": "male-leaning", "female": "female-leaning"})
CONF_ZH = LangDict({"medium": "中", "low": "低", "vlm": "VLM 看到"}, en={"medium": "medium", "low": "low", "vlm": "seen by VLM"})
MODE_ZH = LangDict({"music": "背景音乐", "voice": "人声", "sfx": "音效"}, en={"music": "Music", "voice": "Voice", "sfx": "Sound effects"})


def _layers(rec: dict) -> str:
    ks = []
    for l in rec.get("layers", []):
        k = KIND_ZH.get(l["kind"], l["kind"])
        if k not in ks:
            ks.append(k)
    return join(ks) or "—"


def _origin(rec: dict) -> str:
    m = rec.get("material") or {}
    if not m:
        return "—"
    kinds = []
    for it in m.get("items", []):
        if it.get("real_material") and it["kind_zh"] not in kinds:
            kinds.append(it["kind_zh"])
    unsure = sorted({it["kind_zh"] for it in m.get("items", []) if it.get("real_material") is None})
    refs = list(dict.fromkeys(x["kind_zh"] for x in m.get("references", [])))
    colon = T("：", ": ")
    return (m.get("origin_zh", "—") + (f"{colon}{join(kinds)}" if kinds else "")
            + (semi() + T("待确认是否真实：", "real or not, to confirm: ") + join(unsure) if unsure else "")
            + (semi() + T("复刻参考：", "references for the remake: ") + join(refs) if refs else ""))


def _decided_by(b: dict) -> str:
    if b["analysis"]["vlm"].get("used"):
        return T("确定性取证 + VLM 融合", "deterministic forensics + VLM")
    if (b["analysis"].get("review") or {}).get("shots"):
        return T("确定性取证 + agent 看图融合", "deterministic forensics + agent image review")
    return T("仅确定性取证", "deterministic forensics only")


def markdown(b: dict) -> str:
    s = b["summary"]
    src = b["source"]
    L = [T("# 视频拆解报告：{f}", "# Video breakdown: {f}", f=src["filename"]), "",
         T("**结论**：{v}", "**Verdict**: {v}", v=s["verdict"]), "",
         T("- 时长 {d}s · {w}×{h} · {fps}fps · {ar}", "- {d}s · {w}×{h} · {fps}fps · {ar}",
           d=src["duration"], w=src["width"], h=src["height"], fps=src["fps"], ar=src["aspect_ratio"]),
         T("- 镜头 {n} 个，平均 {avg}s；转场：", "- {n} shots, {avg}s average; transitions: ", n=s["shots"], avg=s["avg_shot_s"])
         + join(f"{k}×{v}" for k, v in s["transitions"].items()),
         T("- 判断置信度（按时长加权）：{c:.0%}；判定方式：{by}", "- Confidence (duration-weighted): {c:.0%}; decided by: {by}", c=s["confidence"], by=_decided_by(b)),
         f"- {s.get('vlm') or vlm_note(b['analysis']['vlm'], b['analysis'].get('review'))}",
         "", "![storyboard](storyboard.jpg)", "", T("## 制作方式占比", "## Production mix"), "",
         T("| 底层画面 | 时长 | 占比 |", "| Base picture | Duration | Share |"), "|---|---|---|"]
    for m in s["production_mix"]:
        L.append(f"| {m['label_zh']} | {m['seconds']}s | {m['ratio']:.0%} |")
    L += production_md(b.get("production_plan") or {})
    L += aspects.report_md(b)
    prov = b.get("provenance", {})
    if prov.get("hints") or prov.get("encoder"):
        L += ["", T("## 元数据线索", "## Metadata clues"), ""]
        if prov.get("encoder"):
            L.append(T("- 编码器：`{e}`", "- Encoder: `{e}`", e=prov["encoder"]))
        for k, v in (prov.get("hints") or {}).items():
            L.append(f"- {k}{T('：', ': ')}{', '.join(v)}")
    a = b.get("audio", {})
    if a.get("has_audio"):
        L += ["", T("## 声音拆解", "## Audio breakdown"), "", "![audio](audio.png)", "",
              T("- 整体响度 {r} dBFS，峰值 {p} dBFS", "- Loudness {r} dBFS, peak {p} dBFS", r=a.get("rms_dbfs"), p=a.get("peak_dbfs"))]
        v = a.get("voice", {})
        L.append(T("- **人声**：{p}，占 {r:.0%} 时长", "- **Voice**: {p}, {r:.0%} of the duration",
                   p=(T("有", "present") if v.get("present") else T("无/很少", "none / very little")), r=v.get("ratio", 0))
                 + (T("，基频约 {f}Hz（{g}）", ", pitch about {f}Hz ({g})", f=v.get("f0_median_hz"),
                      g=GENDER_ZH.get(v.get("gender_hint"), T("性别不确定", "gender unclear"))) if v.get("f0_median_hz") else ""))
        mu = a.get("music", {})
        if mu.get("present"):
            key = mu.get("key") or {}
            L.append(T("- **背景音乐**：占 {r:.0%} 时长（{s}–{e}s），约 {bpm} BPM（也可能是 {alt}）",
                       "- **Music**: {r:.0%} of the duration ({s}–{e}s), about {bpm} BPM (or {alt})",
                       r=mu.get("ratio", 0), s=mu.get("start"), e=mu.get("end"), bpm=mu.get("tempo_bpm"),
                       alt="/".join(str(t) for t in mu.get("tempo_alternatives", [])))
                     + (T("，调性 {t} {m}（置信 {c}）", ", key {t} {m} (confidence {c})", t=key.get("tonic"), m=key.get("mode"), c=key.get("confidence")) if key else "")
                     + (T("，段落变化点 {p}", ", section changes at {p}", p=", ".join(str(t) + "s" for t in mu.get("section_changes", [])[:8])) if mu.get("section_changes") else ""))
        else:
            L.append(T("- **背景音乐**：未检测到持续的音乐床", "- **Music**: no sustained music bed detected"))
        if "cuts_on_beat_ratio" in a:
            L.append(T("- **剪辑与节拍**：{r:.0%} 的切点落在节拍上（随机约 {c:.0%}）", "- **Cuts vs beat**: {r:.0%} of cuts land on a beat (chance: about {c:.0%})",
                       r=a["cuts_on_beat_ratio"], c=a["cuts_on_beat_chance"]) + (T("→ **卡点剪辑**", " → **beat-synced edit**") if a.get("beat_synced_edit") else ""))
        src = b.get("sound_sources") or {}
        if src:
            L += ["", T("**声音来源：AI 生成还是现成素材**", "**Sound sources: AI-generated or existing material**"), ""]
            if src.get("summary_zh"):
                L.append(T("- **结论**：{s}", "- **Verdict**: {s}", s=src["summary_zh"]))
            st = b.get("stems") or {}
            if st.get("ok"):
                L.append(T("- **剥离出的音轨**：`stems/original.wav`（原声）、`stems/vocals.wav`（人声干声，瞬态音效也会落在这里）、`stems/music.wav`（配乐 + 环境声）",
                           "- **Separated stems**: `stems/original.wav` (original), `stems/vocals.wav` (dry voice; transient sound effects land here too), `stems/music.wav` (music + ambience)"))
            ctx = src.get("format_context", {})
            fmt_name = (T("音乐 MV", "music video") if ctx.get("music_mv") else
                        (T("疑似带唱歌曲/MV", "possibly a sung song / music video") if ctx.get("music_mv_maybe") else (ctx.get("format") or T("普通视频配乐", "regular video with music"))))
            L.append(T("- 视频形式：{f}；AI 画面占 {ai:.0%}，实拍占 {live:.0%}", "- Format: {f}; AI picture {ai:.0%}, live footage {live:.0%}",
                       f=fmt_name, ai=ctx.get("ai_picture_share", 0), live=ctx.get("live_picture_share", 0)))
            for k in ("music", "voice", "sfx"):
                v = src.get(k)
                if not v:
                    continue
                probs = T("，", ", ").join(f"{kk} {vv:.0%}" for kk, vv in sorted(v["probs"].items(), key=lambda kv: -kv[1]))
                L.append(f"- **{MODE_ZH[k]}**{T('：', ': ')}{v['source_zh']}（{probs}）" if lang() == "zh" else f"- **{MODE_ZH[k]}**: {v['source_zh']} ({probs})")
                for e in v["evidence"]:
                    L.append(f"  - {e}")
                for h in v.get("how_to_confirm", []):
                    L.append(T("  - 确认方法：{h}", "  - How to confirm: {h}", h=h))
            pc = b.get("per_clip_audio") or {}
            if pc.get("ratio") is not None:
                L.append(T("- 逐段原生音频检测：切点处背景声变化 {a}dB，镜头内 {w}dB（比值 {r}）",
                           "- Per-clip native audio check: background changes {a}dB at cuts vs {w}dB within shots (ratio {r})",
                           a=pc["at_cuts_db"], w=pc["within_shots_db"], r=pc["ratio"])
                         + (T("→ 每段画面自带声音", " → each clip carries its own sound") if pc.get("resets_at_cuts")
                            else T("→ 声音跨切点连续，是后期整体铺的", " → sound runs across cuts, laid in post as one bed")))
        plan = b.get("audio_plan", {})
        sfx = plan.get("sfx_merged", [])
        if sfx:
            L += ["", T("**音效**（{n} 处；来源 vlm=大模型听出，deterministic=信号检测的候选，置信度低）",
                        "**Sound effects** ({n}; source vlm = heard by the model, deterministic = signal-detected candidate, low confidence)", n=len(sfx)), "",
                  T("| 时间 | 类型 | 描述 | 对应画面 | 来源 |", "| Time | Type | Description | On screen | Source |"), "|---|---|---|---|---|"]
            for e in sfx[:40]:
                on = (e.get("on") or {}).get("what") or e.get("sync_with", T("切点", "cut") if e.get("sync_with_visual") else "")
                L.append(f"| {float(e.get('t', 0)):.2f}s | {e.get('type', '')} | {e.get('description', '') or e.get('anchor', '')} | "
                         f"{on} | {e.get('source', '')} |")
        else:
            L.append(T("- **音效**：信号层面未检出显著音效（在人声和音乐下面的轻音效需要 VLM 判断，见 --vlm）",
                       "- **Sound effects**: nothing prominent at the signal level (quiet effects under voice and music need the VLM, see --vlm)"))
        if plan.get("plan"):
            L += ["", T("**声音复刻方案**", "**Audio remake plan**"), ""]
            for t in plan["plan"]:
                L.append(f"- {t['track']}（{t['rule']}）：{t['what']} → {t['how']}" if lang() == "zh" else f"- {t['track']} ({t['rule']}): {t['what']} → {t['how']}")
            if plan.get("mix"):
                L.append(T("- 混音：{m}", "- Mix: {m}", m=plan["mix"]))
    L += packaging_md(b)
    w = b.get("whole_video_vlm")
    if w:
        L += ["", T("## 结构与文案（VLM）", "## Structure and copy (VLM)"), "", T("- 概括：{s}", "- Summary: {s}", s=w.get("summary", "")),
              T("- 形式：{f}；钩子：{h}", "- Format: {f}; hook: {h}", f=w.get("format", ""), h=w.get("hook", ""))]
        n = w.get("narration") or {}
        L.append(T("- 旁白：{t} {v}；音乐：{m}；字幕：{s}", "- Narration: {t} {v}; music: {m}; subtitles: {s}",
                   t=n.get("type", ""), v=n.get("voice", ""), m=(w.get("music") or {}).get("style", ""), s=(w.get("subtitles") or {}).get("style", "")))
        L.append(T("- 整体判断：{o}", "- Overall: {o}", o=w.get("overall_production", "")))
        for seg in w.get("segments", [])[:30]:
            L.append(f"  - {seg.get('start')}–{seg.get('end')}s · {seg.get('beat', '')} · {seg.get('copy', '')}")
    rm = s.get("real_material") or {}
    if rm.get("shots"):
        L += ["", T("## 画面里的真实素材", "## Real material in the picture"), "",
              T("共 {n} 个镜头、{s}s（{r:.0%}）的画面里有真实素材（录屏、截图、照片、嵌入视频），复刻时要拿到原文件，不能用模型生成替代：",
                "{n} shots, {s}s ({r:.0%}) contain real material (screen recordings, screenshots, photos, embedded video). The remake needs the original files; a model cannot stand in for them:",
                n=len(rm["shots"]), s=rm["seconds"], r=rm["ratio"]), ""]
        for r in b["shots"]:
            for a in r.get("replication", {}).get("user_assets", []):
                L.append(T("- 镜头 #{i}（{t}）：{k}，{w}", "- Shot #{i} ({t}): {k}, {w}", i=r["index"], t=fmt_time(r["start"]), k=a["kind_zh"], w=a["where"])
                         + (f"{T('，', ', ')}{a['description']}" if a.get("description") else ""))
    if s.get("references"):
        L += ["", T("## 复刻要准备的参考素材", "## Reference material to prepare for the remake"), "",
              T("这些镜头的画面是生成或拍摄出来的，下面是画面里出现、复刻时应作为参考图 / 道具准备的东西（不是嵌入的真实像素）：",
                "These shots were generated or filmed; the items below appear in them and should be prepared as reference images / props for the remake (they are not embedded real pixels):"), ""]
        for r in b["shots"]:
            for a in (r.get("material") or {}).get("references", []):
                L.append(T("- 镜头 #{i}（{t}）：{k}", "- Shot #{i} ({t}): {k}", i=r["index"], t=fmt_time(r["start"]), k=a["kind_zh"])
                         + (f"{T('，', ', ')}{a['description']}" if a.get("description") else ""))
    L += ["", T("## 分镜表", "## Shot list"), "",
          T("| # | 时间 | 时长 | 底层画面 | 置信度 | 素材来源 | 叠加层 | 运镜 | 复刻路线 |", "| # | Time | Length | Base picture | Confidence | Material | Overlays | Camera | Remake route |"),
          "|---|---|---|---|---|---|---|---|---|"]
    for r in b["shots"]:
        base = r["base"]
        L.append(f"| {r['index']} | {fmt_time(r['start'])} | {r['duration']:.2f}s | {base['label_zh']} | {base['confidence']:.0%} | {_origin(r)} | {_layers(r)} | "
                 f"{CAMERA_ZH.get(r['features'].get('camera', 'unknown'), '—')} | {r['replication']['rule']} → {r['replication']['executor']} |")
    L += ["", T("## 逐镜详情", "## Shot by shot"), ""]
    for r in b["shots"]:
        base = r["base"]
        L += [f"### #{r['index']} {fmt_time(r['start'])}–{fmt_time(r['end'])} · {r['verdict']}", "",
              f"![{r['id']}]({r['assets']['sheet']})", ""]
        probs = sorted(base["probs"].items(), key=lambda kv: -kv[1])[:3]
        L.append(T("- 概率：", "- Probabilities: ") + T("，", ", ").join(f"{LABEL_ZH[k]} {v:.0%}" for k, v in probs) + f"（{base['decided_by']}）")
        for key, name in (("uncertain", T("待确认", "To confirm")), ("disputed", T("有分歧", "Disputed"))):
            if base.get(key):
                L.append(f"- ⚠️ {name}{T('：', ': ')}{base[key]}")
        if base.get("scene_text"):
            L.append(T("- 画面里物体上的字（不是后期字幕）：", "- Text on objects in the scene (not subtitles): ") + join(base["scene_text"][:6]))
        if base.get("image_source"):
            L.append(T("- 静帧来源：{s}", "- Still source: {s}", s=base["image_source"]))
        L.append(T("- 素材来源：{o}", "- Material: {o}", o=_origin(r)))
        for it in (r.get("material") or {}).get("items", []):
            if it.get("real_material") and it.get("evidence"):
                L.append(f"  - {it['kind_zh']}{T('：', ': ')}{it['evidence']}")
        if base.get("generator_guess") and base["generator_guess"] != "unknown":
            L.append(T("- 可能的工具：{g}", "- Likely tool: {g}", g=base["generator_guess"]))
        if r.get("beats"):
            L.append(T("- 镜头内动效节拍：", "- Motion beats within the shot: ") + join(f"{t:.2f}s" for t in r["beats"]))
        for e in base["evidence"][:8]:
            L.append(f"- {e}")
        v = r.get("vlm") or r.get("review") or {}
        if v.get("description"):
            L.append(T("- 画面：{d}", "- Picture: {d}", d=v["description"]))
        cl = aspects.shot_text(r) if r["base"]["label"] in aspects.PHOTOGRAPHIC else ""
        if cl:
            L.append(T("- 拍法/打光：{c}", "- Camera / lighting: {c}", c=cl))
        intrinsic = base["label"] in ("code_render", "screen_capture")
        for tr in r.get("text", {}).get("tracks", [])[:4]:
            kind = T("画面内容文字", "text in the picture") if intrinsic else KIND_ZH.get(tr["kind"], tr["kind"])
            L.append(T("- 文字轨（{k}/{b}）：", "- Text track ({k}/{b}): ", k=kind, b=tr["band"]) + " / ".join(t[:40] for t in tr["texts"][:4]))
        rep = r["replication"]
        L.append(T("- 复刻：{n}", "- Remake: {n}", n=rep["note"]))
        if rep.get("capsule", {}).get("image_prompt"):
            L.append(T("  - 首帧图像提示词：{p}", "  - First-frame image prompt: {p}", p=rep["capsule"]["image_prompt"]))
        if rep.get("capsule", {}).get("prompt"):
            L.append(T("  - 视频提示词：{p}", "  - Video prompt: {p}", p=rep["capsule"]["prompt"]))
        L.append("")
    L += [T("## 说明", "## Notes"), "",
          T("- 确定性取证对「是否在动、是否整图推拉、是否渲染纹理、是否 24→30 帧率转换」很可靠；对实拍 / AI / 3D 的区分要依赖 VLM 与人工复核。",
            "- Deterministic forensics is reliable on motion, whole-frame pan/zoom, rendered texture and 24→30 fps conversion; telling live action from AI from 3D relies on the VLM and human review."),
          T("- 元数据经平台转码常被清除，缺失不代表没有。", "- Platform transcoding usually strips metadata; missing metadata proves nothing."), ""]
    return "\n".join(L)


def packaging_md(b: dict) -> list[str]:
    """剪辑包装层: subtitles / 花字 / titles / labels / stickers and the sound effects that land on them."""
    pack = b.get("packaging")
    if not pack:
        return []
    cues = [c for c in pack.get("text_cues", []) if c["kind"] != "scene" and not c.get("part_of_picture")]
    stickers = [st for st in pack.get("stickers", []) if not st.get("in_picture")]
    L = ["", T("## 剪辑包装层（字幕 / 花字 / 贴纸 / 音效卡点）", "## Packaging layer (subtitles / styled text / stickers / sound-effect hits)"), ""]
    for line in pack.get("summary_zh") or []:
        L.append(f"- {line}")
    if not cues and not stickers:
        L.append(T("- 没有检出后期叠加的文字或贴纸", "- No overlaid text or stickers detected")
                 + (T("（未开 OCR，文字层没有分析）", " (OCR off, text layer not analysed)") if not (b.get("analysis") or {}).get("ocr", True) else ""))
    files = pack.get("files") or {}
    if files:
        L.append(T("- 导出：", "- Exports: ") + join(f"`{v}`" for v in files.values())
                 + T("（SRT 是字幕时间轴；ASS 带颜色、描边、位置和入场动画，字体需要换成原片字体；timeline.json 是完整时间轴）",
                     " (SRT is the subtitle timeline; ASS carries colour, outline, position and entrance animation, swap in the original font; timeline.json is the full timeline)"))
    if cues:
        L += ["", T("| 时间 | 类型 | 文字 | 样式 | 入场 / 出场 | 音效 |", "| Time | Kind | Text | Style | In / out | SFX |"), "|---|---|---|---|---|---|"]
        sfx_by_ref: dict[str, list[str]] = {}
        for e in (b.get("audio_plan") or {}).get("sfx_merged", []):
            ref = (e.get("on") or {}).get("ref")
            if ref:
                sfx_by_ref.setdefault(ref, []).append(str(e.get("type", "")))
        for c in sorted(cues, key=lambda c: c["t0"])[:120]:
            anim = " / ".join(x for x in (ANIM_ZH.get((c.get("anim_in") or {}).get("kind"), ""), ANIM_ZH.get((c.get("anim_out") or {}).get("kind"), "")) if x)
            if c.get("mid_motion"):
                anim += T("（中途有弹跳/变色）", " (bounces / changes colour midway)")
            kind = c["kind_zh"] + (T("·跟随物体", " · follows an object") if c.get("follows") else "")
            L.append(f"| {c['t0']:.2f}–{c['t1']:.2f}s | {kind} | {c['text'].replace('/', ' ⏎ ').replace('|', '｜')[:40]} | "
                     f"{c.get('style_zh', '')} | {anim or '—'} | {join(sfx_by_ref.get(c['id'], [])) or '—'} |")
    if stickers:
        L += ["", T("**贴纸 / 动图 / 画中画**", "**Stickers / GIFs / picture-in-picture**"), "",
              T("| 时间 | 类型 | 位置 | 动态 | 置信 | 截图 |", "| Time | Kind | Position | Motion | Confidence | Crop |"), "|---|---|---|---|---|---|"]
        for st in stickers:
            where = (T("x{0:.0%} y{1:.0%} 宽{2:.0%} 高{3:.0%}", "x{0:.0%} y{1:.0%} w{2:.0%} h{3:.0%}").format(*st["bbox"]) if st.get("bbox") else (st.get("where") or "—"))
            dyn = (T("循环，周期 {p:.2f}s", "loops every {p:.2f}s", p=st["loop_period_s"]) if st.get("looping")
                   else (T("在动", "animated") if st.get("animated") else T("静止", "static")))
            why = []
            if st.get("crosses_cut"):
                why.append(T("跨切镜不变", "unchanged across a cut"))
            if st.get("disappears_mid_shot"):
                why.append(T("镜头中途出现又消失", "appears and disappears mid-shot"))
            conf = CONF_ZH.get(st.get("confidence"), st.get("confidence", ""))
            L.append(f"| {st['t0']:.2f}–{st['t1']:.2f}s | {st['kind_zh']} | {where} | {dyn} | {conf}{('（' + join(why) + '）') if why else ''} | "
                     + (f"![{st['id']}]({st['image']})" if st.get("image") else "—") + " |")
        dropped = [st for st in pack.get("stickers", []) if st.get("in_picture")]
        if dropped:
            L.append(T("- 另有 {n} 处画面里突然出现的东西没算作贴纸（3D/动画/版式画面里元素本来就会出现）",
                       "- {n} more things that pop up in the picture were not counted as stickers (elements appear on their own in 3D / animation / layout shots)", n=len(dropped)))
    if pack.get("vlm_text_effects"):
        L += ["", T("**VLM 看到的文字特效**", "**Text effects seen by the VLM**"), ""]
        for e in pack["vlm_text_effects"][:20]:
            L.append(f"- {e.get('t', '')}s {e.get('kind', '')}「{e.get('text', '')}」{e.get('style', '')} {e.get('animation', '')}")
    L += ["", T("复刻：字幕和花字用 `packaging/overlays.ass` 直接压制（ffmpeg -vf ass=overlays.ass），或按 timeline.json 在剪映 / Remotion 里重建；"
                "贴纸和动图要找同款素材（表情包库 / GIF 库），截图在 packaging/ 下。",
                "Remake: burn subtitles and styled text in with `packaging/overlays.ass` (ffmpeg -vf ass=overlays.ass), or rebuild them from timeline.json in CapCut / Remotion; "
                "stickers and GIFs need matching assets (meme / GIF libraries), crops are under packaging/.")]
    return L


def _packaging_html(b: dict) -> str:
    pack = b.get("packaging")
    if not pack:
        return ""
    esc = html.escape
    parts = [f"<section class='shot'><h3 style='margin:4px 0'>{T('剪辑包装层', 'Packaging layer')}</h3>"]
    parts += [f"<p>{esc(x)}</p>" for x in pack.get("summary_zh") or []]
    stickers = [st for st in pack.get("stickers", []) if not st.get("in_picture") and st.get("image")]
    if stickers:
        parts.append("<div style='display:flex;gap:8px;flex-wrap:wrap'>" + "".join(
            f"<figure style='margin:0'><img src='{esc(st['image'])}' style='max-height:90px;border-radius:6px'>"
            f"<figcaption class='muted' style='font-size:12px'>{esc(st['kind_zh'])} {st['t0']:.1f}s</figcaption></figure>" for st in stickers) + "</div>")
    files = pack.get("files") or {}
    if files:
        parts.append("<p class='muted'>" + " · ".join(f"<a href='{esc(v)}'>{esc(v)}</a>" for v in files.values()) + "</p>")
    parts.append("</section>")
    return "".join(parts)


def html_report(b: dict) -> str:
    s = b["summary"]
    esc = html.escape
    rows = []
    for r in b["shots"]:
        base = r["base"]
        c = "rgb(%d,%d,%d)" % LABEL_COLOR.get(base["label"], (80, 80, 80))
        ev = "".join(f"<li>{esc(e)}</li>" for e in base["evidence"][:8])
        desc = esc((r.get("vlm") or r.get("review") or {}).get("description", ""))
        prompt = esc(r["replication"].get("capsule", {}).get("prompt", ""))
        intrinsic = base["label"] in ("code_render", "screen_capture")
        texts = "".join(f"<li>{esc(T('画面内容文字', 'text in the picture') if intrinsic else KIND_ZH.get(t['kind'], t['kind']))}·{esc(t['band'])}{T('：', ': ')}"
                        f"{esc(' / '.join(x[:40] for x in t['texts'][:4]))}</li>" for t in r.get("text", {}).get("tracks", [])[:4])
        rows.append(f"""
<section class="shot">
  <div class="head"><span class="badge" style="background:{c}">{esc(base['label_zh'])} {base['confidence']:.0%}</span>
  <b>#{r['index']}</b> {fmt_time(r['start'])}–{fmt_time(r['end'])} · {r['duration']:.2f}s · {esc(r['verdict'])}
  {('<span class="real">' + T('真实素材', 'real material') + '</span>') if (r.get('material') or {}).get('has_real_material') else ''}</div>
  <img src="{esc(r['assets']['sheet'])}" loading="lazy">
  <div class="cols"><div><h4>{T('证据', 'Evidence')}</h4><ul>{ev}</ul>{('<h4>' + T('文字轨', 'Text tracks') + '</h4><ul>' + texts + '</ul>') if texts else ''}</div>
  <div><h4>{T('画面', 'Picture')}</h4><p>{desc or '—'}</p><h4>{T('复刻', 'Remake')}</h4><p>{esc(r['replication']['rule'])} → {esc(r['replication']['executor'])}<br>{esc(r['replication']['note'])}</p>
  {('<p class="prompt">' + prompt + '</p>') if prompt else ''}</div></div>
</section>""")

    def _rgb(label: str) -> str:
        return "rgb(%d,%d,%d)" % LABEL_COLOR.get(label, (80, 80, 80))

    mix = "".join(f"<div class='bar' style='flex:{m['ratio']};background:{_rgb(m['label'])}' title='{esc(m['label_zh'])}'>{esc(m['label_zh'])} {m['ratio']:.0%}</div>" for m in s["production_mix"])
    rm = s.get("real_material") or {}
    real_line = ""
    if rm.get("shots"):
        real_line = ("<p><b>" + T("用户真实素材：", "Real material: ") + "</b>"
                     + T("{n} 个镜头，{s}s（{r:.0%}）——画面里嵌入的真实素材，复刻时需要原文件", "{n} shots, {s}s ({r:.0%}) embed real material; the remake needs the original files",
                         n=len(rm["shots"]), s=rm["seconds"], r=rm["ratio"]) + "</p>")
    return f"""<!doctype html><html lang="{lang()}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{T('拆解：', 'Breakdown: ')}{esc(b['source']['filename'])}</title>
<style>
:root{{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--card:#fff;--line:#e7e5e4}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--card:#292524;--line:#44403c}}}}
body{{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC","Noto Sans CJK SC",sans-serif}}
main{{max-width:1100px;margin:auto}} h1{{font-size:22px}} .muted{{color:var(--muted)}}
.mix{{display:flex;height:30px;border-radius:6px;overflow:hidden;margin:12px 0}} .bar{{color:#fff;font-size:12px;padding:6px;white-space:nowrap;overflow:hidden}}
.shot{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin:14px 0}}
.shot img{{max-width:100%;border-radius:6px;margin:8px 0}} .badge{{color:#fff;border-radius:4px;padding:2px 8px;margin-right:8px;font-size:13px}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} @media (max-width:700px){{.cols{{grid-template-columns:1fr}}}}
h4{{margin:6px 0 2px;font-size:13px;color:var(--muted)}} ul{{margin:0;padding-left:18px}} .prompt{{background:var(--bg);padding:8px;border-radius:6px;font-size:13px}}
img.sb{{max-width:100%;border-radius:8px}} .real{{background:#0d9488;color:#fff;border-radius:4px;padding:2px 8px;margin-left:8px;font-size:12px}}
</style></head><body><main>
<h1>{T('视频拆解：', 'Video breakdown: ')}{esc(b['source']['filename'])}</h1>
<p><b>{T('结论：', 'Verdict: ')}</b>{esc(s['verdict'])}</p>
{real_line}
<p class="muted">{b['source']['duration']}s · {b['source']['width']}×{b['source']['height']} · {b['source']['fps']}fps · {T('{n} 个镜头', '{n} shots', n=s['shots'])} · {T('置信度', 'confidence')} {s['confidence']:.0%} ·
{esc(s.get('vlm') or vlm_note(b['analysis']['vlm'], b['analysis'].get('review')))}</p>
<div class="mix">{mix}</div>
<img class="sb" src="storyboard.jpg">
{_production_html(b.get("production_plan") or {})}
{aspects.report_html(b)}
{_sound_html(b)}
{_packaging_html(b)}
{''.join(rows)}
</main></body></html>"""


def _shots(ids) -> str:
    return join("#" + str(i) for i in ids)


def _production_html(plan: dict) -> str:
    if not plan:
        return ""
    esc = html.escape
    P = [f"<section class='shot'><h3 style='margin:4px 0'>{T('复刻方案', 'Remake plan')}</h3><p><b>{esc(plan['summary'])}</b></p>"]
    if plan.get("engine_note"):
        P.append(f"<p class='muted'>{esc(plan['engine_note'])}</p>")
    if plan.get("audio_note"):
        P.append(f"<p class='muted'>{T('声音：', 'Audio: ')}{esc(plan['audio_note'])}</p>")
    for u in plan.get("units", []):
        refs = "".join(f"<li><code>{esc(r['tag'])}</code> {esc(r['what'])}</li>" for r in u["refs"])
        P.append(f"<h4>{esc(u['id'])} · {esc(u['label_zh'])} · {T('镜头', 'shots')} {esc(_shots(u['shots']))} · "
                 f"{u['start']:.1f}–{u['end']:.1f}s（{u['duration']:.1f}s）</h4>"
                 + (f"<ul>{refs}</ul>" if refs else "")
                 + (f"<p class='muted'>{esc(u['continuity'])}</p>" if u.get("continuity") else "")
                 + "".join(f"<p class='muted'>{esc(u[k])}</p>" for k in ("note", "alt") if u.get(k))
                 + f"<pre class='prompt' style='white-space:pre-wrap'>{esc(u['prompt'])}</pre>")
    for sc in plan.get("shoot", []):
        rows = "".join(T("<li>机位 {size} · {sup}：镜头 {shots}，成片 {scr}s，建议拍 {take}s</li>",
                         "<li>Setup {size} · {sup}: shots {shots}, {scr}s on screen, shoot about {take}s</li>",
                         size=esc(st["shot_size"]), sup=esc(st["support"]), shots=esc(_shots(st["shots"])), scr=st["screen_s"], take=st["take_s"]) for st in sc["setups"])
        rows += "".join(T("<li>拍完后 #{s} 要 AI 加工：{k}", "<li>#{s} gets AI processing after the shoot: {k}", s=e["shot"], k=esc(e["kind_zh"]))
                        + (f" → {esc(e['how'])}" if e.get("how") else "") + "</li>" for e in sc.get("post", []))
        P.append(T("<h4>场次 {id}：镜头 {shots}（{scr}s）</h4>", "<h4>Scene {id}: shots {shots} ({scr}s)</h4>", id=esc(sc["id"]), shots=esc(_shots(sc["shots"])), scr=sc["screen_s"])
                 + (f"<p>{esc(sc['description'])}</p>" if sc.get("description") else "")
                 + (f"<p class='muted'>{T('布光：', 'Lighting: ')}{esc(sc['lighting'])}</p>" if sc.get("lighting") else "") + f"<ul>{rows}</ul>")
    for c in plan.get("code", []):
        P.append(T("<h4>{id} 代码合成：镜头 {shots}（{d:.1f}s）</h4>", "<h4>{id} code render: shots {shots} ({d:.1f}s)</h4>", id=esc(c["id"]), shots=esc(_shots(c["shots"])), d=c["duration"]))
    P.append("</section>")
    return "".join(P)


def _sound_html(b: dict) -> str:
    esc = html.escape
    src = b.get("sound_sources") or {}
    st = b.get("stems") or {}
    if not src and not st.get("ok"):
        return ""
    parts = [f"<section class='shot'><h3 style='margin:4px 0'>{T('声音', 'Audio')}</h3>"]
    if src.get("summary_zh"):
        parts.append(f"<p><b>{esc(src['summary_zh'])}</b></p>")
    if st.get("ok"):
        for f, name in (("original.wav", T("原声", "original")), ("vocals.wav", T("人声干声", "dry voice")), ("music.wav", T("配乐 + 环境声", "music + ambience"))):
            parts.append(f"<div>{name}{T('：', ': ')}<audio controls preload='none' src='stems/{f}'></audio></div>")
    if (b.get("assets") or {}).get("audio_timeline") or b.get("audio", {}).get("has_audio"):
        parts.append("<img class='sb' src='audio.png' onerror=\"this.remove()\">")
    parts.append("</section>")
    return "".join(parts)


def write_reports(b: dict, out: Path) -> None:
    from .i18n import set_lang

    set_lang(b.get("lang", "zh"))
    (out / "report.md").write_text(markdown(b), encoding="utf-8")
    (out / "report.html").write_text(html_report(b), encoding="utf-8")
