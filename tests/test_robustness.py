"""Edge cases from the 2026-11 review: low fps, damaged files, malformed VLM answers, engine, exports."""
import subprocess
from pathlib import Path

import pytest

from rea_video.media import extract_frame, video_info
from rea_video.packaging import _ts
from rea_video.probe import _scan_bytes, provenance
from rea_video.review import _tiles_cmd
from rea_video.vlm import VLMClient, VLMConfig, normalize_shot_result, normalize_whole


def _ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def fps10(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("v") / "fps10.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10", "-t", "2", "-pix_fmt", "yuv420p", str(p))
    return p


def test_last_frame_past_final_pts_falls_back(fps10, tmp_path):
    # 10 fps: end - 0.08 s lies after the last frame's timestamp; used to abort decompose
    out = extract_frame(fps10, 1.95, tmp_path / "last.jpg", width=160)
    assert out.is_file() and out.stat().st_size > 0


def test_byte_scan_skips_mdat(tmp_path):
    p = tmp_path / "x.mp4"
    _ff("-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5", "-t", "1", str(p))
    raw = p.read_bytes()
    i = raw.index(b"mdat") + 8
    p.write_bytes(raw[:i] + b"xxx Veo xxx" + raw[i + 11:])  # random-looking "veo" inside the frame data
    assert b"Veo" not in _scan_bytes(p)
    assert not provenance(video_info(p)).get("hints", {}).get("generator")


def test_normalize_whole_survives_bad_shapes():
    w = normalize_whole({"narration": "none", "music": ["x"], "sfx": [{"t": "00:03"}, {"t": None}, "pop"],
                         "segments": [{"start": "1:02.5", "end": "bad"}], "summary": 3})
    assert "narration" not in w and "music" not in w
    assert w["sfx"] == [{"t": 3.0}, {}]
    assert w["segments"] == [{"start": 62.5}]
    assert w["summary"] == "3"
    assert normalize_whole("oops") is None


def test_merge_audio_with_bad_whole_answer():
    from rea_video.audio import merge_audio_with_vlm

    w = normalize_whole({"narration": "none", "sfx": [{"t": "00:03"}, {"t": None}]})
    merge_audio_with_vlm({"has_audio": True, "sfx": {"events": [{"t": 3.1}]}}, w)


def test_normalize_shot_result_bad_types():
    r = normalize_shot_result({"label_probs": [0.5, 0.5], "evidence": "skin texture"})
    assert r["evidence"] == ["skin texture"] and not any(r["label_probs"].values())  # unusable probs = "no opinion"


def test_empty_choices_falls_back_to_next_model(monkeypatch):
    c = VLMClient(VLMConfig("https://example.invalid/v1", "k", ["m1", "m2"]))
    calls = []

    def fake_urlopen(req, timeout):
        import io
        import json

        model = json.loads(req.data)["model"]
        calls.append(model)
        body = {"choices": []} if model == "m1" else {"choices": [{"message": {"content": '{"ok": 1}'}}]}

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R(json.dumps(body).encode())

    monkeypatch.setattr("rea_video.vlm.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("rea_video.vlm.time.sleep", lambda s: None)
    assert c.ask_json("p", [])["ok"] == 1
    assert calls[-1] == "m2"


def test_srt_timestamp_carries_into_seconds():
    assert _ts(3.9996) == "00:00:04,000"
    assert _ts(59.9997) == "00:01:00,000"
    assert _ts(1.5, ".") == "00:00:01.500"


def test_tiles_cmd_quotes_paths():
    assert "'/a/my video.mp4'" in _tiles_cmd("/a/my video.mp4", 1, 2, 0.2, "out/c.jpg")
