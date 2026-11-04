#!/usr/bin/env python3
"""生成 README 用的流程图 docs/rea-flow.svg（中文）和 docs/rea-flow_en.svg（英文）和 docs/rea-flow_ja.svg（日文）。

用法：python3 scripts/render_flow_svg.py [输出目录，默认 docs]

画的是一个具体例子：一条带货视频切成 5 个镜头 + 声音，每列一个镜头，每行一步
（切开 / 取证据 / 判断 / 复刻方案）。改文案改顶部的 ZH / EN / JA 字典即可。
"""
import sys
from pathlib import Path

ZH = dict(
    title="拿一条带货视频举例：切成 5 个镜头和声音，每个镜头走四步",
    subtitle="横着看是同一个镜头从取证到复刻方案；竖着看是同一步里不同镜头的差别",
    steps=[
        ("①", "切开", "按镜头切开\n声音拆三轨"),
        ("②", "取证据", "测画面\n看图\n听声音"),
        ("③", "判断", "硬证据优先\n冲突就复核"),
        ("④", "复刻方案", "按模型能力\n分批、分路"),
    ],
    shots=["0:00–0:06", "0:06–0:11", "0:11–0:16", "0:16–0:21", "0:21–0:24"],
    sound_lanes=["人声", "配乐", "音效"],
    sound_caption="整条 0:00–0:24",
    kinds={"m": "测", "l": "看", "h": "听"},
    evidence=[
        [("m", "60fps，无重复帧"), ("m", "有传感器噪点"), ("l", "手指、文字正常")],
        [("m", "背景像素逐帧不变"), ("m", "只有局部在动"), ("l", "真实 App 界面")],
        [("m", "24fps，重复帧规律"), ("m", "文件带 AIGC 标识"), ("l", "礼盒上文字乱码")],
        [("m", "工具：像 3D 渲染"), ("l", "看图：像 AI 视频"), ("l", "意见冲突 ⚠")],
        [("m", "背景像素冻结"), ("m", "OCR 出文字轨"), ("l", "没有拍摄痕迹")],
        [("h", "3–8Hz 音节调制"), ("h", "122 BPM，D 小调"), ("h", "切点落在节拍上")],
    ],
    verdicts=[
        ("live", "实拍", 0.96, None),
        ("screen", "录屏 · 真实素材", 0.93, None),
        ("ai", "AI 视频", 0.98, None),
        ("ai", "AI 视频", 0.88, "agent 看图复核后定"),
        ("code", "代码渲染", 0.95, None),
        ("sound", "真人口播 + 曲库配乐", 0.9, None),
    ],
    rule="硬证据（AIGC 标识、帧率规律）优先于「看起来像」；几方意见冲突的镜头标出来，交给 agent 看图复核",
    plans={
        "s1": ("cam", ["自己拍", "1 场景 1 机位", "环形灯 + 手机"]),
        "s2": ("screen", ["自己录屏", "真实素材", "不用模型生成"]),
        "s34": ("ai", ["两镜合并成 1 次生成", "多镜头提示词 + 2 张产品图 + @原片", "按模型能力分批：单次 10 秒、", "能出多镜头、吃 2 张参考图"]),
        "s5": ("code", ["写代码", "Remotion 工程", "文字、版式、动效"]),
        "snd": ("sound", ["三条轨分开做", "口播：自己配或 TTS", "配乐：曲库找 122 BPM", "音效：按时间表加"]),
    },
    footer=("报告  report.html · breakdown.json", "一句话结论 · 按实拍 / AI 生成 / 代码分开写的复刻方案 · 逐镜截图、判断依据、提示词 · 字幕 SRT / ASS"),
)

