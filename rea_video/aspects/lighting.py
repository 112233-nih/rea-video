"""Lighting: where the key light comes from, how strong against the shadow side, how soft, what colour, what else
is lit; then a named set-up with gear to rebuild it.

Measured on the frame's subject (aspects.subject): the face split at the nose, or the in-focus region split at its
centre. In linear light:
  ratio        brighter half / darker half (light ratio; 2:1 = one stop)
  key_side     which half of the picture is brighter
  top_bottom   upper / lower part of the subject (light from above vs from below: screens, desk bounce)
  transition   width of the lit -> shadow ramp across the subject (narrow = hard light); only when ratio >= 1.4
  subject_luma / bg_luma / dark_share / point_lights   exposure, separation, low light, visible lamps
  warmth / bg_warmth   Lab b* of subject and lit background (mixed colour temperatures)
  bg_blur      background sharpness / subject sharpness (shallow depth of field)
The set-up table (SETUPS) maps these to a named set-up; add rows there for new lighting styles. Rim light, catchlight
shape and the actual light source are the image readers' call. Platform transcodes crush shadows: hardness and
fill are the least reliable measurements."""
from __future__ import annotations

import math

import cv2
import numpy as np

from .base import FILMED, PHOTOGRAPHIC, Aspect, Field, ShotCtx, field_table, register, seen, weighted_median, weighted_mode

MEASURES = ("ratio", "top_bottom", "transition", "subject_luma", "bg_luma", "dark_share", "warmth", "bg_warmth", "bg_blur", "point_lights")


def _lin(gray: np.ndarray) -> np.ndarray:
    return (gray.astype(np.float32) / 255.0) ** 2.2


def _px(x0: float, y0: float, x1: float, y1: float, w: int, h: int) -> tuple[int, int, int, int]:
    return max(0, int(x0 * w)), max(0, int(y0 * h)), min(w, int(x1 * w)), min(h, int(y1 * h))


