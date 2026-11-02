"""Production plan: AI shots grouped into generation units, filmed shots into shooting setups."""
from pathlib import Path

import cv2
import numpy as np

from rea_video.plan import Planner
from rea_video.production import _limits, _split_run, pick_model, production_plan

KITCHEN = (40, 90, 160)
STUDIO = (200, 60, 30)


def _shot(i, start, dur, label, color, out: Path, trans="cut", product=False, prompt=None, size="中景"):
    for k in ("first", "last"):
        img = np.full((160, 90, 3), color, np.uint8)
        cv2.circle(img, (45, 80), 20 + 5 * (k == "last"), (255, 255, 255), -1)
        cv2.imwrite(str(out / f"s{i}_{k}.jpg"), img)
    items = [{"kind": "product_photo", "real_material": True}] if product else []
    return {"index": i, "id": f"shot_{i:03d}", "start": start, "end": start + dur, "duration": dur, "transition_in": trans,
            "assets": {"first_frame": f"s{i}_first.jpg", "last_frame": f"s{i}_last.jpg"},
            "base": {"label": label, "label_zh": label}, "material": {"items": items}, "faces": 0.0,
            "vlm": {"replication": {"video_prompt": prompt or f"镜头 {i} 的动作", "video_prompt_en": f"shot {i}"}},
            "aspects": {"framing": {"result": {"shot_size": size}}, "camera": {"result": {"support_zh": "手持"}}}}


def _b(shots):
    return {"shots": shots, "source": {"aspect_ratio": "9:16"}, "audio": {"voice": {"present": True}}}


def test_engine_without_multishot_falls_back():
    eng, model = pick_model(Planner(), "seedance2.0")
    assert eng["id"] == "seedance-2.0"
    assert "video.multi_shot" in model["primitives"] and model["id"] != eng["id"]


def test_split_respects_hardcut_cap():
    lim = {"max": 30.0, "multi_shot": True}
    hard = {"prefer": 8, "max": 10}
    run = [{"duration": d} for d in (1.8, 1.0, 0.9, 2.5, 2.8, 5.5, 4.3, 2.2)]
    units = _split_run(run, lim, hard, 4.0)
    assert all(sum(r["duration"] for r in u) <= 10 for u in units if len(u) > 1)
    assert len(units) == 3
    assert [len(u) for u in _split_run(run, {"max": 15.0, "multi_shot": False}, hard, 4.0)] == [1] * 8


def test_units_scene_product_and_continuity(tmp_path):
    shots = [_shot(1, 0.0, 2.0, "ai_video", KITCHEN, tmp_path, product=True),
             _shot(2, 2.0, 2.0, "ai_video", KITCHEN, tmp_path),
             _shot(3, 4.0, 3.0, "ai_video", KITCHEN, tmp_path),
             _shot(4, 7.0, 3.0, "ai_video", STUDIO, tmp_path),           # new scene -> new unit
             _shot(5, 10.0, 2.0, "ai_video", STUDIO, tmp_path, trans="dissolve"),  # transition -> new unit
             _shot(6, 12.0, 40.0, "ai_video", STUDIO, tmp_path)]         # longer than the model allows
    plan = production_plan(_b(shots), tmp_path)
    units = plan["units"]
    assert [u["shots"] for u in units] == [[1, 2, 3], [4], [5], [6]]
    assert units[0]["mode"] == "multi_shot" and "镜头2（2.0–4.0s）" in units[0]["prompt"]
    assert all(any(r["tag"] == "@图片1" for r in u["refs"]) for u in units)  # product referenced everywhere
    assert units[1]["continuity"].startswith("首帧：")   # scene changed: no tail-frame hand-over
    assert units[2]["continuity"].startswith("首帧：")   # dissolve: own unit, not a continuation
    assert units[3]["mode"] == "split" and units[3]["calls"] == 2
    assert plan["calls"] == 5 and plan["engine_note"]


def test_shooting_setups_group_non_adjacent(tmp_path):
    shots = [_shot(1, 0.0, 2.0, "live_action", KITCHEN, tmp_path, size="中景"),
             _shot(2, 2.0, 2.0, "live_action", STUDIO, tmp_path, size="近景"),
             _shot(3, 4.0, 2.0, "live_action", KITCHEN, tmp_path, size="特写"),
             _shot(4, 6.0, 2.0, "live_action", KITCHEN, tmp_path, size="中景")]
    plan = production_plan(_b(shots), tmp_path)
    assert plan["units"] == []
    scenes = {tuple(s["shots"]) for s in plan["shoot"]}
    assert scenes == {(1, 3, 4), (2,)}
    kitchen = next(s for s in plan["shoot"] if s["shots"] == [1, 3, 4])
    assert [st["shot_size"] for st in kitchen["setups"]] == ["中景", "特写"]  # wide before close
    assert kitchen["setups"][0]["shots"] == [1, 4]


def test_limits_reads_capabilities():
    m = Planner().models["kling-3.0"]
    lim = _limits(m)
    assert lim["max"] == 15 and lim["min"] == 3 and lim["multi_shot"]


def test_review_answers_carry_prompts():
    from rea_video.plan import shot_prompts
    from rea_video.review import _clean

    ans, err = _clean({"label_probs": {"ai_video": 1}, "replication": {"video_prompt": "手倒牛奶", "image_prompt": " ", "x": "y"}})
    assert err is None and ans["replication"] == {"video_prompt": "手倒牛奶"}
    shot = {"vlm": {"replication": {"video_prompt": "VLM 版", "video_prompt_en": "en"}}, "review": ans}
    assert shot_prompts(shot) == {"video_prompt": "手倒牛奶", "video_prompt_en": "en"}  # agent wins, VLM fills gaps


def test_inserts_do_not_split_a_generation(tmp_path):
    # talk shots in one scene, UI inserts cut in between: generate the talk shots back to back
    shots = [_shot(1, 0.0, 1.5, "code_render", STUDIO, tmp_path),
             _shot(2, 1.5, 2.0, "ai_video", KITCHEN, tmp_path),
             _shot(3, 3.5, 2.0, "code_render", STUDIO, tmp_path),
             _shot(4, 5.5, 1.5, "ai_video", KITCHEN, tmp_path),
             _shot(5, 7.0, 2.0, "screen_capture", STUDIO, tmp_path),
             _shot(6, 9.0, 2.5, "ai_video", KITCHEN, tmp_path)]
    plan = production_plan(_b(shots), tmp_path)
    (u,) = plan["units"]
    assert u["shots"] == [2, 4, 6] and u["duration"] == 6.0 and len(u["ranges"]) == 3
    assert "镜头2（2.0–3.5s）" in u["prompt"]  # generation-relative times
    assert "1 个代码工程（3 段）" in plan["summary"]
