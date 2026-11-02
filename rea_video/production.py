"""From per-shot breakdown to a production plan: how the shots would actually be made again.

Edit shots (cut detection) are the facts; making them is organised differently:
  AI shots      -> generation units: consecutive same-scene shots one model call can produce together,
                   sized by the chosen model's limits (duration, multi-shot, references) and routing rules S01-S06
  filmed shots  -> shooting setups: shots grouped by scene (not only adjacent ones) and, inside a scene,
                   by camera setup (shot size + support), so each setup is lit and shot once
  code / screen -> one code composition per contiguous run
  still motion  -> rebuilt locally from one image
"""
from __future__ import annotations

from pathlib import Path

import cv2

from .classify import LABEL_ZH
from .aspects.camera import SUPPORT_ZH
from .plan import Planner, shot_prompts

GEN_LABELS = ("ai_video", "cg_3d", "anim_2d")
SHOOT_LABELS = ("live_action", "stop_motion")
GEN_ZH = {"ai_video": "AI 视频", "cg_3d": "3D 渲染", "anim_2d": "2D 动画"}
CODE_LABELS = ("code_render", "screen_capture")
LOCAL_LABELS = ("still_motion", "static_hold")
SIZE_ORDER = {"远景": 0, "全景": 1, "中景": 2, "近景": 3, "特写": 4}
PRODUCT_KINDS = ("product_photo", "logo")


def _norm(s: str) -> str:
    return s.replace("-", "").replace("_", "").replace(" ", "").lower()


# ---------------------------------------------------------------- scene similarity
def _hist(path: Path):
    im = cv2.imread(str(path))
    if im is None:
        return None
    h = cv2.calcHist([cv2.cvtColor(im, cv2.COLOR_BGR2HSV)], [0, 1, 2], None, [18, 4, 4], [0, 180, 0, 256, 0, 256])
    cv2.normalize(h, h)
    return h


def scene_signatures(shots: list[dict], out: Path) -> dict[int, list]:
    sig = {}
    for r in shots:
        hs = [_hist(out / r["assets"][k]) for k in ("first_frame", "last_frame") if r.get("assets", {}).get(k)]
        sig[r["index"]] = [h for h in hs if h is not None]
    return sig


def scene_distance(a: list, b: list) -> float:
    """Smallest colour-distribution distance between any key frame of two shots (0 = same, 1 = unrelated)."""
    d = [cv2.compareHist(x, y, cv2.HISTCMP_BHATTACHARYYA) for x in a for y in b]
    return float(min(d)) if d else 1.0


# ---------------------------------------------------------------- model choice
def pick_model(planner: Planner, engine: str) -> tuple[dict | None, dict | None]:
    """-> (the user's engine as a capability entry, the model the generation units are planned for).
    Plan for the engine when it can do multi-shot; otherwise for the first live multi-shot model in R11."""
    live = {k: m for k, m in planner.models.items() if m.get("status") not in ("shutdown", "deprecated")}
    eng = next((m for m in live.values() if _norm(m["id"]) == _norm(engine)), None)
    if eng and "video.multi_shot" in eng.get("primitives", []):
        return eng, eng
    best = None
    for mid in (planner.rules.get("R11-recurring-character") or {}).get("prefer", []):
        m = live.get(mid)
        if m and m.get("status") == "ga" and "video.multi_shot" in m.get("primitives", []):
            best = m
            break
    return eng, best or eng


def _limits(m: dict | None) -> dict:
    lim = (m or {}).get("limits") or {}
    d = lim.get("duration_s") or {}
    return {"max": float(d.get("max") or 10), "min": float(d.get("min") or 0), "refs": lim.get("refs") or {},
            "multi_shot": "video.multi_shot" in (m or {}).get("primitives", []),
            "ref_video": "video.reference_video" in (m or {}).get("primitives", []),
            "multi_ref": "video.multi_reference" in (m or {}).get("primitives", []),
            "first_frame": "video.i2v_first_frame" in (m or {}).get("primitives", []),
            "native_audio": bool(((m or {}).get("audio") or {}).get("native"))}


# ---------------------------------------------------------------- generation units
INSERT_LABELS = CODE_LABELS + LOCAL_LABELS  # made separately and cut in during editing