def frame_light(bgr: np.ndarray, subj: dict) -> dict | None:
    """Lighting measurements of one frame around its subject."""
    h, w = bgr.shape[:2]
    bx, by, bw, bh = subj["box"]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    lm = subj.get("landmarks")
    if subj["kind"] == "face":  # skin band below the hairline, inside the cheeks; split at the nose
        x0, y0, x1, y1 = _px(bx + 0.12 * bw, by + 0.25 * bh, bx + 0.88 * bw, by + 0.92 * bh, w, h)
        mid = lm[2][0] if lm else bx + bw / 2
        bg_box = (bx - 0.6 * bw, by - 0.4 * bh, bx + 1.6 * bw, 1.0)  # head and shoulders
    else:
        x0, y0, x1, y1 = _px(bx, by, bx + bw, by + bh, w, h)
        mid = bx + bw / 2
        bg_box = (bx - 0.1 * bw, by - 0.1 * bh, bx + 1.1 * bw, by + 1.1 * bh)
    xm = int(mid * w)
    if x1 - x0 < 12 or y1 - y0 < 12 or not (x0 + 4 < xm < x1 - 4):
        return None
    region = gray[y0:y1, x0:x1]
    lmean = float(_lin(gray[y0:y1, x0:xm]).mean()) + 1e-4
    rmean = float(_lin(gray[y0:y1, xm:x1]).mean()) + 1e-4
    ratio = max(lmean, rmean) / min(lmean, rmean)
    key_side = "left" if lmean > rmean else "right"
    top = float(_lin(gray[y0:int(y0 + 0.35 * (y1 - y0)), x0:x1]).mean()) + 1e-4
    bottom = float(_lin(gray[int(y0 + 0.65 * (y1 - y0)):y1, x0:x1]).mean()) + 1e-4
    transition = None
    if ratio >= 1.4:
        prof = _lin(gray[y0 + (y1 - y0) // 4: y0 + 3 * (y1 - y0) // 4, x0:x1]).mean(axis=0)
        k = max(3, (len(prof) // 12) | 1)
        prof = np.convolve(prof, np.ones(k) / k, mode="valid")
        if len(prof) > 8:
            p = prof if key_side == "right" else prof[::-1]  # rise from shadow to lit
            lo, hi = float(np.percentile(p, 10)), float(np.percentile(p, 90))
            if hi - lo > 1e-3:
                transition = max(0, int(np.argmax(p >= lo + 0.8 * (hi - lo))) - int(np.argmax(p >= lo + 0.2 * (hi - lo)))) / len(p)
    mask = np.ones((h, w), bool)
    a0, b0, a1, b1 = _px(*bg_box, w, h)
    mask[b0:b1, a0:a1] = False
    has_bg = mask.mean() > 0.12
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    subj_sharp = float(lap[y0:y1, x0:x1].mean()) + 1e-3
    out = {"ratio": ratio, "key_side": key_side, "top_bottom": top / bottom, "transition": transition,
           "subject_luma": float(np.median(region)), "dark_share": float((gray < 28).mean()),
           "warmth": float(lab[y0:y1, x0:x1, 2].astype(np.float32).mean() - 128),
           "bg_luma": None, "bg_warmth": None, "bg_blur": None, "point_lights": 0}
    if has_bg:
        out["bg_luma"] = float(np.median(gray[mask]))
        out["bg_blur"] = float(lap[mask].mean()) / subj_sharp
        lit_bg = mask & (gray > 50)
        if lit_bg.sum() > 200:
            out["bg_warmth"] = float(lab[..., 2][lit_bg].astype(np.float32).mean() - 128)
        hot = ((gray > 235) & mask).astype(np.uint8)
        n, _, stats, _ = cv2.connectedComponentsWithStats(hot, connectivity=8)
        out["point_lights"] = int(sum(1 for i in range(1, n) if 2 <= stats[i, cv2.CC_STAT_AREA] <= 0.004 * h * w))
    return out


def words(m: dict) -> dict:
    """Plain-language reading of measurements (one shot, or a video's medians)."""
    if not m or m.get("ratio") is None:
        return {}
    r, side = m["ratio"], {"left": "画面左侧", "right": "画面右侧"}.get(m.get("key_side"), "?")
    key = "正面（光从镜头方向来，主体两侧亮度接近）" if r < 1.35 else (f"{side}偏前方" if r < 2.5 else f"{side}（侧光）")
    tb = m.get("top_bottom") or 1.0
    vert = "从下往上（屏幕/桌面反光/低位光源）" if tb < 0.85 else ("从上往下（顶灯/路灯/高位光源）" if tb > 1.35 else "与主体同高")
    tr = m.get("transition")
    quality = (("柔光（几乎没有明暗分界）" if r < 1.35 else "无法判断") if tr is None
               else "柔光（明暗过渡宽）" if tr >= 0.32 else "硬光（明暗分界清楚）" if tr <= 0.16 else "中等柔度")
    warm = m.get("warmth") or 0.0
    ct = "偏暖（约 3000–4000K）" if warm > 14 else ("偏冷（日光/冷白 LED）" if warm < 4 else "中性（约 5000K）")
    blur = m.get("bg_blur")
    depth = None if blur is None else ("背景明显虚化（浅景深/人像模式）" if blur < 0.45 else "背景略虚" if blur < 0.8 else "背景清晰")
    sep = None
    if m.get("bg_luma") is not None and m.get("subject_luma") is not None:
        d = m["subject_luma"] - m["bg_luma"]
        sep = "主体比背景亮" if d > 25 else ("背景比主体亮（逆光/背景灯更亮）" if d < -25 else "主体和背景亮度接近")
    low = (m.get("subject_luma", 255) < 95 and (m.get("dark_share") or 0) > 0.25) or (m.get("bg_luma") is not None and m["bg_luma"] < 35)
    return {"key_direction": key, "vertical": vert, "ratio": f"约 {r:.1f}:1（相差 {math.log2(max(r, 1.0)):.1f} 档）",
            "quality": quality, "color_temp": ct, "depth": depth, "separation": sep, "low_light": bool(low),
            "mixed_color": m.get("bg_warmth") is not None and abs(m["bg_warmth"] - warm) > 10,
            "point_lights": int(m.get("point_lights") or 0)}


# Lighting set-ups, first match wins. `when(m, w)` gets the measurements and their reading.
# gear: per subject kind ("face" = people, "other" = desk / product / hands).
SETUPS = [
    {"key": "ambient_low_light", "name": "夜景/弱光环境光，没有专门补光",
     "when": lambda m, w: w["low_light"],
     "detail": "主光来自环境（路灯、招牌、室内顶灯、屏幕），主体偏暗、反差由环境决定",
     "gear": {"face": ["手机夜景/人像模式，找一盏路灯或招牌做主光，人站在灯斜下方", "想更干净：镜头旁加一个低亮度暖色 LED 小灯（3200K）"],
              "other": ["找一处有台灯/氛围灯的位置，把物品放在灯下", "想更干净：加一盏低亮度 LED 小灯从侧上方照物品"]}},
    {"key": "back_lit", "name": "逆光/背景比主体亮",
     "when": lambda m, w: m.get("bg_luma") is not None and m["bg_luma"] - m.get("subject_luma", 0) > 40,
     "detail": "背景（窗户、天空、亮墙）比主体亮，主体靠反光或曝光补偿",
     "gear": {"face": ["背对窗户坐，或在镜头侧加反光板/补光灯把脸提亮"], "other": ["物品背对窗户，正面放反光板或白卡补光"]}},
    {"key": "soft_front", "name": "正面柔光（平光）",
     "when": lambda m, w: m["ratio"] < 1.35,
     "detail": "大面积光从镜头方向来，主体上几乎没有阴影：面朝窗户、环形灯，或大柔光箱放在镜头后方",
     "gear": {"face": ["环形灯（18 寸）或带柔光罩的 LED 平板灯，放在手机正后方略高处", "或白天面朝窗户，窗户在镜头后面"],
              "other": ["桌面正上方/镜头后方放一盏大柔光灯（或两盏左右对称 45°）", "白色桌面或白卡反光，阴影会更淡"]}},
    {"key": "key_fill", "name": "侧前方主光 + 补光（标准布光）",
     "when": lambda m, w: m["ratio"] < 2.5,
     "detail": "主光在主体一侧斜前方，另一侧有补光或反光板把阴影提亮",
     "gear": {"face": ["主光：LED + 柔光箱/柔光伞，人物 45° 侧前方、略高于眼睛", "补光：另一侧反光板或低亮度小灯", "可选：背后加轮廓灯"],
              "other": ["主光：LED + 柔光箱从桌面一侧 45° 斜上方打", "另一侧白卡/反光板补光", "可选：后方加一盏轮廓灯勾边"]}},
    {"key": "hard_side", "name": "单侧主光，补光很弱（明暗对比强）",
     "when": lambda m, w: True,
     "detail": "主光在一侧，另一侧明显暗下去，接近伦勃朗光/侧光",
     "gear": {"face": ["一盏 LED + 柔光箱放在人物侧面 60–90°", "不补光，或用黑旗压暗另一侧"],
              "other": ["一盏灯从侧面低角度打，制造长影子", "另一侧不补光或用黑卡吸光"]}},
]


def setup_for(m: dict, w: dict, subject_kind: str = "face") -> dict:
    if not w:
        return {}
    s = next(s for s in SETUPS if s["when"](m, w))
    notes = []
    if w["vertical"].startswith("从下往上"):
        notes.append("主体下半部偏亮：可能有来自下方的光（屏幕、桌面反光）")
    if w["mixed_color"]:
        notes.append("主体和背景色温不一致：混合光源（例如暖色灯 + 冷色窗光/屏幕）")
    if w.get("depth"):
        notes.append(w["depth"])
    return {"key": s["key"], "name": s["name"], "detail": s["detail"], "notes": notes,
            "gear": s["gear"]["face" if subject_kind == "face" else "other"]}


class Lighting(Aspect):
    def __init__(self):
        super().__init__(key="lighting", title="打光", shot_fields=[
            Field("location", "场景", "室内|户外；白天|夜晚；具体场景（卧室/办公室/桌面/街边/车内…）", prefer="seen"),
            Field("key_light", "主光", "主光方向与高度（如：画面左前方 45° 略高）及光源（窗户/环形灯/柔光箱/台灯/路灯/屏幕/顶灯）", prefer="seen"),
            Field("light_quality", "软硬", "柔光|硬光|混合，依据（阴影边缘、投影）"),
            Field("fill_light", "补光", "有无补光/反光板，暗部多暗", prefer="seen"),
            Field("rim_light", "轮廓光", "有无轮廓光/背景灯（头发、肩膀、物体边缘的亮边）", prefer="seen"),
            Field("catchlight", "眼神光", "眼神光形状（方形=柔光箱/窗，圆环=环形灯，点状=小灯/路灯，无）；没有人脸写不适用", prefer="seen"),
            Field("practicals", "可见光源", "画面里能看到的光源（台灯、灯带、路灯、招牌、屏幕）", prefer="seen"),
            Field("color_temp", "色温", "暖|中性|冷|混合（主体和背景是否不同色温）"),
            Field("lighting_setup", "布光方案", "一句话布光方案（如：环形灯正面平光 + 暖色背景灯；两盏柔光箱 45° 夹打桌面）", prefer="seen"),
        ], video_fields=[
            Field("lighting_overall", "整体布光", "整片的布光方案：主光、补光、轮廓光、环境光、色温；不同场景分别写", prefer="seen"),
        ])

    def measure(self, ctx: ShotCtx) -> dict:
        per, kinds = [], []
        for f, s in zip(ctx.frames, ctx.subjects):
            if s and s["kind"] != "frame":
                m = frame_light(f, s)
                if m:
                    per.append(m)
                    kinds.append(s["kind"])
        if not per:
            return {}
        out = {"subject": max(set(kinds), key=kinds.count), "frames": len(per)}
        for k in MEASURES:
            vals = [m[k] for m in per if m.get(k) is not None]
            if vals:
                out[k] = round(float(np.median(vals)), 3)
        sides = [m["key_side"] for m in per]
        out["key_side"] = max(set(sides), key=sides.count)
        return out

    def interpret(self, shot: dict) -> dict:
        m = ((shot.get("aspects") or {}).get(self.key) or {}).get("measured") or {}
        w = words(m)
        if not w:
            return {}
        st = setup_for(m, w, m.get("subject", "face"))
        return {**w, "setup": st.get("key"), "setup_name": st.get("name")}

    def summarize(self, shots: list[dict], b: dict) -> dict:
        filmed = [r for r in shots if r["base"]["label"] in FILMED]
        main = [r for r in (filmed or [r for r in shots if r["base"]["label"] in PHOTOGRAPHIC])
                if ((r.get("aspects") or {}).get(self.key) or {}).get("measured", {}).get("ratio") is not None]
        if not main:
            return {}
        ms = [(r["aspects"][self.key]["measured"], r["duration"]) for r in main]
        med = {k: weighted_median([(m.get(k), w) for m, w in ms]) for k in MEASURES}
        med["key_side"], _ = weighted_mode([(m.get("key_side"), w) for m, w in ms])
        kind, _ = weighted_mode([(m.get("subject"), w) for m, w in ms])
        w = words(med)
        st = setup_for(med, w, kind or "face")
        lines = [f"打光：{st['name']}。主光 {w['key_direction']}，{w['vertical']}；光比 {w['ratio']}；{w['quality']}；色温{w['color_temp']}"
                 + (f"；{'；'.join(st['notes'])}" if st.get("notes") else "")]
        if not filmed:
            lines.append("（画面不是实拍：以上是画面里的光影，用于写生成提示词，不是拍摄方案）")
        return {"result": {"setup": st["key"]}, "measured": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in med.items()},
                "words": w, "setup": st, "filmed": bool(filmed), "lines": lines,
                "fields": field_table(self, main, b, {"light_quality": w["quality"], "color_temp": w["color_temp"]})}


register(Lighting())
