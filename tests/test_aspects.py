import numpy as np

from rea_video import aspects
from rea_video.aspects import REGISTRY, check, clean_answers, finalize, measure_shot, prompt_block, review_template
from rea_video.aspects.base import seen
from rea_video.aspects.framing import shot_size
from rea_video.aspects.lighting import frame_light, setup_for, words
from rea_video.aspects.subject import salient_box
from rea_video.classify import deterministic, lowlight_photo
from rea_video.pipeline import _vlm_gate

FACE_BOX = [0.25, 0.25, 0.5, 0.3]
LANDMARKS = [[0.4, 0.33], [0.6, 0.33], [0.5, 0.4], [0.42, 0.48], [0.58, 0.48]]


def _frame(left: int, right: int, bg: int = 120, box=FACE_BOX, h: int = 640, w: int = 360, warm: int = 0, texture: bool = False):
    rng = np.random.default_rng(0)
    img = np.full((h, w, 3), bg, np.float32)
    x0, y0, x1, y1 = int(box[0] * w), int(box[1] * h), int((box[0] + box[2]) * w), int((box[1] + box[3]) * h)
    xm = (x0 + x1) // 2
    img[y0:y1, x0:xm] = left
    img[y0:y1, xm:x1] = right
    if texture:  # fine detail inside the subject = what the camera focused on
        img[y0:y1, x0:x1] += rng.normal(0, 25, (y1 - y0, x1 - x0, 3))
    img[..., 0] -= warm
    img[..., 2] += warm
    img += rng.normal(0, 2, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def _face(box=FACE_BOX):
    return {"box": box, "area": box[2] * box[3], "score": 0.9, "landmarks": LANDMARKS}


def test_registry_order_and_prompt():
    keys = [a.key for a in REGISTRY]
    assert keys[:3] == ["framing", "camera", "lighting"] and keys.index("editing") < keys.index("format")
    p = prompt_block()
    assert '"lighting"' in p and "{{" in p  # braces doubled for str.format
    from rea_video.vlm import SHOT_PROMPT, WHOLE_PROMPT
    assert '"key_light"' in SHOT_PROMPT.format(hints="") and '"gear_guess"' in WHOLE_PROMPT.format(shots="")


def test_review_template_and_clean():
    t = review_template()
    assert "lighting" in t and "key_light" in t["lighting"]
    ans = clean_answers({"lighting": {"key_light": " 左前方窗户 ", "bogus": "x", "rim_light": ""}, "nope": {"a": 1}})
    assert ans == {"lighting": {"key_light": "左前方窗户"}}


def test_shot_size_thresholds():
    assert shot_size(0.5) == "大特写" and shot_size(0.3) == "特写" and shot_size(0.18) == "近景" and shot_size(None) is None


def test_face_side_light():
    m = frame_light(_frame(200, 90), {"kind": "face", "box": FACE_BOX, "landmarks": LANDMARKS})
    assert m["key_side"] == "left" and m["ratio"] > 2.5
    assert m["transition"] is not None and m["transition"] < 0.2
    w = words(m)
    assert "画面左侧" in w["key_direction"]
    assert setup_for(m, w)["key"] == "hard_side"


def test_flat_front_warm():
    m = frame_light(_frame(170, 168, warm=25), {"kind": "face", "box": FACE_BOX, "landmarks": LANDMARKS})
    w = words(m)
    assert w["key_direction"].startswith("正面") and w["color_temp"].startswith("偏暖")
    assert setup_for(m, w)["key"] == "soft_front"


def test_low_light_and_back_light_setups():
    m = {"ratio": 1.8, "key_side": "left", "top_bottom": 1.5, "transition": 0.25, "subject_luma": 70, "bg_luma": 18,
         "dark_share": 0.55, "warmth": 18, "bg_warmth": 5, "bg_blur": 0.6, "point_lights": 3}
    s = setup_for(m, words(m))
    assert s["key"] == "ambient_low_light" and any("混合光源" in n for n in s["notes"])
    m2 = {**m, "subject_luma": 90, "bg_luma": 200, "dark_share": 0.0}
    assert setup_for(m2, words(m2))["key"] == "back_lit"


def test_desk_shot_without_face():
    """No face: the in-focus region is the subject; lighting and framing still measured, gear for objects."""
    box = [0.2, 0.35, 0.6, 0.3]
    img = _frame(190, 110, bg=90, box=box, texture=True)
    sb = salient_box(img)
    assert sb is not None and abs((sb[0] + sb[2] / 2) - 0.5) < 0.15  # found the textured block
    feats = {"camera": "static", "zoom_per_s": 0.0, "pan_px_per_s": 0.0}
    asp = measure_shot([img] * 6, {"presence": 0.0, "frames": [{"i": i, "face": None} for i in range(6)]}, feats, 3.0)
    assert asp["framing"]["measured"]["subject"] == "focus"
    assert asp["lighting"]["measured"]["subject"] == "focus" and asp["lighting"]["measured"]["ratio"] > 1.4
    assert asp["camera"]["measured"]["support"] == "fixed"
    m = asp["lighting"]["measured"]
    st = setup_for(m, words(m), "focus")
    from rea_video.aspects.lighting import SETUPS
    assert st["gear"] == next(s for s in SETUPS if s["key"] == st["key"])["gear"]["other"]  # object gear, not people gear


def _rec(i, start, dur, face_h, sig, label="live_action", support="handheld"):
    return {"index": i, "start": start, "end": start + dur, "duration": dur, "base": {"label": label},
            "features": {"camera": "handheld_like"},
            "aspects": {"framing": {"measured": {"subject": "face", "face_h": face_h, "shot_size": shot_size(face_h), "position": "居中"}},
                        "camera": {"measured": {"support": support}},
                        "lighting": {"measured": {"subject": "face", "ratio": 1.2, "key_side": "left", "top_bottom": 1.0,
                                                  "subject_luma": 150, "bg_luma": 140, "dark_share": 0.0, "warmth": 10}},
                        "editing": {"measured": {"bg_signature": sig}}}}


def test_video_summary_and_check():
    a, b2 = [100.0] * 60, [20.0] * 60
    shots = [_rec(1, 0, 4, 0.3, a), _rec(2, 4, 3, 0.31, a), _rec(3, 7, 3, 0.42, a),
             {**_rec(4, 10, 2, 0.3, a, label="screen_capture"), "aspects": {}}, _rec(5, 12, 4, 0.3, b2)]
    b = {"shots": shots, "audio": {"voice": {"present": True, "segments": [[0, 3.9], [4.2, 6.9], [7.3, 16]]}}}
    finalize(b)
    A = b["aspects"]
    assert A["editing"]["jump_cuts"] == 1 and A["editing"]["punch_ins"] == 1 and A["editing"]["positions"] == 2
    assert A["camera"]["result"]["support"] == "handheld"
    assert A["format"]["result"]["format"] == "talking_head"
    assert A["lighting"]["result"]["setup"] == "soft_front"
    assert b["shooting_plan"]["gear"]
    assert check({"lighting": {"setup": ["soft_front", "key_fill"]}, "framing": {"shot_size": "特写"}}, b) == {
        "lighting.setup": True, "framing.shot_size": True}
    assert any(l.startswith("打光") for l in aspects.summary_lines(b))
    assert aspects.report_md(b) and aspects.report_html(b)


def test_seen_reads_new_and_legacy_answers():
    r = {"vlm": {"cinematography": {"key_light": "路灯"}}, "review": {"aspects": {"lighting": {"key_light": "窗户"}}}}
    assert seen(r, "lighting", "key_light") == {"agent": "窗户", "vlm": "路灯"}


def _night_features() -> dict:
    # dim street shot: crushed shadows, small palette, a big face (eval/koubo.yaml night_street #31)
    return {"noise_sigma": 0.04, "flat_ratio": 0.59, "palette95": 19, "axis_edge_ratio": 0.37, "saturation": 0.7, "luma": 23,
            "dup_ratio": 0.01, "dup_period": 1, "dup_regularity": 0.5, "moving_pair_ratio": 0.79, "zero_pixel_ratio": 0.7,
            "temporal_noise": 0.46, "pair_mad_p90": 3.8, "start_end_mad": 23, "camera": "static_with_subject_motion",
            "dark_ratio": 0.71, "dark_std": 0.4, "dark_flat": 0.55, "flat_lit": 0.1, "face_presence": 1.0, "face_area": 0.12}


def test_dim_presenter_not_rendered():
    f = _night_features()
    assert lowlight_photo(f, 1.0)
    probs, _, facts = deterministic(f, faces=1.0, ctx={"transcoded": True, "platform": ["Douyin/TikTok"], "native30": True})
    assert facts["graphic_score"] < 0.3 and max(probs, key=probs.get) in ("live_action", "ai_video", "avatar")
    probs2, _, facts2 = deterministic({**f, "face_presence": 0.0, "face_area": 0.0}, faces=0.0, ctx={"transcoded": True})
    assert facts2["graphic_score"] > 0.6 and max(probs2, key=probs2.get) in ("cg_3d", "code_render")


def test_dark_render_on_exact_black_is_not_lowlight():
    assert not lowlight_photo({**_night_features(), "dark_std": 0.0, "dark_flat": 0.95}, 1.0)


def test_vlm_person_exception():
    rec = {"deterministic": {"probs": {"cg_3d": 0.6, "code_render": 0.2, "live_action": 0.1, "ai_video": 0.1},
                             "facts": {"big_face": True, "transcode_degraded": True}}}
    v = {"live_action": 1.0}
    assert _vlm_gate(rec, v, {"native30": True}) == v
    rec["deterministic"]["facts"]["big_face"] = False
    assert _vlm_gate(rec, v, {"native30": True}) is None


def test_person_exception_stays_out_of_ai_live_axis():
    # eval holdout2 dual_role_realcat: tools say AI then live; Gemini says avatar -> keep the tools' axis call
    rec = {"deterministic": {"probs": {"ai_video": 0.64, "live_action": 0.2, "avatar": 0.05, "cg_3d": 0.11},
                             "facts": {"big_face": True, "lowlight": True, "face_area": 0.07}}}
    assert _vlm_gate(rec, {"avatar": 1.0}, {"native30": False}) is None
