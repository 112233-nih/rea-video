import subprocess
import wave

import numpy as np
import pytest

from rea_video.audio import analyze_audio

SR = 22050


def _write_av(tmp_path, y: np.ndarray, name: str):
    wav = tmp_path / f"{name}.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    mp4 = tmp_path / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=160x90:d={len(y) / SR:.2f}",
                    "-i", str(wav), "-shortest", "-c:v", "libx264", "-c:a", "aac", str(mp4)], check=True)
    return mp4


def _music(dur=12.0, bpm=120.0):
    t = np.arange(int(dur * SR)) / SR
    y = 0.08 * sum(np.sin(2 * np.pi * f * t) for f in (261.63, 329.63, 392.0, 523.25))  # C major chord bed
    rng = np.random.default_rng(0)
    for k in np.arange(0, dur, 60 / bpm):  # kick + hat on every beat
        i = int(k * SR)
        n = int(0.08 * SR)
        env = np.exp(-np.arange(n) / (0.015 * SR))
        y[i:i + n] += 0.5 * env * np.sin(2 * np.pi * 70 * np.arange(n) / SR)
        y[i:i + n // 3] += 0.15 * rng.normal(size=n // 3) * env[: n // 3]
    return y


@pytest.fixture(scope="module")
def music_mp4(tmp_path_factory):
    return _write_av(tmp_path_factory.mktemp("a"), _music(), "music")


def test_music_tempo_key(music_mp4):
    a = analyze_audio(str(music_mp4))
    m = a["music"]
    assert m["present"] and not a["voice"]["present"]
    cands = [m["tempo_bpm"]] + m["tempo_alternatives"]
    assert any(abs(c - 120) < 4 for c in cands), cands
    assert m["key"]["tonic"] == "C" and m["key"]["mode"] == "major"


def test_cut_on_beat(music_mp4):
    cuts = [2.0, 4.0, 6.0, 8.0, 10.0]  # on the 120 BPM grid
    a = analyze_audio(str(music_mp4), cut_times=cuts)
    assert a["cuts_on_beat_ratio"] >= 0.8 and a["beat_synced_edit"]


def test_isolated_sfx_in_silence(tmp_path):
    y = np.zeros(int(6 * SR))
    for t0 in (1.0, 2.5, 4.0):  # three "dings" in an otherwise quiet track
        n = int(0.3 * SR)
        tt = np.arange(n) / SR
        y[int(t0 * SR):int(t0 * SR) + n] += 0.6 * np.exp(-tt / 0.08) * np.sin(2 * np.pi * 1800 * tt)
    y += 0.002 * np.random.default_rng(1).normal(size=len(y))
    a = analyze_audio(str(_write_av(tmp_path, y, "sfx")))
    ts = [e["t"] for e in a["sfx"]["events"]]
    assert all(any(abs(t - t0) < 0.08 for t in ts) for t0 in (1.0, 2.5, 4.0)), ts
    assert not a["music"]["present"]