EN = dict(
    title="A worked example: one product video cut into 5 shots plus sound, each shot going through four steps",
    subtitle="Read across for one shot from evidence to remake plan; read down to compare shots within a step",
    steps=[
        ("1", "Split", "cut into shots\nsplit audio 3 ways"),
        ("2", "Evidence", "measure frames\nlook at images\nlisten"),
        ("3", "Decide", "hard evidence first\nreview conflicts"),
        ("4", "Plan", "batched by model\ncapabilities"),
    ],
    shots=["0:00–0:06", "0:06–0:11", "0:11–0:16", "0:16–0:21", "0:21–0:24"],
    sound_lanes=["voice", "music", "sfx"],
    sound_caption="whole track 0:00–0:24",
    kinds={"m": "M", "l": "L", "h": "H"},
    evidence=[
        [("m", "60fps, no dup frames"), ("m", "sensor noise"), ("l", "hands, text fine")],
        [("m", "static background"), ("m", "only local motion"), ("l", "a real app UI")],
        [("m", "24fps dup cadence"), ("m", "AIGC metadata tag"), ("l", "garbled box text")],
        [("m", "tools: looks 3D"), ("l", "VLM: looks AI"), ("l", "conflict ⚠")],
        [("m", "static background"), ("m", "OCR text track"), ("l", "no camera traces")],
        [("h", "3–8Hz syllable rate"), ("h", "122 BPM, D minor"), ("h", "cuts land on beats")],
    ],
    verdicts=[
        ("live", "Live action", 0.96, None),
        ("screen", "Screen recording", 0.93, None),
        ("ai", "AI video", 0.98, None),
        ("ai", "AI video", 0.88, "agent reviews visually"),
        ("code", "Code render", 0.95, None),
        ("sound", "Voice + library music", 0.9, None),
    ],
    rule="Hard evidence (AIGC tags, frame cadence) outranks \"looks like\"; shots where the sources disagree are flagged for the agent to review visually",
    plans={
        "s1": ("cam", ["Shoot it", "1 scene, 1 camera", "ring light + phone"]),
        "s2": ("screen", ["Record it", "real asset", "never generated"]),
        "s34": ("ai", ["Merge 2 shots into 1 generation", "multi-shot prompt + 2 product images + @source", "batched by model capability: 10 s per call,", "multi-shot output, 2 reference images"]),
        "s5": ("code", ["Write code", "Remotion project", "text, layout, motion"]),
        "snd": ("sound", ["Three tracks", "voice: record or TTS", "music: library, 122 BPM", "sfx: by timetable"]),
    },
    footer=("Report  report.html · breakdown.json", "one-line verdict · remake plan split into shoot / generate / code · per-shot stills, evidence, prompts · subtitles SRT / ASS"),
)

