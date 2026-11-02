"""New coverage: 2D animation / stop motion, AI edits on filmed shots, colour grade, more lighting set-ups."""
import cv2
import numpy as np

from rea_video.aspects.grade import frame_grade, words
from rea_video.aspects.lighting import setup_for, words as lwords
from rea_video.classify import LABELS, decide, fuse
from rea_video.pipeline import _vlm_gate
from rea_video.plan import Planner
from rea_video.review import _clean


def _gradient(shadow_bgr=(0, 0, 0), high_bgr=(0, 0, 0), black=0):
    g = np.tile(np.linspace(black, 255, 256, dtype=np.float32), (120, 1))
    img = np.stack([g, g, g], -1)
    t = (g / 255.0)[..., None]
    img = img + (1 - t) * np.array(shadow_bgr, np.float32) + t * np.array(high_bgr, np.float32)
    return np.clip(img, 0, 255).astype(np.uint8)


def test_grade_looks():
    assert words(frame_grade(_gradient()))["look"] == "黑白"
    teal_orange = _gradient(shadow_bgr=(40, 20, -10), high_bgr=(-30, 0, 25))
    assert words(frame_grade(teal_orange))["look"].startswith("青橙")
    lifted = frame_grade(_gradient(black=60))
    assert words(lifted)["black"].startswith("黑位抬高")


def test_grade_ignores_coloured_objects():
    img = _gradient(high_bgr=(-10, 0, 10))
    img[:, :60] = (180, 60, 10)  # a saturated blue object in the dark part
    assert not words(frame_grade(img))["look"].startswith("青橙")


def _m(**kw):
    m = {"ratio": 1.2, "key_side": "left", "top_bottom": 1.0, "transition": None, "subject_luma": 150, "bg_luma": 140,
         "dark_share": 0.05, "warmth": 8, "bg_warmth": 8, "bg_blur": 0.6, "point_lights": 0, "bg_std": 30}
    m.update(kw)
    return m


def test_lighting_new_setups():
    m = _m(ratio=3.0, transition=0.1)
    assert setup_for(m, lwords(m), "face", "户外 白天 街边")["key"] == "outdoor_sun"
    assert setup_for(m, lwords(m), "face", "室内 卧室")["key"] != "outdoor_sun"
    m = _m(bg_luma=200, subject_luma=150)
    assert setup_for(m, lwords(m), "face", "户外 海边")["key"] == "outdoor_backlit"
    m = _m(bg_luma=10, subject_luma=130, dark_share=0.6, ratio=2.0)
    assert setup_for(m, lwords(m), "face")["key"] == "low_key"
    m = _m()
    assert setup_for(m, lwords(m), "face", None, {"mean_l": 80, "dark_share": 0.0})["key"] == "high_key"
    assert setup_for(m, lwords(m), "face", None, {"mean_l": 50, "bright_sat": 0.7, "hue_bins": 3})["key"] == "colored_ambient"
    m = _m(bg_std=5)
    assert setup_for(m, lwords(m), "other")["key"] == "product_still"
    # the koubo night street keeps its call
    m = _m(ratio=1.83, transition=0.33, subject_luma=71, bg_luma=23, dark_share=0.55)
    assert setup_for(m, lwords(m), "face", None, {"mean_l": 20, "bright_sat": 0.1, "hue_bins": 0})["key"] == "ambient_low_light"


def test_unmeasured_labels_come_from_readers():
    assert "anim_2d" in LABELS and "stop_motion" in LABELS
    det = {k: 0.02 for k in LABELS}
    det.update({"code_render": 0.5, "cg_3d": 0.3})
    rec = {"deterministic": {"probs": det, "facts": {}}}
    seen = {"anim_2d": 0.85, "code_render": 0.15}
    assert _vlm_gate(rec, seen, {}) == seen  # the tools cannot back a label they cannot score
    assert decide(fuse(det, seen, "dynamic"))[0] == "anim_2d"


def test_ai_edits_become_post_steps():
    ans, err = _clean({"label_probs": {"live_action": 1}, "ai_edits": [{"kind": "face_swap", "evidence": "下巴边缘闪烁"}, {"kind": "x"}]})
    assert err is None and ans["ai_edits"] == [{"kind": "face_swap", "evidence": "下巴边缘闪烁"}]
    steps = Planner().edit_steps(ans["ai_edits"] + [{"kind": "lip_sync"}])
    assert [s["kind_zh"] for s in steps] == ["换脸", "对口型/改台词"]
    assert steps[0]["rule"] == "R12-owned-footage-edit" and "（" in steps[0]["how"]