def _runs(shots: list[dict], same_scene, labels: tuple) -> list[list[dict]]:
    """Runs of generated shots that may share one generation: same scene, joined by hard cuts. Shots cut in
    between them (UI, screenshots, stills) do not break a run: the generated shots are produced back to back
    and the inserts are placed in the edit, which is how a talk-plus-UI video is usually made."""
    runs, cur, gap = [], [], False
    for r in shots:
        lab = r["base"]["label"]
        if lab not in labels:
            if lab in INSERT_LABELS and cur:
                gap = True
                continue
            if cur:
                runs.append(cur)
            cur, gap = [], False
            continue
        joinable = cur and same_scene(cur[-1], r) and (gap or r.get("transition_in") == "cut")
        if cur and not joinable:
            runs.append(cur)
            cur = []
        cur.append(r)
        gap = False
    if cur:
        runs.append(cur)
    return runs


def _split_run(run: list[dict], lim: dict, hard: dict, unit_min: float) -> list[list[dict]]:
    """Fewest units first, then avoid multi-shot units past the preferred hard-cut length and units under the
    minimum generation length (dynamic programming over cut points)."""
    if not lim["multi_shot"]:
        return [[r] for r in run]
    cap = min(lim["max"], hard["max"])
    n = len(run)
    best = [0.0] + [float("inf")] * n
    prev = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            seg = run[i:j]
            dur = sum(r["duration"] for r in seg)
            if len(seg) > 1 and dur > cap:
                break
            cost = 1.0 + (0.4 if len(seg) > 1 and dur > hard["prefer"] else 0.0) + (0.2 if dur < unit_min else 0.0)
            if best[i] + cost < best[j]:
                best[j], prev[j] = best[i] + cost, i
    out, j = [], n
    while j > 0:
        out.append(run[prev[j]:j])
        j = prev[j]
    return out[::-1]


def _style_line(shots: list[dict], aspect_ratio: str) -> str:
    """One style sentence for every unit (most common lighting reading across the generated shots, by duration),
    so separately generated units still look like one video."""
    orient = {"9:16": "竖屏 9:16", "16:9": "横屏 16:9", "1:1": "方形 1:1", "3:4": "竖屏 3:4", "4:3": "横屏 4:3"}.get(aspect_ratio, aspect_ratio)
    bits = [orient]
    picks = []
    for key in ("setup_name", "quality", "color_temp", "depth"):
        votes: dict[str, float] = {}
        for r in shots:
            v = (((r.get("aspects") or {}).get("lighting") or {}).get("result") or {}).get(key)
            if v:
                votes[v] = votes.get(v, 0.0) + r["duration"]
        if votes:
            picks.append(max(votes.items(), key=lambda kv: kv[1])[0])
    if picks:
        bits.append("，".join(picks))
    return "；".join(bits)


