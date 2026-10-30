"""Framing: shot size, where the subject sits, camera angle.

Shot size is measured from the face height when there is a face (reliable), else estimated from the in-focus
subject's area (rough: a hand on a desk and a product close-up can both fill the frame); camera angle comes from
the image readers."""
from __future__ import annotations

import numpy as np

from .base import FILMED, PHOTOGRAPHIC, Aspect, Field, ShotCtx, field_table, register, seen, weighted_mode

# face height / frame height
FACE_SIZES = [(0.40, "大特写"), (0.25, "特写"), (0.14, "近景"), (0.08, "中景"), (0.04, "全景"), (0.0, "远景")]
# in-focus subject area / frame area
AREA_SIZES = [(0.45, "特写"), (0.22, "近景"), (0.08, "中景"), (0.0, "全景")]


def size_from(table, v: float | None) -> str | None:
    if v is None:
        return None
    return next(name for th, name in table if v >= th)


def shot_size(face_h: float | None) -> str | None:
    return size_from(FACE_SIZES, face_h)


class Framing(Aspect):
    def __init__(self):
        super().__init__(key="framing", title="取景", shot_fields=[
            Field("shot_size", "景别", "特写|近景|中景|全景|远景（按画面主体，人物按头肩/半身/全身）"),
            Field("camera_angle", "机位角度", "平视|略俯拍|俯拍|顶拍（正上方俯拍桌面）|略仰拍|仰拍", prefer="seen"),
            Field("composition", "构图", "主体位置与留白（居中/三分线/对称/框中框），竖屏还是横屏", prefer="seen"),
        ])

    def measure(self, ctx: ShotCtx) -> dict:
        subs = [s for s in ctx.subjects if s]
        if not subs:
            return {}
        kinds = [s["kind"] for s in subs]
        kind = max(set(kinds), key=kinds.count)
        use = [s for s in subs if s["kind"] == kind]
        hs = [s["box"][3] for s in use]
        cx = float(np.median([s["box"][0] + s["box"][2] / 2 for s in use]))
        cy = float(np.median([s["box"][1] + s["box"][3] / 2 for s in use]))
        area = float(np.median([s["area"] for s in use]))
        out = {"subject": kind, "subject_share": round(len(use) / max(1, len(ctx.subjects)), 3),
               "center_x": round(cx, 3), "center_y": round(cy, 3), "area": round(area, 3)}
        if kind == "face":
            fh = float(np.median(hs))
            out.update(face_h=round(fh, 3), face_h_spread=round(float(np.std(hs)), 3), shot_size=shot_size(fh),
                       headroom=round(float(np.median([s["box"][1] for s in use])), 3))
        elif kind == "focus":
            out["shot_size"] = size_from(AREA_SIZES, area)
            out["shot_size_rough"] = True
        out["position"] = "居中" if abs(cx - 0.5) < 0.12 else ("偏左" if cx < 0.5 else "偏右")
        return out

    def interpret(self, shot: dict) -> dict:
        m = ((shot.get("aspects") or {}).get(self.key) or {}).get("measured") or {}
        s = seen(shot, self.key, "shot_size")
        size = m.get("shot_size") if m.get("subject") == "face" else (s.get("agent") or s.get("vlm") or m.get("shot_size"))
        ang = seen(shot, self.key, "camera_angle")
        return {"shot_size": size, "camera_angle": ang.get("agent") or ang.get("vlm"), "position": m.get("position"),
                "subject": m.get("subject")}

    def summarize(self, shots: list[dict], b: dict) -> dict:
        main = [r for r in shots if r["base"]["label"] in FILMED] or [r for r in shots if r["base"]["label"] in PHOTOGRAPHIC]
        if not main:
            return {}
        tot = sum(r["duration"] for r in main)
        res = {r["index"]: (r.get("aspects") or {}).get(self.key, {}).get("result") or {} for r in main}
        size, sizes = weighted_mode([(res[r["index"]].get("shot_size"), r["duration"]) for r in main])
        angle, angles = weighted_mode([(res[r["index"]].get("camera_angle"), r["duration"]) for r in main])
        subj, subjects = weighted_mode([(res[r["index"]].get("subject"), r["duration"]) for r in main])
        lines = [f"取景：景别 " + ("、".join(f"{k} {v:.0%}" for k, v in list(sizes.items())[:3] if v >= 0.01) or "未测出")
                 + (f"；机位角度 {angle}" if angle else "")
                 + {"face": "；主体是人脸（按脸高算景别）", "focus": "；主体是画面清晰区域（没有人脸，景别为估算）",
                    "frame": "；没找到明确主体"}.get(subj, "")]
        return {"result": {"shot_size": size, "camera_angle": angle, "subject": subj},
                "shot_sizes": sizes, "angles": angles, "subjects": subjects, "seconds": round(tot, 2), "lines": lines,
                "fields": field_table(self, main, b, {"shot_size": size})}


register(Framing())
