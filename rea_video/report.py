"""Human-readable reports (Markdown + standalone HTML) from a breakdown."""
from __future__ import annotations

import html
from pathlib import Path

from .classify import LABEL_ZH
from .util import fmt_time
from .visual import LABEL_COLOR
from .vlm import vlm_note

KIND_ZH = {"subtitle": "字幕", "title": "标题", "label": "文字", "static_graphic": "图形贴片", "layout_template": "版式模板"}
CAMERA_ZH = {"static": "固定", "static_with_subject_motion": "固定（主体在动）", "planar_zoom_pan": "整图推拉/平移",
             "handheld_like": "手持晃动", "smooth_move": "平滑运镜", "unknown": "—"}


def _layers(rec: dict) -> str:
    ks = []
    for l in rec.get("layers", []):
        k = KIND_ZH.get(l["kind"], l["kind"])
        if k not in ks:
            ks.append(k)
    return "、".join(ks) or "—"


def _origin(rec: dict) -> str:
    m = rec.get("material") or {}
    if not m:
        return "—"
    kinds = []
    for it in m.get("items", []):
        if it.get("real_material") and it["kind_zh"] not in kinds:
            kinds.append(it["kind_zh"])
    unsure = sorted({it["kind_zh"] for it in m.get("items", []) if it.get("real_material") is None})
    return (m.get("origin_zh", "—") + (f"：{'、'.join(kinds)}" if kinds else "")
            + (f"；待确认是否真实：{'、'.join(unsure)}" if unsure else ""))