def _unit(seg: list[dict], k: int, lim: dict, model: dict | None, style: str, cast: dict, prev_unit: dict | None,
          same_scene_prev: bool, unit_min: float) -> dict:
    t0, t1 = seg[0]["start"], seg[-1]["end"]
    dur = round(sum(r["duration"] for r in seg), 3)  # generated length; inserts cut in between are not generated
    ranges: list[list[float]] = []
    for r in seg:
        if ranges and abs(ranges[-1][1] - r["start"]) < 1e-3:
            ranges[-1][1] = r["end"]
        else:
            ranges.append([r["start"], r["end"]])
    contiguous = len(ranges) == 1
    votes: dict[str, float] = {}
    for r in seg:
        votes[r["base"]["label"]] = votes.get(r["base"]["label"], 0.0) + r["duration"]
    label = max(votes.items(), key=lambda kv: kv[1])[0]
    product, people = cast["product"], cast["people"]
    refs, tags = [], {}
    if product:
        tags["product"] = "@图片1"
        refs.append({"tag": "@图片1", "what": "产品实拍图（正面 + 带 Logo 面），用户提供", "user": True})
    if people:
        tags["person"] = f"@图片{len(refs) + 1}"
        refs.append({"tag": tags["person"], "what": "人物参考图（每个角色一张正脸 + 半身），保证跨镜头长相一致", "user": True})
    if lim["ref_video"]:
        tags["video"] = "@视频1"
        span = "、".join(f"{a:.1f}–{z:.1f}s" for a, z in ranges)
        refs.append({"tag": "@视频1", "what": f"原片 {span} 片段" + ("（剪掉中间插入的画面后拼成一段）" if not contiguous else "")
                     + "：参考动作、运镜和剪辑节奏（只作参考，不复制画面）", "user": False})
    cont = None
    if prev_unit and same_scene_prev and seg[0].get("transition_in") == "cut" and abs(prev_unit["ranges"][-1][1] - t0) < 1e-3:
        cont = f"首帧用 {prev_unit['id']} 的尾帧，接上动作（S05）"
    elif lim["first_frame"]:
        cont = f"首帧：参照原片第 {seg[0]['index']} 镜首帧（{seg[0]['assets']['first_frame']}）的构图出图" + ("，产品换成 @图片1" if product else "")
    head = [style]
    if product:
        head.append("产品外观以 @图片1 为准，Logo 和细节保持一致")
    if people:
        head.append(f"人物以 {tags['person']} 为准")
    if "video" in tags:
        head.append("参考 @视频1 的动作、运镜和剪辑节奏")
    if len(seg) > 1:
        head.append("镜头之间硬切")
    lines, lines_en, missing = [], [], []
    acc = 0.0
    for i, r in enumerate(seg, 1):
        p = shot_prompts(r)
        text = p.get("video_prompt") or (r.get("vlm") or r.get("review") or {}).get("description") or ""
        if not text:
            missing.append(r["index"])
            text = "（缺提示词：开 --vlm 或 agent 看图补写）"
        a, z = acc, acc + r["duration"]
        acc = z
        lines.append(f"镜头{i}（{a:.1f}–{z:.1f}s）：{text}" if len(seg) > 1 else text)
        if p.get("video_prompt_en"):
            lines_en.append(f"Shot {i} ({a:.1f}-{z:.1f}s): {p['video_prompt_en']}" if len(seg) > 1 else p["video_prompt_en"])
    parts = 1
    if len(seg) == 1 and dur > lim["max"]:
        parts = int(-(-dur // lim["max"]))
    mode = "multi_shot" if len(seg) > 1 else ("split" if parts > 1 else "single")
    unit = {"id": f"G{k}", "kind": "generate", "label": label, "label_zh": LABEL_ZH[label],
            "shots": [r["index"] for r in seg], "start": round(t0, 3), "end": round(t1, 3), "duration": dur,
            "ranges": [[round(a, 3), round(z, 3)] for a, z in ranges],
            "mode": mode, "calls": parts, "model": (model or {}).get("id"), "model_name": (model or {}).get("name"),
            "generate_s": max(dur, lim["min"], unit_min if len(seg) == 1 else 0), "refs": refs, "continuity": cont,
            "prompt": "。".join(head) + "。\n" + "\n".join(lines),
            "prompt_en": "\n".join(lines_en) if len(lines_en) == len(seg) else "",
            "image_prompt": shot_prompts(seg[0]).get("image_prompt", "")}
    if missing:
        unit["prompt_missing"] = missing
    if parts > 1:
        unit["note"] = f"单镜 {dur:.1f}s 超过 {unit['model_name']} 单次上限 {lim['max']:.0f}s：在动作停顿处切成 {parts} 段，后一段用前一段尾帧作首帧（S02）"
    if unit["generate_s"] > dur + 0.5:
        unit["note"] = f"生成 {unit['generate_s']:.0f}s 后剪到 {dur:.1f}s（太短的镜头单独生成不稳定）"
    if not contiguous:
        unit["note"] = (unit.get("note", "") + "；" if unit.get("note") else "") + \
            "这几镜在成片里被插入画面隔开：生成时连着出，剪辑时再把插入画面放回去"
    if label == "anim_2d" or "anim_2d" in votes:
        unit["alt"] = ("另一条路：2D 动画工程。角色拆层做 Live2D / Spine 骨骼动画，或在 AE 里做；简单的扁平风格动画也可以让 LLM 写 "
                       "Lottie / Canvas 代码。口型、动作要逐帧精确时比视频模型可控（R19）")
    if label == "cg_3d" or "cg_3d" in votes:
        unit["alt"] = ("另一条路：3D 工程。让 LLM 写 Blender 脚本或 Three.js 场景，角色/道具建模一次、按镜头设机位渲染，"
                       "多角度和角色一致性最好，改文案和镜头也只是改代码（R08）")
    return unit


# ---------------------------------------------------------------- shooting setups
def _setups(shots: list[dict], sig: dict, thr: float, b: dict) -> list[dict]:
    filmed = [r for r in shots if r["base"]["label"] in SHOOT_LABELS]
    scenes: list[list[dict]] = []
    for r in filmed:
        d = [(min(scene_distance(sig[r["index"]], sig[m["index"]]) for m in sc), i) for i, sc in enumerate(scenes)]
        best = min(d) if d else (1.0, -1)
        if best[0] <= thr:
            scenes[best[1]].append(r)
        else:
            scenes.append([r])
    out = []
    for si, sc in enumerate(scenes, 1):
        groups: dict[tuple, list[dict]] = {}
        for r in sc:
            a = r.get("aspects") or {}
            # group on normalised values: free-text answers ("近景（手持物品特写）") would split one setup into many
            raw = str(((a.get("framing") or {}).get("result") or {}).get("shot_size") or "")
            size = next((k for k in SIZE_ORDER if raw.startswith(k)), raw or "—")
            cam = (a.get("camera") or {}).get("result") or {}
            support = SUPPORT_ZH.get(cam.get("support"), cam.get("support_zh") or "—")
            groups.setdefault((size, support), []).append(r)
        light = (((sc[0].get("aspects") or {}).get("lighting") or {}).get("result") or {})
        desc = (sc[0].get("vlm") or sc[0].get("review") or {}).get("description", "")
        setups = []
        for (size, support), rs in sorted(groups.items(), key=lambda kv: SIZE_ORDER.get(kv[0][0], 9)):
            setups.append({"shot_size": size, "support": support, "shots": [r["index"] for r in rs],
                           "screen_s": round(sum(r["duration"] for r in rs), 2),
                           "take_s": round(sum(r["duration"] for r in rs) + 2.0 * len(rs), 1),
                           "moves": sorted({((r.get("aspects") or {}).get("camera") or {}).get("result", {}).get("move") or "固定" for r in rs})})
        post = [dict(e, shot=r["index"]) for r in sc for e in (r.get("ai_edits") or [])]
        out.append({"id": f"S{si}", "kind": "shoot", "shots": [r["index"] for r in sc], "post": post,
                    "stop_motion": [r["index"] for r in sc if r["base"]["label"] == "stop_motion"],
                    "screen_s": round(sum(r["duration"] for r in sc), 2), "description": desc,
                    "lighting": "，".join(x for x in (light.get("setup_name"), light.get("key_direction"), light.get("ratio"), light.get("color_temp")) if x),
                    "setups": setups})
    return out


# ---------------------------------------------------------------- whole plan
def production_plan(b: dict, out: Path, engine: str = "seedance2.0") -> dict:
    shots = b["shots"]
    planner = Planner()
    seg = planner.routing.get("segmentation", {}).get("defaults", {})
    hard = seg.get("multishot_hardcut_s") or {"prefer": 8, "max": 10}
    thr = float(seg.get("same_scene_distance", 0.5))
    unit_min = float((seg.get("generation_unit_s") or {}).get("min", 4))
    sig = scene_signatures(shots, out)
    def same_scene(a: dict, c: dict) -> bool:
        return scene_distance(sig[a["index"]], sig[c["index"]]) <= thr

    same_prev = {r["index"]: same_scene(p, r) for p, r in zip(shots, shots[1:])}
    eng, model = pick_model(planner, engine)
    lim = _limits(model)
    gen = [r for r in shots if r["base"]["label"] in GEN_LABELS]
    # product / people that recur across the video are referenced in every unit, not only where they were detected
    cast = {"product": any(it.get("kind") in PRODUCT_KINDS and (it.get("real_material") or it in m.get("references", []))
                           for r in gen for m in [r.get("material") or {}] for it in m.get("items", []) + m.get("references", [])),
            "people": sum(r["duration"] for r in gen if (r.get("faces") or 0) > 0.3) >= 1.0}
    style = _style_line(gen, b["source"].get("aspect_ratio", "")) if gen else ""
    units: list[dict] = []
    for run in _runs(shots, same_scene, GEN_LABELS):
        for segm in _split_run(run, lim, hard, unit_min):
            prev = units[-1] if units else None
            units.append(_unit(segm, len(units) + 1, lim, model, style, cast, prev, same_prev.get(segm[0]["index"], False), unit_min))
    units.sort(key=lambda u: u["start"])
    for k, u in enumerate(units, 1):
        u["id"] = f"G{k}"
    plan: dict = {"model": (model or {}).get("id"), "model_name": (model or {}).get("name"),
                  "model_limits": {k: lim[k] for k in ("max", "min", "multi_shot", "ref_video", "native_audio")},
                  "rules": {"hardcut_s": hard, "same_scene_distance": thr}, "units": units}
    gen_shots = sum(len(u["shots"]) for u in units)
    if units:
        calls = sum(u["calls"] for u in units)
        plan["calls"] = calls
        plan["gen_shots"] = gen_shots
        if eng and model and eng["id"] != model["id"]:
            plan["engine_note"] = (f"默认引擎 {eng['name']} 不支持一次多镜头，用它要逐镜生成 {gen_shots} 次；"
                                   f"这里按 {model['name']} 规划（单次 ≤{lim['max']:.0f}s、可一次出多镜头），共 {calls} 次")
        voice = ((b.get("audio") or {}).get("voice") or {}).get("present")
        if lim["native_audio"]:
            plan["audio_note"] = "模型可以同时生成动作音效和环境声" + ("；口播建议单独 TTS 再混进去，方便改文案和卡时间" if voice else "")
    plan["shoot"] = _setups(shots, sig, thr, b)
    code, cur = [], []
    for r in shots + [None]:
        if r is not None and r["base"]["label"] in CODE_LABELS:
            cur.append(r)
            continue
        if cur:
            code.append({"id": f"段{len(code) + 1}", "kind": "code", "shots": [x["index"] for x in cur],
                         "start": cur[0]["start"], "end": cur[-1]["end"], "duration": round(cur[-1]["end"] - cur[0]["start"], 3),
                         "screen_capture": [x["index"] for x in cur if x["base"]["label"] == "screen_capture"],
                         "text_tracks": sum(len((x.get("text") or {}).get("tracks", [])) for x in cur)})
            cur = []
    plan["code"] = code
    plan["local"] = [{"shot": r["index"], "label_zh": r["base"]["label_zh"], "duration": r["duration"]}
                     for r in shots if r["base"]["label"] in LOCAL_LABELS]
    plan["avatar"] = [r["index"] for r in shots if r["base"]["label"] == "avatar"]
    plan["_labels"] = {r["index"]: r["base"]["label"] for r in shots}
    plan["summary"] = summary_line(plan, b)
    del plan["_labels"]
    return plan


def summary_line(plan: dict, b: dict) -> str:
    bits = []
    if plan["units"]:
        kinds: dict[str, int] = {}
        for u in plan["units"]:
            for i in u["shots"]:
                lab = plan["_labels"][i]
                kinds[lab] = kinds.get(lab, 0) + 1
        what = " + ".join(f"{GEN_ZH.get(k, k)} {v} 镜" for k, v in kinds.items())
        bits.append(f"{what} → {len(plan['units'])} 个生成单元、{plan['calls']} 次 {plan['model_name']} 调用")
    if plan["shoot"]:
        n_set = sum(len(s["setups"]) for s in plan["shoot"])
        n_shot = sum(len(s["shots"]) for s in plan["shoot"])
        n_stop = sum(len(s.get("stop_motion", [])) for s in plan["shoot"])
        what = "实拍" if not n_stop else ("定格动画" if n_stop == n_shot else "实拍 / 定格")
        bits.append(f"{what} {n_shot} 镜 → {len(plan['shoot'])} 个场景、{n_set} 个机位")
        n_post = len({e["shot"] for s in plan["shoot"] for e in s.get("post", [])})
        if n_post:
            bits.append(f"其中 {n_post} 镜拍完还要 AI 加工（" + "、".join(dict.fromkeys(e["kind_zh"] for s in plan["shoot"] for e in s["post"])) + "）")
    if plan["code"]:
        n = sum(len(c["shots"]) for c in plan["code"])
        bits.append(f"代码渲染/录屏 {n} 镜 → 1 个代码工程" + (f"（{len(plan['code'])} 段）" if len(plan["code"]) > 1 else ""))
    if plan["local"]:
        bits.append(f"静图推拉/静帧 {len(plan['local'])} 镜 → 本地重建")
    if plan["avatar"]:
        bits.append(f"数字人 {len(plan['avatar'])} 镜 → TTS + 数字人驱动")
    return "；".join(bits)


def report_md(plan: dict) -> list[str]:
    if not plan:
        return []
    L = ["", "## 复刻方案", "", f"**{plan['summary']}**", ""]
    if plan.get("engine_note"):
        L += [f"- {plan['engine_note']}"]
    if plan["units"]:
        lim = plan["model_limits"]
        L += [f"- 分组规则：同场景、硬切相连的 AI 镜头合并为一次生成（S01）；单次 ≤{lim['max']:.0f}s；"
              f"单元内连续硬切尽量 ≤{plan['rules']['hardcut_s']['prefer']}s、最多 {plan['rules']['hardcut_s']['max']}s（S06）；"
              "在叠化/淡入淡出和换场景处切开（S03）"]
        if plan.get("audio_note"):
            L.append(f"- 声音：{plan['audio_note']}")
        L += ["", "### 生成单元", "", "| 单元 | 镜头 | 时间 | 时长 | 方式 | 衔接 |", "|---|---|---|---|---|---|"]
        mode_zh = {"multi_shot": "一次多镜头", "single": "单镜", "split": "超长切段"}
        for u in plan["units"]:
            sh = f"#{u['shots'][0]}" + (f"–#{u['shots'][-1]}" if len(u["shots"]) > 1 else "")
            sh = "、".join(f"#{i}" for i in u["shots"]) if len(u["ranges"]) > 1 else sh
            when = "、".join(f"{a:.1f}–{z:.1f}s" for a, z in u["ranges"])
            L.append(f"| {u['id']} | {sh} | {when} | {u['duration']:.1f}s | {mode_zh[u['mode']]}"
                     f"{'（' + str(u['calls']) + ' 次）' if u['calls'] > 1 else ''} | {u['continuity'] or '—'} |")
        for u in plan["units"]:
            L += ["", f"**{u['id']} · {u['label_zh']} · 镜头 {'、'.join('#' + str(i) for i in u['shots'])} · {u['duration']:.1f}s**", ""]
            if u["refs"]:
                L.append("- 参考素材：" + "；".join(f"`{r['tag']}` {r['what']}" for r in u["refs"]))
            if u.get("image_prompt"):
                L.append(f"- 首帧图像提示词：{u['image_prompt']}")
            for k in ("note", "alt"):
                if u.get(k):
                    L.append(f"- {u[k]}")
            if u.get("prompt_missing"):
                L.append(f"- 镜头 {'、'.join('#' + str(i) for i in u['prompt_missing'])} 缺提示词")
            L += ["", "```text", u["prompt"], "```"]
            if u.get("prompt_en"):
                L += ["", "<details><summary>English prompt</summary>", "", "```text", u["prompt_en"], "```", "", "</details>"]
    if plan["shoot"]:
        L += ["", "### 拍摄场次", "", "同一场景的镜头集中拍完再换场景；场景内先拍大景别再拍特写，不用重新布光。每条前后多留约 1s 余量。", ""]
        for s in plan["shoot"]:
            L.append(f"**{s['id']}**：镜头 {'、'.join('#' + str(i) for i in s['shots'])}（成片 {s['screen_s']}s）"
                     + (f"；{s['description']}" if s.get("description") else ""))
            if s.get("lighting"):
                L.append(f"- 布光：{s['lighting']}")
            for st in s["setups"]:
                L.append(f"- 机位 {st['shot_size']} · {st['support']} · {'/'.join(st['moves'])}：镜头 {'、'.join('#' + str(i) for i in st['shots'])}，"
                         f"成片 {st['screen_s']}s，建议拍 {st['take_s']}s")
            if s.get("stop_motion"):
                L.append(f"- 定格动画（{'、'.join('#' + str(i) for i in s['stop_motion'])}）：相机固定，每秒约 12 张逐帧拍；用定格 App 的洋葱皮对位，"
                         "灯光全程不动（避免闪烁）")
            for e in s.get("post", []):
                L.append(f"- 拍完后 #{e['shot']} 要 AI 加工：{e['kind_zh']}" + (f"（{e['evidence']}）" if e.get("evidence") else "")
                         + (f" → {e['how']}" if e.get("how") else ""))
            L.append("")
    if plan["code"]:
        L += ["", "### 代码合成", "", "一个 Remotion/HTML 工程，按文字轨和版式重建（文字、界面 100% 可控，改文案只改数据）；"
              "可以让 LLM 直接写这个工程。", ""]
        for c in plan["code"]:
            L.append(f"- {c['id']}：镜头 {'、'.join('#' + str(i) for i in c['shots'])}（{c['start']:.1f}–{c['end']:.1f}s）"
                     + (f"；其中 {'、'.join('#' + str(i) for i in c['screen_capture'])} 用真实界面录屏" if c["screen_capture"] else ""))
    if plan["local"]:
        L += ["", "### 本地重建", "", "- " + "；".join(f"#{x['shot']} {x['label_zh']} {x['duration']:.1f}s" for x in plan["local"])
              + "：一张图 + 测得的缩放/平移参数，ffmpeg 直接出"]
    return L
