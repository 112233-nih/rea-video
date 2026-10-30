from rea_video.sound_source import classify_sound_sources


def _b(music=True, voice=True, whole=None, meta=None, clip=None, labels=("ai_video",) * 4, ending_curve=None, sfx=None):
    shots = [{"base": {"label": l}, "duration": 5.0} for l in labels]
    ec = ending_curve or [0.0] * 40
    return {
        "source": {"duration": 20.0, "path": "x.mp4"}, "shots": shots, "provenance": {},
        "audio": {"has_audio": True, "energy_curve": ec,
                  "music": {"present": music, "ratio": 0.95 if music else 0, "start": 0.0, "end": 20.0},
                  "voice": {"present": voice, "ratio": 0.6 if voice else 0}},
        "whole_video_vlm": whole or {}, "platform_meta": meta or {}, "per_clip_audio": clip or {},
        "audio_plan": {"sfx_merged": sfx or []},
    }


def test_mv_with_lyrics_about_content_is_ai():
    w = {"format": "MV", "music": {"present": True, "has_vocals": True, "lyrics_match_content": True}, "narration": {"type": "none"}}
    r = classify_sound_sources(_b(whole=w))
    assert r["format_context"]["music_mv"] and r["music"]["source"] == "ai_generated"


def test_ordinary_bgm_hard_cut_is_library():
    w = {"format": "知识讲解", "music": {"present": True, "has_vocals": False}, "narration": {"type": "tts"}}
    r = classify_sound_sources(_b(whole=w, ending_curve=[0.0] * 40))
    assert r["music"]["leaning"] == "existing_library" and r["music"]["ending"] == "hard_cut"


def test_platform_music_title_wins():
    meta = {"music": {"title": "某热门歌", "author": "某歌手", "is_original": False, "popular": True}}
    r = classify_sound_sources(_b(meta=meta))
    assert r["music"]["leaning"] == "popular_song"


def test_native_clip_audio_means_ai_sfx_and_voice():
    clip = {"ratio": 2.4, "resets_at_cuts": True, "at_cuts_db": 20.0, "within_shots_db": 8.0}
    r = classify_sound_sources(_b(music=False, clip=clip))
    assert r["sfx"]["leaning"] == "ai_native"
    assert r["voice"]["leaning"] == "ai_video_native"


def test_editor_sfx_synced_to_cuts():
    sfx = [{"t": 1.0, "sync_with": "cut"}, {"t": 2.0, "sync_with": "text_pop"}, {"t": 3.0, "sync_with": "none"}]
    r = classify_sound_sources(_b(sfx=sfx, labels=("code_render",) * 4))
    assert r["sfx"]["leaning"] == "library_editor"


def test_sung_vocals_without_vlm_lean_ai_but_tts_narration_does_not():
    b = _b()
    b["audio"]["voice"].update({"style": "singing", "sung": True, "sustained_note_ratio": 0.44})
    r = classify_sound_sources(b)
    assert r["format_context"]["music_mv_maybe"] and r["music"]["leaning"] == "ai_generated"
    b = _b()
    b["audio"]["voice"].update({"style": "speech", "sung": False, "sustained_note_ratio": 0.15})
    r = classify_sound_sources(b)
    assert not r["format_context"]["music_mv_maybe"] and r["music"]["leaning"] == "existing_library"