def markdown(b: dict) -> str:
    s = b["summary"]
    src = b["source"]
    L = [f"# 视频拆解报告：{src['filename']}", "",
         f"**结论**：{s['verdict']}", "",
         f"- 时长 {src['duration']}s · {src['width']}×{src['height']} · {src['fps']}fps · {src['aspect_ratio']}",
         f"- 镜头 {s['shots']} 个，平均 {s['avg_shot_s']}s；转场：" + "、".join(f"{k}×{v}" for k, v in s["transitions"].items()),
         f"- 判断置信度（按时长加权）：{s['confidence']:.0%}；判定方式：" + ("确定性取证 + VLM 融合" if b["analysis"]["vlm"].get("used") else ("确定性取证 + agent 看图融合" if (b["analysis"].get("review") or {}).get("shots") else "仅确定性取证")),
         f"- {s.get('vlm') or vlm_note(b['analysis']['vlm'], b['analysis'].get('review'))}",
         "", "![storyboard](storyboard.jpg)", "", "## 制作方式占比", "", "| 底层画面 | 时长 | 占比 |", "|---|---|---|"]
    for m in s["production_mix"]:
        L.append(f"| {m['label_zh']} | {m['seconds']}s | {m['ratio']:.0%} |")
    prov = b.get("provenance", {})
    if prov.get("hints") or prov.get("encoder"):
        L += ["", "## 元数据线索", ""]
        if prov.get("encoder"):
            L.append(f"- 编码器：`{prov['encoder']}`")
        for k, v in (prov.get("hints") or {}).items():
            L.append(f"- {k}：{', '.join(v)}")
    a = b.get("audio", {})
    if a.get("has_audio"):
        L += ["", "## 声音拆解", "", "![audio](audio.png)", "",
              f"- 整体响度 {a.get('rms_dbfs')} dBFS，峰值 {a.get('peak_dbfs')} dBFS"]
        v = a.get("voice", {})
        L.append(f"- **人声**：{'有' if v.get('present') else '无/很少'}，占 {v.get('ratio', 0):.0%} 时长"
                 + (f"，基频约 {v.get('f0_median_hz')}Hz（{ {'male': '偏男声', 'female': '偏女声'}.get(v.get('gender_hint'), '性别不确定') }）" if v.get("f0_median_hz") else ""))
        mu = a.get("music", {})
        if mu.get("present"):
            key = mu.get("key") or {}
            L.append(f"- **背景音乐**：占 {mu.get('ratio', 0):.0%} 时长（{mu.get('start')}–{mu.get('end')}s），约 {mu.get('tempo_bpm')} BPM"
                     f"（也可能是 {'/'.join(str(t) for t in mu.get('tempo_alternatives', []))}）"
                     + (f"，调性 {key.get('tonic')} {key.get('mode')}（置信 {key.get('confidence')}）" if key else "")
                     + (f"，段落变化点 {', '.join(str(t) + 's' for t in mu.get('section_changes', [])[:8])}" if mu.get("section_changes") else ""))
        else:
            L.append("- **背景音乐**：未检测到持续的音乐床")
        if "cuts_on_beat_ratio" in a:
            L.append(f"- **剪辑与节拍**：{a['cuts_on_beat_ratio']:.0%} 的切点落在节拍上（随机约 {a['cuts_on_beat_chance']:.0%}）" + ("→ **卡点剪辑**" if a.get("beat_synced_edit") else ""))
        src = b.get("sound_sources") or {}
        if src:
            L += ["", "**声音来源：AI 生成还是现成素材**", ""]
            if src.get("summary_zh"):
                L.append(f"- **结论**：{src['summary_zh']}")
            st = b.get("stems") or {}
            if st.get("ok"):
                L.append("- **剥离出的音轨**：`stems/original.wav`（原声）、`stems/vocals.wav`（人声干声，瞬态音效也会落在这里）、`stems/music.wav`（配乐 + 环境声）")
            ctx = src.get("format_context", {})
            L.append(f"- 视频形式：{'音乐 MV' if ctx.get('music_mv') else ('疑似带唱歌曲/MV' if ctx.get('music_mv_maybe') else (ctx.get('format') or '普通视频配乐'))}"
                     f"；AI 画面占 {ctx.get('ai_picture_share', 0):.0%}，实拍占 {ctx.get('live_picture_share', 0):.0%}")
            names = {"music": "背景音乐", "voice": "人声", "sfx": "音效"}
            for k in ("music", "voice", "sfx"):
                v = src.get(k)
                if not v:
                    continue
                probs = "，".join(f"{kk} {vv:.0%}" for kk, vv in sorted(v["probs"].items(), key=lambda kv: -kv[1]))
                L.append(f"- **{names[k]}**：{v['source_zh']}（{probs}）")
                for e in v["evidence"]:
                    L.append(f"  - {e}")
                for h in v.get("how_to_confirm", []):
                    L.append(f"  - 确认方法：{h}")
            pc = b.get("per_clip_audio") or {}
            if pc.get("ratio") is not None:
                L.append(f"- 逐段原生音频检测：切点处背景声变化 {pc['at_cuts_db']}dB，镜头内 {pc['within_shots_db']}dB（比值 {pc['ratio']}）"
                         + ("→ 每段画面自带声音" if pc.get("resets_at_cuts") else "→ 声音跨切点连续，是后期整体铺的"))
        plan = b.get("audio_plan", {})
        sfx = plan.get("sfx_merged", [])
        if sfx:
            L += ["", f"**音效**（{len(sfx)} 处；来源 vlm=大模型听出，deterministic=信号检测的候选，置信度低）", "",
                  "| 时间 | 类型 | 描述 | 对应画面 | 来源 |", "|---|---|---|---|---|"]
            for e in sfx[:40]:
                L.append(f"| {float(e.get('t', 0)):.2f}s | {e.get('type', '')} | {e.get('description', '')} | "
                         f"{e.get('sync_with', '切点' if e.get('sync_with_visual') else '')} | {e.get('source', '')} |")
        else:
            L.append("- **音效**：信号层面未检出显著音效（在人声和音乐下面的轻音效需要 VLM 判断，见 --vlm）")
        if plan.get("plan"):
            L += ["", "**声音复刻方案**", ""]
            for t in plan["plan"]:
                L.append(f"- {t['track']}（{t['rule']}）：{t['what']} → {t['how']}")
            if plan.get("mix"):
                L.append(f"- 混音：{plan['mix']}")
    w = b.get("whole_video_vlm")
    if w:
        L += ["", "## 结构与文案（VLM）", "", f"- 概括：{w.get('summary', '')}", f"- 形式：{w.get('format', '')}；钩子：{w.get('hook', '')}"]
        n = w.get("narration") or {}
        L.append(f"- 旁白：{n.get('type', '')} {n.get('voice', '')}；音乐：{(w.get('music') or {}).get('style', '')}；字幕：{(w.get('subtitles') or {}).get('style', '')}")
        L.append(f"- 整体判断：{w.get('overall_production', '')}")
        for seg in w.get("segments", [])[:30]:
            L.append(f"  - {seg.get('start')}–{seg.get('end')}s · {seg.get('beat', '')} · {seg.get('copy', '')}")
    rm = s.get("real_material") or {}
    if rm.get("shots"):
        L += ["", "## 用户真实素材", "",
              f"共 {len(rm['shots'])} 个镜头、{rm['seconds']}s（{rm['ratio']:.0%}）引入了真实素材，复刻时需要准备对应素材，不能用模型生成替代：", ""]
        for r in b["shots"]:
            for a in r.get("replication", {}).get("user_assets", []):
                L.append(f"- 镜头 #{r['index']}（{fmt_time(r['start'])}）：{a['kind_zh']}，{a['where']}" + (f"，{a['description']}" if a.get("description") else ""))
    L += ["", "## 分镜表", "", "| # | 时间 | 时长 | 底层画面 | 置信度 | 素材来源 | 叠加层 | 运镜 | 复刻路线 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in b["shots"]:
        base = r["base"]
        L.append(f"| {r['index']} | {fmt_time(r['start'])} | {r['duration']:.2f}s | {base['label_zh']} | {base['confidence']:.0%} | {_origin(r)} | {_layers(r)} | "
                 f"{CAMERA_ZH.get(r['features'].get('camera', 'unknown'), '—')} | {r['replication']['rule']} → {r['replication']['executor']} |")
    L += ["", "## 逐镜详情", ""]
    for r in b["shots"]:
        base = r["base"]
        L += [f"### #{r['index']} {fmt_time(r['start'])}–{fmt_time(r['end'])} · {r['verdict']}", "",
              f"![{r['id']}]({r['assets']['sheet']})", ""]
        probs = sorted(base["probs"].items(), key=lambda kv: -kv[1])[:3]
        L.append("- 概率：" + "，".join(f"{LABEL_ZH[k]} {v:.0%}" for k, v in probs) + f"（{base['decided_by']}）")
        if base.get("image_source"):
            L.append(f"- 静帧来源：{base['image_source']}")
        L.append(f"- 素材来源：{_origin(r)}")
        for it in (r.get("material") or {}).get("items", []):
            if it.get("real_material") and it.get("evidence"):
                L.append(f"  - {it['kind_zh']}：{it['evidence']}")
        if base.get("generator_guess") and base["generator_guess"] != "unknown":
            L.append(f"- 可能的工具：{base['generator_guess']}")
        if r.get("beats"):
            L.append("- 镜头内动效节拍：" + "、".join(f"{t:.2f}s" for t in r["beats"]))
        for e in base["evidence"][:8]:
            L.append(f"- {e}")
        v = r.get("vlm") or r.get("review") or {}
        if v.get("description"):
            L.append(f"- 画面：{v['description']}")
        intrinsic = base["label"] in ("code_render", "screen_capture")
        for tr in r.get("text", {}).get("tracks", [])[:4]:
            kind = "画面内容文字" if intrinsic else KIND_ZH.get(tr["kind"], tr["kind"])
            L.append(f"- 文字轨（{kind}/{tr['band']}）：" + " / ".join(t[:40] for t in tr["texts"][:4]))
        rep = r["replication"]
        L.append(f"- 复刻：{rep['note']}")
        if rep.get("capsule", {}).get("prompt"):
            L.append(f"  - 视频提示词：{rep['capsule']['prompt']}")
        L.append("")
    L += ["## 说明", "",
          "- 确定性取证对「是否在动、是否整图推拉、是否渲染纹理、是否 24→30 帧率转换」很可靠；对实拍 / AI / 3D 的区分要依赖 VLM 与人工复核。",
          "- 元数据经平台转码常被清除，缺失不代表没有。", ""]
    return "\n".join(L)


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
        texts = "".join(f"<li>{esc('画面内容文字' if intrinsic else KIND_ZH.get(t['kind'], t['kind']))}·{esc(t['band'])}：{esc(' / '.join(x[:40] for x in t['texts'][:4]))}</li>" for t in r.get("text", {}).get("tracks", [])[:4])
        rows.append(f"""
<section class="shot">
  <div class="head"><span class="badge" style="background:{c}">{esc(base['label_zh'])} {base['confidence']:.0%}</span>
  <b>#{r['index']}</b> {fmt_time(r['start'])}–{fmt_time(r['end'])} · {r['duration']:.2f}s · {esc(r['verdict'])}
  {('<span class="real">真实素材</span>') if (r.get('material') or {}).get('has_real_material') else ''}</div>
  <img src="{esc(r['assets']['sheet'])}" loading="lazy">
  <div class="cols"><div><h4>证据</h4><ul>{ev}</ul>{('<h4>文字轨</h4><ul>' + texts + '</ul>') if texts else ''}</div>
  <div><h4>画面</h4><p>{desc or '—'}</p><h4>复刻</h4><p>{esc(r['replication']['rule'])} → {esc(r['replication']['executor'])}<br>{esc(r['replication']['note'])}</p>
  {('<p class="prompt">' + prompt + '</p>') if prompt else ''}</div></div>
</section>""")
    def _rgb(label: str) -> str:
        return "rgb(%d,%d,%d)" % LABEL_COLOR.get(label, (80, 80, 80))

    mix = "".join(f"<div class='bar' style='flex:{m['ratio']};background:{_rgb(m['label'])}' title='{esc(m['label_zh'])}'>{esc(m['label_zh'])} {m['ratio']:.0%}</div>" for m in s["production_mix"])
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>拆解：{esc(b['source']['filename'])}</title>
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
<h1>视频拆解：{esc(b['source']['filename'])}</h1>
<p><b>结论：</b>{esc(s['verdict'])}</p>
{('<p><b>用户真实素材：</b>' + str(len(s['real_material']['shots'])) + ' 个镜头，' + str(s['real_material']['seconds']) + 's（' + format(s['real_material']['ratio'], '.0%') + '）——复刻时需要自备对应素材</p>') if (s.get('real_material') or {}).get('shots') else ''}
<p class="muted">{b['source']['duration']}s · {b['source']['width']}×{b['source']['height']} · {b['source']['fps']}fps · {s['shots']} 个镜头 · 置信度 {s['confidence']:.0%} ·
{esc(s.get('vlm') or vlm_note(b['analysis']['vlm'], b['analysis'].get('review')))}</p>
<div class="mix">{mix}</div>
<img class="sb" src="storyboard.jpg">
{_sound_html(b)}
{''.join(rows)}
</main></body></html>"""


def _sound_html(b: dict) -> str:
    esc = html.escape
    src = b.get("sound_sources") or {}
    st = b.get("stems") or {}
    if not src and not st.get("ok"):
        return ""
    parts = ["<section class='shot'><h3 style='margin:4px 0'>声音</h3>"]
    if src.get("summary_zh"):
        parts.append(f"<p><b>{esc(src['summary_zh'])}</b></p>")
    if st.get("ok"):
        for f, name in (("original.wav", "原声"), ("vocals.wav", "人声干声"), ("music.wav", "配乐 + 环境声")):
            parts.append(f"<div>{name}：<audio controls preload='none' src='stems/{f}'></audio></div>")
    if (b.get("assets") or {}).get("audio_timeline") or b.get("audio", {}).get("has_audio"):
        parts.append("<img class='sb' src='audio.png' onerror=\"this.remove()\">")
    parts.append("</section>")
    return "".join(parts)


def write_reports(b: dict, out: Path) -> None:
    (out / "report.md").write_text(markdown(b), encoding="utf-8")
    (out / "report.html").write_text(html_report(b), encoding="utf-8")
