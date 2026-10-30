from rea_video.classify import deterministic, video_context

PHOTO = {"noise_sigma": 0.5, "temporal_noise": 1.2, "flat_ratio": 0.02, "palette95": 220, "axis_edge_ratio": 0.2,
         "dup_ratio": 0.0, "moving_pair_ratio": 1.0, "zero_pixel_ratio": 0.1, "camera": "smooth_move", "saturation": 0.3}
PROV_WECHAT = {"hints": {"platform": ["WeChat transcode"], "generator": [], "camera": []}}


def _shots(dup):
    return [{"duration": 3.0, "features": dict(PHOTO, dup_ratio=dup)} for _ in range(3)]


def test_video_context_native30_only_without_pulldown():
    assert video_context(_shots(0.0), PROV_WECHAT, 30.0)["native30"] is True
    assert video_context(_shots(0.2), PROV_WECHAT, 30.0)["native30"] is False
    assert video_context(_shots(0.0), PROV_WECHAT, 24.0)["native30"] is False


def test_transcoded_phone_footage_is_not_called_ai():
    ctx = video_context(_shots(0.0), PROV_WECHAT, 30.0)
    probs, ev, facts = deterministic(PHOTO, provenance=PROV_WECHAT, fps=30.0, ctx=ctx)
    assert probs["live_action"] > probs["ai_video"]
    assert facts["transcode_degraded"]


def test_transcoded_ai_with_pulldown_stays_ai():
    f = dict(PHOTO, dup_ratio=0.2, dup_period=5, dup_regularity=0.6)
    ctx = video_context(_shots(0.2), PROV_WECHAT, 30.0)
    probs, _, _ = deterministic(f, provenance=PROV_WECHAT, fps=30.0, ctx=ctx)
    assert probs["ai_video"] > probs["live_action"]


def test_gray_ui_is_not_3d():
    ui = {"noise_sigma": 0.1, "temporal_noise": 0.2, "flat_ratio": 0.65, "palette95": 3, "axis_edge_ratio": 0.27,
          "dup_ratio": 0.2, "moving_pair_ratio": 1.0, "zero_pixel_ratio": 0.9, "saturation": 0.006}
    probs, _, facts = deterministic(ui)
    assert facts["ui_gray"] > 0.5 and probs["code_render"] > probs["cg_3d"]


def test_transcoded_ai_or_live_without_decisive_evidence_is_flagged():
    from rea_video.pipeline import _ai_or_live_doubt

    t = {"transcode_degraded": True, "pulldown": "24→30"}
    assert _ai_or_live_doubt("ai_video", t, {}, {}) and _ai_or_live_doubt("live_action", t, {}, {"native30": False})
    assert _ai_or_live_doubt("ai_video", t, {"aigc": {"label": "1"}}, {}) is None          # AIGC label settles it
    assert _ai_or_live_doubt("live_action", {"transcode_degraded": True}, {}, {"native30": True}) is None  # phone camera
    assert _ai_or_live_doubt("ai_video", {"pulldown": "24→30"}, {}, {}) is None            # not transcoded: noise evidence works
    assert _ai_or_live_doubt("cg_3d", t, {}, {}) is None
