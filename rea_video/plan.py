"""Turn a classified shot into a replication plan: which capability rule applies, which
models are candidates, and how capsule-cinema (or local ffmpeg) would execute it."""
from __future__ import annotations

from pathlib import Path

import yaml

from .i18n import LangDict, T, join, semi

CAP = Path(__file__).resolve().parent / "capabilities"

# base label -> (routing rule, executor, capsule generation type)
LABEL_ROUTE = {
    "ai_video": ("R07-product-hero", "capsule_cinema", "first_last_frame"),
    "live_action": ("R07-product-hero", "capsule_cinema", "image_to_video"),
    "avatar": ("R03-talking-head", "capsule_cinema", "image_to_video"),
    "cg_3d": ("R08-product-multiview", "capsule_cinema", "first_last_frame"),
    "code_render": ("R01-code-graphics", "code_render_spec", None),
    "screen_capture": ("R02-screen-demo", "code_render_spec", None),
    "still_motion": ("R17-still-motion", "local_ffmpeg", None),
    "static_hold": ("R18-static-hold", "local_ffmpeg", None),
    "anim_2d": ("R19-2d-animation", "capsule_cinema", "first_last_frame"),
    "stop_motion": ("R20-stop-motion", "capsule_cinema", "image_to_video"),
}
NOTE = LangDict({
    "ai_video": "原片为 AI 生成：取原镜头首尾帧做首尾帧生成，可高度还原构图与动作；换主体时先用图像模型改首帧",
    "live_action": "原片为实拍：按拍摄方案（景别/机位/打光）重新拍摄；没有拍摄条件时才用 AI 图生视频近似（以首帧为参考）",
    "avatar": "数字人口播：先克隆/合成配音，再用数字人模型由人像 + 音频驱动",
    "cg_3d": "3D 渲染：可用首尾帧 AI 生成近似；需要精确多角度时走 3D 建模 + Blender 渲染",
    "code_render": "代码渲染：用 Remotion/HTML 按文字轨与版式重建，100% 可控，不需要生成模型",
    "screen_capture": "录屏/截图：用真实界面录屏或网页截图重建",
    "still_motion": "静态图推拉：用一张图 + 测得的缩放/平移参数在本地 ffmpeg 精确复刻",
    "static_hold": "静帧：一张图定格即可",
    "anim_2d": "2D 动画：先出角色设定图定画风，再首尾帧生成；要精确口型和动作时做 AE / Live2D / Spine 工程",
    "stop_motion": "定格动画：首选真的拍（手机固定 + 定格 App，每秒约 12 张）；AI 生成只能近似顿挫感",
}, en={
    "ai_video": "Source is AI-generated: use the shot's first and last frames for first/last-frame generation to closely match composition and motion; to change the subject, edit the first frame with an image model first",
    "live_action": "Source is live action: reshoot per the shooting plan (shot size / camera setup / lighting); only fall back to AI image-to-video (first frame as reference) when a shoot is not possible",
    "avatar": "Digital-avatar talking head: clone or synthesize the voice first, then drive an avatar model from a portrait + the audio",
    "cg_3d": "3D render: AI first/last-frame generation gives an approximation; for precise multiple angles, model in 3D and render in Blender",
    "code_render": "Code render: rebuild in Remotion / HTML from the text tracks and layout, 100% controllable, no generative model needed",
    "screen_capture": "Screen recording / screenshot: rebuild from a real screen recording or web screenshot",
    "still_motion": "Still-image move: one image + the measured zoom / pan parameters, reproduced exactly with local ffmpeg",
    "static_hold": "Static frame: one held image is enough",
    "anim_2d": "2D animation: make a character sheet to lock the style, then first/last-frame generation; for precise lip sync and motion, build an AE / Live2D / Spine project",
    "stop_motion": "Stop motion: shooting for real is the first choice (phone locked off + a stop-motion app, about 12 frames per second); AI generation only approximates the stepped feel",
})
# AI edits on filmed footage: what was done, which routing rule rebuilds it, how in one line
AI_EDITS = {
    "face_swap": ("换脸", "R12-owned-footage-edit", "拍好后用视频编辑模型换人，保留动作"),
    "lip_sync": ("对口型/改台词", "R13-redub", "拍好后配新台词，再用口型重配模型改嘴型"),
    "restyle": ("整段风格化重绘", "R12-owned-footage-edit", "拍好后用视频编辑模型按参考风格重绘，保留动作和构图"),
    "outfit_swap": ("AI 换装", "R05-garment", "先出试穿图确认，再用视频模型或编辑模型换装"),
    "bg_replace": ("抠像换背景", "R12-owned-footage-edit", "绿幕拍摄后抠像合成；没有绿幕时用视频编辑模型换背景"),
    "object_edit": ("加/删/换物体", "R12-owned-footage-edit", "拍好后用视频编辑模型改画面里的物体"),
    "beautify": ("美颜/瘦脸", None, "剪映、美颜相机等的美颜功能即可，不需要生成模型"),
}
AI_EDITS_EN = {  # (what was done, how) in English, same keys as AI_EDITS
    "face_swap": ("face swap", "after the shoot, swap the person with a video-editing model, keeping the motion"),
    "lip_sync": ("lip sync / new lines", "after the shoot, record the new lines, then redo the mouth with a lip-sync model"),
    "restyle": ("full stylized repaint", "after the shoot, repaint with a video-editing model in the reference style, keeping motion and composition"),
    "outfit_swap": ("AI outfit swap", "make a try-on still to approve first, then swap the outfit with a video or editing model"),
    "bg_replace": ("keyed background replacement", "shoot on green screen and key / composite; without a green screen, replace the background with a video-editing model"),
    "object_edit": ("object added / removed / replaced", "after the shoot, change the object in frame with a video-editing model"),
    "beautify": ("beauty filter / face slimming", "the beauty tools in CapCut, beauty camera apps etc. are enough, no generative model needed"),
}
CAPSULE_ENGINES = {"seedance2.0": (4, 15), "minimax-h3": (4, 15)}