JA = dict(
    title="商品紹介動画を例に：5 ショットと音声に分け、各ショットが 4 ステップを通る",
    subtitle="横に読むと 1 ショットの根拠からリメイク案まで、縦に読むと同じステップでのショットごとの違い",
    steps=[
        ("①", "分割", "ショットに分け\n音声を 3 つに分離"),
        ("②", "根拠", "フレーム計測\n画像を見る\n音を聴く"),
        ("③", "判定", "確かな根拠を優先\n矛盾は再確認"),
        ("④", "リメイク案", "モデルの能力に\n合わせてまとめる"),
    ],
    shots=["0:00–0:06", "0:06–0:11", "0:11–0:16", "0:16–0:21", "0:21–0:24"],
    sound_lanes=["声", "音楽", "効果音"],
    sound_caption="全体 0:00–0:24",
    kinds={"m": "測", "l": "見", "h": "聴"},
    evidence=[
        [("m", "60fps、重複なし"), ("m", "センサーノイズ"), ("l", "指と文字が自然")],
        [("m", "背景の画素が不変"), ("m", "動くのは一部だけ"), ("l", "実在アプリの UI")],
        [("m", "24fps 重複の周期"), ("m", "AIGC タグあり"), ("l", "箱の文字が崩れ")],
        [("m", "ツール：3D っぽい"), ("l", "VLM：AI 動画"), ("l", "意見が矛盾 ⚠")],
        [("m", "背景の画素が固定"), ("m", "OCR で文字軌道"), ("l", "撮影の痕跡なし")],
        [("h", "3–8Hz の音節変調"), ("h", "122 BPM、ニ短調"), ("h", "カットが拍に乗る")],
    ],
    verdicts=[
        ("live", "実写", 0.96, None),
        ("screen", "画面収録・実素材", 0.93, None),
        ("ai", "AI 動画", 0.98, None),
        ("ai", "AI 動画", 0.88, "agent が目視で再確認"),
        ("code", "コード描画", 0.95, None),
        ("sound", "実声 + ライブラリ音楽", 0.9, None),
    ],
    rule="確かな根拠（AIGC タグ、フレーム周期）は「そう見える」より優先。意見が割れたショットは agent が目視で再確認",
    plans={
        "s1": ("cam", ["自分で撮る", "1 シーン 1 カメラ", "照明 + スマホ"]),
        "s2": ("screen", ["画面収録", "実素材", "生成しない"]),
        "s34": ("ai", ["2 ショットを 1 回で生成", "マルチショット用プロンプト + 商品画像 2 枚", "モデルの能力に合わせて：1 回 10 秒、", "マルチショット出力、参照画像 2 枚"]),
        "s5": ("code", ["コードを書く", "Remotion で再現", "文字、配置、動き"]),
        "snd": ("sound", ["3 トラック", "声：自分で録るか TTS", "音楽：122 BPM の曲", "効果音：時刻表どおり"]),
    },
    footer=("レポート  report.html · breakdown.json", "一文の結論 · 実写 / AI 生成 / コード別のリメイク案 · ショットごとのスクショ、根拠、プロンプト · 字幕 SRT / ASS"),
)

# ---------------------------------------------------------------- palette
C = {
    "ink": "#0f172a", "mute": "#64748b", "line": "#cbd5e1", "band": "#f8fafc", "bg": "#ffffff",
    "live": "#16a34a", "screen": "#2563eb", "ai": "#9333ea", "code": "#d97706", "sound": "#0d9488",
    "live_bg": "#dcfce7", "screen_bg": "#dbeafe", "ai_bg": "#f3e8ff", "code_bg": "#fef3c7", "sound_bg": "#ccfbf1",
    "m_bg": "#e2e8f0", "m_fg": "#334155", "l_bg": "#ede9fe", "l_fg": "#5b21b6", "h_bg": "#ccfbf1", "h_fg": "#115e59",
    "warn": "#b45309", "warn_bg": "#fef3c7",
}

W, H = 1000, 738
GX = 24          # gutter x
COL0 = 134       # first column x
COLW = 126
GAP = 12
SND_X = COL0 + 5 * (COLW + GAP) + 6   # 830
SND_W = W - SND_X - 18                # 152
ROWS = {"split": (84, 214), "evidence": (222, 338), "decide": (346, 466), "plan": (474, 636)}
FONT_JA = "Hiragino Sans, Hiragino Kaku Gothic ProN, Yu Gothic, Meiryo, Noto Sans CJK JP, Noto Sans JP, sans-serif"
FONT = "PingFang SC, Hiragino Sans GB, Microsoft YaHei, Noto Sans CJK SC, Source Han Sans SC, Segoe UI, Helvetica, Arial, sans-serif"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def col_x(i):
    return COL0 + i * (COLW + GAP)


def text(x, y, s, size=13, fill=C["ink"], weight=None, anchor=None, extra=""):
    a = f' text-anchor="{anchor}"' if anchor else ""
    w = f' font-weight="{weight}"' if weight else ""
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}"{w}{a}{extra}>{esc(s)}</text>'


# ---------------------------------------------------------------- pictograms
def thumb_frame(x, y, w, h, fill, extra=""):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{C["line"]}"/>{extra}'


def sparkle(cx, cy, r, fill="#fff"):
    return (f'<path d="M{cx},{cy-r} Q{cx},{cy} {cx+r},{cy} Q{cx},{cy} {cx},{cy+r} Q{cx},{cy} {cx-r},{cy} Q{cx},{cy} {cx},{cy-r} Z" fill="{fill}"/>')


def thumb_talk(x, y, w, h):
    cx = x + w / 2
    o = [f'<clipPath id="ct"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/></clipPath>',
         f'<g clip-path="url(#ct)">',
         f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#fde7d6"/>',
         f'<rect x="{x}" y="{y}" width="{w}" height="{h*0.55}" fill="#fbd3b8"/>',
         # shoulders + head
         f'<path d="M{cx-34},{y+h} C{cx-34},{y+h-28} {cx-18},{y+h-32} {cx},{y+h-32} C{cx+18},{y+h-32} {cx+34},{y+h-28} {cx+34},{y+h} Z" fill="#f97316"/>',
         f'<circle cx="{cx}" cy="{y+h-48}" r="14" fill="#fed7aa"/>',
         f'<path d="M{cx-14},{y+h-50} a14,14 0 0 1 28,0 l0,-4 a14,10 0 0 0 -28,0 Z" fill="#7c2d12"/>',
         # mic
         f'<rect x="{cx+16}" y="{y+h-46}" width="6" height="12" rx="3" fill="#1f2937"/><path d="M{cx+19},{y+h-34} v6" stroke="#1f2937" stroke-width="1.6"/>',
         # subtitle bar
         f'<rect x="{x+14}" y="{y+h-16}" width="{w-28}" height="10" rx="3" fill="#fff" opacity="0.9"/>',
         f'<rect x="{x+26}" y="{y+h-13}" width="{w-52}" height="4" rx="2" fill="#f97316"/>',
         '</g>', f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="none" stroke="{C["line"]}"/>']
    return "".join(o)


def thumb_phone(x, y, w, h):
    cx = x + w / 2
    px, py, pw, ph = cx - 22, y + 8, 44, h - 16
    o = [thumb_frame(x, y, w, h, "#e2e8f0"),
         f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="7" fill="#0f172a"/>',
         f'<rect x="{px+3}" y="{py+6}" width="{pw-6}" height="{ph-12}" rx="4" fill="#fff"/>',
         f'<rect x="{px+8}" y="{py+12}" width="{pw-16}" height="5" rx="2" fill="#2563eb"/>',
         f'<rect x="{px+8}" y="{py+22}" width="{pw-20}" height="3" rx="1.5" fill="#94a3b8"/>',
         f'<rect x="{px+8}" y="{py+29}" width="{pw-24}" height="3" rx="1.5" fill="#94a3b8"/>',
         f'<rect x="{px+8}" y="{py+36}" width="{pw-18}" height="3" rx="1.5" fill="#94a3b8"/>',
         f'<rect x="{px+8}" y="{py+ph-22}" width="{pw-16}" height="9" rx="4" fill="#2563eb"/>',
         # cursor hand (a simple pointer)
         f'<path d="M{cx+10},{py+ph-14} l6,-6 l2,2 l-3,3 l6,0 l0,2 l-6,0 l3,3 l-2,2 Z" fill="#f97316" stroke="#fff" stroke-width="0.8"/>']
    return "".join(o)


def thumb_product(x, y, w, h, close=False, cid="cp"):
    cx = x + w / 2
    o = [f'<clipPath id="{cid}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/></clipPath>',
         f'<g clip-path="url(#{cid})">',
         f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#gai)"/>',
         f'<ellipse cx="{cx}" cy="{y+h-10}" rx="{w*0.4}" ry="8" fill="#581c87" opacity="0.35"/>']
    if not close:
        # bottle
        o += [f'<rect x="{cx-11}" y="{y+22}" width="22" height="44" rx="6" fill="#faf5ff" stroke="#c084fc"/>',
              f'<rect x="{cx-6}" y="{y+14}" width="12" height="10" rx="2" fill="#a855f7"/>',
              f'<rect x="{cx-8}" y="{y+36}" width="16" height="14" rx="2" fill="#c084fc"/>',
              # gift box beside with garbled text
              f'<rect x="{cx+16}" y="{y+40}" width="26" height="26" rx="3" fill="#fbcfe8" stroke="#ec4899"/>',
              f'<path d="M{cx+16},{y+52} h26 M{cx+29},{y+40} v26" stroke="#ec4899" stroke-width="2"/>',
              text(cx + 19, y + 50, "ﾎ巿", 7, "#9d174d", extra=' font-family="monospace"'),
              sparkle(x + 18, y + 20, 7), sparkle(x + w - 20, y + 30, 5), sparkle(x + 30, y + h - 24, 4)]
    else:
        o += [f'<rect x="{cx-26}" y="{y+6}" width="52" height="{h}" rx="12" fill="#faf5ff" stroke="#c084fc"/>',
              f'<rect x="{cx-14}" y="{y-6}" width="28" height="18" rx="4" fill="#a855f7"/>',
              f'<rect x="{cx-20}" y="{y+34}" width="40" height="28" rx="4" fill="#c084fc"/>',
              f'<rect x="{cx-14}" y="{y+42}" width="28" height="4" rx="2" fill="#faf5ff"/>',
              f'<rect x="{cx-14}" y="{y+50}" width="18" height="4" rx="2" fill="#faf5ff"/>',
              sparkle(x + 16, y + 26, 6), sparkle(x + w - 18, y + 18, 7), sparkle(x + w - 26, y + h - 20, 4)]
    o += ['</g>', f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="none" stroke="{C["line"]}"/>']
    return "".join(o)


def thumb_card(x, y, w, h, headline):
    o = [thumb_frame(x, y, w, h, "#0f172a"),
         f'<rect x="{x+14}" y="{y+16}" width="34" height="8" rx="4" fill="#f59e0b"/>',
         text(x + 14, y + 48, headline, 20, "#fff", 800),
         f'<rect x="{x+14}" y="{y+58}" width="{w-50}" height="4" rx="2" fill="#94a3b8"/>',
         f'<rect x="{x+14}" y="{y+66}" width="{w-70}" height="4" rx="2" fill="#94a3b8"/>',
         # mini bar chart
         f'<rect x="{x+w-38}" y="{y+52}" width="6" height="18" fill="#38bdf8"/>',
         f'<rect x="{x+w-29}" y="{y+44}" width="6" height="26" fill="#38bdf8"/>',
         f'<rect x="{x+w-20}" y="{y+34}" width="6" height="36" fill="#f59e0b"/>']
    return "".join(o)


def waveform(x, y, w, h, kind, color, seed):
    """Deterministic pseudo-random bars for a lane."""
    import math
    n = 36
    step = w / n
    bars = []
    for i in range(n):
        t = i / n
        if kind == "voice":
            amp = 0.3 + 0.7 * abs(math.sin(i * 1.7 + seed)) * (0 if 0.42 < t < 0.5 or 0.75 < t < 0.8 else 1)
        elif kind == "music":
            amp = 0.45 + 0.55 * abs(math.sin(i * 0.9 + seed)) * (1 if i % 4 else 1.0)
            if i % 4 == 0:
                amp = 1.0
        else:
            amp = 1.0 if i in (5, 17, 29) else 0.08
        bh = max(1.5, amp * h)
        bars.append(f'<rect x="{x+i*step+0.5:.1f}" y="{y+(h-bh)/2:.1f}" width="{step-1.2:.1f}" height="{bh:.1f}" rx="1" fill="{color}"/>')
    return "".join(bars)


def thumb_sound(x, y, w, h, lanes):
    o = [thumb_frame(x, y, w, h, "#f0fdfa")]
    lane_h = (h - 16) / 3
    cols = ["#f97316", "#0d9488", "#2563eb"]
    kinds = ["voice", "music", "sfx"]
    for i, name in enumerate(lanes):
        ly = y + 8 + i * lane_h
        o.append(text(x + 8, ly + lane_h / 2 + 4, name, 10, C["mute"], 700))
        o.append(waveform(x + 42, ly + 3, w - 50, lane_h - 6, kinds[i], cols[i], i * 2.3))
    return "".join(o)


# ---------------------------------------------------------------- icons for plan cards
def icon(kind, x, y, color):
    s = 20
    if kind == "cam":
        return (f'<rect x="{x}" y="{y+5}" width="{s}" height="{s-6}" rx="3" fill="none" stroke="{color}" stroke-width="2"/>'
                f'<circle cx="{x+s/2}" cy="{y+12}" r="4" fill="none" stroke="{color}" stroke-width="2"/>'
                f'<rect x="{x+6}" y="{y+1}" width="8" height="5" rx="1" fill="{color}"/>')
    if kind == "screen":
        return (f'<rect x="{x}" y="{y+2}" width="{s}" height="{s-7}" rx="2" fill="none" stroke="{color}" stroke-width="2"/>'
                f'<path d="M{x+6},{y+s} h8 M{x+s/2},{y+s-5} v5" stroke="{color}" stroke-width="2"/>'
                f'<circle cx="{x+s-5}" cy="{y+7}" r="2.5" fill="#ef4444"/>')
    if kind == "ai":
        return sparkle(x + 10, y + 10, 10, color) + sparkle(x + 19, y + 3, 4, color)
    if kind == "code":
        return (f'<path d="M{x+6},{y+3} l-6,7 l6,7 M{x+14},{y+3} l6,7 l-6,7 M{x+12},{y+1} l-4,18" '
                f'fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
    if kind == "sound":
        return (f'<path d="M{x+2},{y+7} h5 l6,-5 v16 l-6,-5 h-5 Z" fill="{color}"/>'
                f'<path d="M{x+16},{y+6} q4,4 0,8 M{x+19},{y+3} q7,7 0,14" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round"/>')
    return ""


# ---------------------------------------------------------------- main
def build(S, lang):
    o = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="{FONT_JA if lang == "ja" else FONT}">')
    o.append('<defs>'
             '<marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
             f'<path d="M0,0 L10,5 L0,10 z" fill="{C["mute"]}"/></marker>'
             '<linearGradient id="gai" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#c084fc"/><stop offset="1" stop-color="#6d28d9"/></linearGradient>'
             '<filter id="sh" x="-5%" y="-5%" width="110%" height="120%"><feDropShadow dx="0" dy="1.5" stdDeviation="2" flood-color="#0f172a" flood-opacity="0.10"/></filter>'
             '</defs>')
    o.append(f'<rect width="{W}" height="{H}" rx="18" fill="{C["bg"]}"/>')

    # title
    o.append(text(GX, 36, S["title"], {"zh": 21, "ja": 19, "en": 19}[lang], C["ink"], 800))
    o.append(text(GX, 60, S["subtitle"], 13, C["mute"]))

    # row bands
    for i, key in enumerate(["split", "evidence", "decide", "plan"]):
        y0, y1 = ROWS[key]
        if i % 2 == 0:
            o.append(f'<rect x="{GX-8}" y="{y0-6}" width="{W-2*GX+16}" height="{y1-y0+12}" rx="12" fill="{C["band"]}"/>')

    # column divider before sound
    dv = SND_X - 8
    o.append(f'<path d="M{dv},{ROWS["split"][0]-2} V{ROWS["plan"][1]+2}" stroke="{C["line"]}" stroke-width="1.5" stroke-dasharray="4 4"/>')

    # step labels in gutter
    step_colors = ["#0f172a", "#0f172a", "#0f172a", "#0f172a"]
    for (num, name, sub), key, colr in zip(S["steps"], ["split", "evidence", "decide", "plan"], step_colors):
        y0, y1 = ROWS[key]
        cy = y0 + 20
        o.append(f'<circle cx="{GX+13}" cy="{cy}" r="13" fill="{C["ink"]}"/>')
        o.append(text(GX + 13, cy + 5, num, 13, "#fff", 800, "middle"))
        o.append(text(GX + 32, cy + 5, name, {"zh": 16, "ja": 15, "en": 14}[lang], C["ink"], 800))
        for j, line in enumerate(sub.split("\n")):
            o.append(text(GX, cy + 26 + j * 17, line, 12, C["mute"]))
        # arrow to next row
        if key != "plan":
            ny0 = ROWS[["split", "evidence", "decide", "plan"][["split", "evidence", "decide", "plan"].index(key) + 1]][0]
            o.append(f'<path d="M{GX+13},{y1-4} V{ny0+2}" stroke="{C["mute"]}" stroke-width="1.6" marker-end="url(#ah)" fill="none"/>')

    # ---- row 1: split
    y0, y1 = ROWS["split"]
    ty = y0 + 6
    th = 84
    o.append(thumb_talk(col_x(0), ty, COLW, th))
    o.append(thumb_phone(col_x(1), ty, COLW, th))
    o.append(thumb_product(col_x(2), ty, COLW, th, False, "cp1"))
    o.append(thumb_product(col_x(3), ty, COLW, th, True, "cp2"))
    o.append(thumb_card(col_x(4), ty, COLW, th, "5折" if lang == "zh" else "50%"))
    o.append(thumb_sound(SND_X, ty, SND_W, th, S["sound_lanes"]))
    # filmstrip sprockets between frames
    for i in range(5):
        x = col_x(i)
        for k in range(6):
            o.append(f'<rect x="{x+6+k*22}" y="{ty+th+6}" width="8" height="5" rx="1.5" fill="{C["line"]}"/>')
        o.append(text(x + COLW / 2, ty + th + 28, f"{S['shots'][i]}", 11.5, C["mute"], None, "middle"))
        o.append(text(x + COLW / 2, ty + th + 28, "", 11))
    for i in range(4):
        x = col_x(i) + COLW
        o.append(f'<rect x="{x+1}" y="{ty+30}" width="{GAP-2}" height="24" fill="{C["line"]}" opacity="0.6"/>')
    o.append(text(SND_X + SND_W / 2, ty + th + 28, S["sound_caption"], 11.5, C["mute"], None, "middle"))

    # ---- row 2: evidence chips
    y0, y1 = ROWS["evidence"]
    kinds = S["kinds"]
    for i, chips in enumerate(S["evidence"]):
        x = col_x(i) if i < 5 else SND_X
        w = COLW if i < 5 else SND_W
        for j, (k, label) in enumerate(chips):
            cy = y0 + 8 + j * 32
            bg, fg = C[f"{k}_bg"], C[f"{k}_fg"]
            warn = "⚠" in label
            if warn:
                bg, fg = C["warn_bg"], C["warn"]
            o.append(f'<rect x="{x}" y="{cy}" width="{w}" height="26" rx="7" fill="{bg}"/>')
            o.append(f'<rect x="{x}" y="{cy}" width="22" height="26" rx="7" fill="{fg}" opacity="0.9"/>')
            o.append(f'<rect x="{x+14}" y="{cy}" width="8" height="26" fill="{fg}" opacity="0.9"/>')
            o.append(text(x + 11, cy + 18, kinds[k], 11.5, "#fff", 800, "middle"))
            o.append(text(x + 28, cy + 17.5, label.replace(" ⚠", ""), {"zh": 11.5, "ja": 11, "en": 10.5}[lang], fg, 600))

    # ---- row 3: verdict
    y0, y1 = ROWS["decide"]
    for i, (kind, label, conf, note) in enumerate(S["verdicts"]):
        x = col_x(i) if i < 5 else SND_X
        w = COLW if i < 5 else SND_W
        colr, bg = C[kind], C[f"{kind}_bg"]
        py = y0 + 6
        o.append(f'<rect x="{x}" y="{py}" width="{w}" height="30" rx="15" fill="{colr}"' + (f' stroke="{C["warn"]}" stroke-width="2" stroke-dasharray="4 3"' if note else "") + '/>')
        o.append(text(x + w / 2, py + 20, label, {"zh": 13, "ja": 12.5, "en": 11.5}[lang], "#fff", 800, "middle"))
        # confidence bar
        by = py + 42
        o.append(f'<rect x="{x}" y="{by}" width="{w}" height="6" rx="3" fill="{C["m_bg"]}"/>')
        o.append(f'<rect x="{x}" y="{by}" width="{w*conf:.0f}" height="6" rx="3" fill="{colr}"/>')
        o.append(text(x, by + 22, f"{int(conf*100)}%", 11, C["mute"], 700))
        if note:
            o.append(text(x, by + 40, "⚠ " + note, 10.5, C["warn"], 700))
    # rule line
    o.append(text(COL0, y1 - 4, S["rule"], 12 if lang != "ja" else 11.5, C["mute"]))

    # ---- row 4: plan cards
    y0, y1 = ROWS["plan"]
    cy0 = y0 + 16
    ch = 120
    layout = [("s1", col_x(0), COLW), ("s2", col_x(1), COLW), ("s34", col_x(2), COLW * 2 + GAP), ("s5", col_x(4), COLW), ("snd", SND_X, SND_W)]
    for key, x, w in layout:
        kind, lines = S["plans"][key]
        colr = {"cam": C["live"], "screen": C["screen"], "ai": C["ai"], "code": C["code"], "sound": C["sound"]}[kind]
        bg = {"cam": C["live_bg"], "screen": C["screen_bg"], "ai": C["ai_bg"], "code": C["code_bg"], "sound": C["sound_bg"]}[kind]
        o.append(f'<g filter="url(#sh)"><rect x="{x}" y="{cy0}" width="{w}" height="{ch}" rx="10" fill="{bg}" stroke="{colr}" stroke-opacity="0.35"/></g>')
        o.append(icon(kind, x + 12, cy0 + 12, colr))
        o.append(text(x + 40, cy0 + 28, lines[0], {"zh": 14, "ja": 13, "en": 12.5}[lang], colr, 800))
        for j, line in enumerate(lines[1:]):
            o.append(text(x + 12, cy0 + 52 + j * 21, line, {"zh": 12, "ja": 11, "en": 10.5}[lang], C["ink"]))
    # bracket joining shots 3+4 above the merged card
    bx0, bx1 = col_x(2) + 10, col_x(3) + COLW - 10
    o.append(f'<path d="M{bx0},{cy0-12} v6 H{bx1} v-6" fill="none" stroke="{C["ai"]}" stroke-width="2.2"/>')
    o.append(f'<path d="M{(bx0+bx1)/2},{cy0-6} v6" stroke="{C["ai"]}" stroke-width="2.2"/>')

    # ---- footer pipeline
    fy = ROWS["plan"][1] + 28
    fx = COL0
    fw = W - COL0 - 18
    o.append(f'<path d="M{GX+13},{ROWS["plan"][1]-4} V{fy+24} H{fx-8}" fill="none" stroke="{C["mute"]}" stroke-width="1.6" marker-end="url(#ah)"/>')
    name, sub = S["footer"]
    o.append(f'<g filter="url(#sh)"><rect x="{fx}" y="{fy}" width="{fw}" height="52" rx="12" fill="{C["ink"]}"/></g>')
    o.append(text(fx + 16, fy + 22, name, 14, "#fff", 800))
    o.append(text(fx + 16, fy + 41, sub, 11.5 if lang != "en" else 11, "#cbd5e1"))

    o.append('</svg>')
    return "\n".join(o)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "docs"
    (out / "rea-flow.svg").write_text(build(ZH, "zh"), encoding="utf-8")
    (out / "rea-flow_en.svg").write_text(build(EN, "en"), encoding="utf-8")
    (out / "rea-flow_ja.svg").write_text(build(JA, "ja"), encoding="utf-8")
    print("written", out)