def shot_prompts(shot: dict) -> dict:
    """Reverse-engineered prompts for one shot: the agent's review wins over the VLM (it is the later, checked pass)."""
    out = {}
    for src in ((shot.get("vlm") or {}).get("replication") or {}, (shot.get("review") or {}).get("replication") or {}):
        out.update({k: v for k, v in src.items() if k in ("image_prompt", "video_prompt", "video_prompt_en") and v})
    return out


def _load(name: str):
    with (CAP / name).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


class Planner:
    def __init__(self) -> None:
        self.routing = _load("routing.yaml")
        self.rules = {r["id"]: r for r in self.routing["rules"]}
        self.models = {}
        for f in sorted((CAP / "models").glob("*.yaml")):
            for m in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("models", []):
                self.models[m["id"]] = m

    def edit_steps(self, edits: list | None) -> list[dict]:
        """Image readers' list of AI edits on a filmed shot -> post-production steps with candidate models."""
        out = []
        for e in edits or []:
            if not isinstance(e, dict) or e.get("kind") not in AI_EDITS or any(x["kind"] == e["kind"] for x in out):
                continue
            zh, rule_id, how = AI_EDITS[e["kind"]]
            zh_en, how_en = AI_EDITS_EN[e["kind"]]
            names = [self.models[m]["name"] for m in (self.rules.get(rule_id) or {}).get("prefer", [])
                     if m in self.models and self.models[m]["status"] != "shutdown"][:3]
            out.append({"kind": e["kind"], "kind_zh": T(zh, zh_en), "evidence": str(e.get("evidence", ""))[:120], "rule": rule_id,
                        "how": T(how, how_en) + (T("（{n}）", " ({n})", n=" / ".join(names)) if names else "")})
        return out

    def plan_shot(self, label: str, shot: dict, video: dict, engine: str = "seedance2.0") -> dict:
        rule_id, executor, gen_type = LABEL_ROUTE.get(label, ("R14-ambient-broll", "capsule_cinema", "text_to_video"))
        rule = self.rules.get(rule_id, {})
        candidates = [{"id": m, "name": self.models[m]["name"], "status": self.models[m]["status"]}
                      for m in rule.get("prefer", []) if m in self.models and self.models[m]["status"] != "shutdown"]
        plan = {"label": label, "rule": rule_id, "route": rule.get("route", []), "candidates": candidates,
                "verify": rule.get("verify", []), "executor": executor, "note": NOTE.get(label, "")}
        dur = float(shot["duration"])
        mat = shot.get("material") or {}
        user_assets = []
        for it in mat.get("items", []):
            if it.get("real_material"):
                where = T("整个画面", "whole frame") if it.get("region") == "full" else (
                    T("画面局部 x{:.0%} y{:.0%} 宽{:.0%} 高{:.0%}", "part of the frame x{:.0%} y{:.0%} w{:.0%} h{:.0%}").format(*it["bbox"])
                    if it.get("bbox") else T("画面局部", "part of the frame"))
                user_assets.append({"kind": it["kind"], "kind_zh": it.get("kind_zh", it["kind"]), "where": where,
                                    "description": it.get("description", ""), "reference": shot.get("assets", {}).get("first_frame")})
        if user_assets:
            plan["user_assets"] = user_assets
            plan["note"] += semi() + T("需要用户提供真实素材：", "real material the user must supply: ") \
                + join(T("{k}（{w}）", "{k} ({w})", k=a["kind_zh"], w=a["where"]) for a in user_assets)
        refs = mat.get("references") or []
        if refs:
            plan["references"] = refs
            plan["note"] += semi() + T("生成时作参考：", "references for generation: ") + join(dict.fromkeys(r["kind_zh"] for r in refs))
        if executor == "capsule_cinema":
            lo, hi = CAPSULE_ENGINES.get(engine, (4, 15))
            gen_dur = int(min(hi, max(lo, round(dur + 0.49))))
            rep = shot_prompts(shot)
            prompt = rep.get("video_prompt") or (shot.get("vlm") or shot.get("review") or {}).get("description") or ""
            plan["capsule"] = {
                "engine": engine, "generation_type": gen_type, "duration": gen_dur,
                "trim_to": round(dur, 3), "aspect_ratio": video.get("aspect_ratio", "9:16"),
                "prompt": prompt, "prompt_en": rep.get("video_prompt_en", ""), "image_prompt": rep.get("image_prompt", ""),
                "needs_split": dur > hi,
            }
            if not prompt:
                plan["capsule"]["prompt_missing"] = T("未运行 VLM，缺少视频提示词；请用 --vlm 重新拆解或手动补写",
                                                      "VLM was not run, so there is no video prompt; re-run the breakdown with --vlm or write one by hand")
        if executor == "local_ffmpeg":
            m = shot.get("features", {})
            plan["local"] = {"zoom_per_s": m.get("zoom_per_s", 0.0), "pan_px_per_s": m.get("pan_px_per_s", 0.0),
                             "pan_dir_deg": m.get("pan_dir_deg", 0.0), "analysis_width": 480, "duration": round(dur, 3)}
        elif executor == "code_render_spec":
            plan["spec"] = {"text_layers": [l for l in shot.get("layers", []) if l["kind"] in ("subtitle", "title", "label")],
                            "beats": shot.get("beats", []), "duration": round(dur, 3)}
        return plan
