import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _photo_like(w: int, h: int, seed: int) -> np.ndarray:
    """Photographic-looking still: smooth multi-scale texture + sensor-like noise."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.float32)
    for scale, amp in ((8, 90), (32, 40), (128, 20)):
        small = rng.random((max(2, h // scale), max(2, w // scale), 3)).astype(np.float32)
        img += cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC) * amp
    img = cv2.GaussianBlur(img, (0, 0), 1.2) + rng.normal(0, 3.0, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)


def _write(frames, path: Path, fps: int = 30) -> Path:
    h, w = frames[0].shape[:2]
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps),
                          "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", str(path)], stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    p.wait()
    return path


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    w, h, fps = 360, 640, 30
    out = {}
    # 1) static photo hold, 2 s
    still = _photo_like(w, h, 1)
    out["static"] = _write([still] * (2 * fps), d / "static.mp4")
    # 2) Ken Burns: zoom in 4%/s on another photo, 3 s
    base = _photo_like(int(w * 1.3), int(h * 1.3), 2)
    frames = []
    for i in range(3 * fps):
        s = 1.0 + 0.04 * i / fps
        M = cv2.getRotationMatrix2D((base.shape[1] / 2, base.shape[0] / 2), 0, s)
        warped = cv2.warpAffine(base, M, (base.shape[1], base.shape[0]), flags=cv2.INTER_CUBIC)
        y0, x0 = (base.shape[0] - h) // 2, (base.shape[1] - w) // 2
        frames.append(np.ascontiguousarray(warped[y0:y0 + h, x0:x0 + w]))
    out["kenburns"] = _write(frames, d / "kenburns.mp4")
    # 3) code-render style: flat background, a card sliding in, text-like bars, 3 s
    frames = []
    for i in range(3 * fps):
        f = np.full((h, w, 3), (59, 41, 30), np.uint8)
        x = int(min(40, -200 + i * 12))
        cv2.rectangle(f, (x, 200), (x + 280, 360), (245, 245, 245), -1)
        for k in range(4):
            cv2.rectangle(f, (x + 20, 220 + k * 30), (x + 20 + 200 - k * 30, 236 + k * 30), (60, 60, 60), -1)
        cv2.putText(f, "REA VIDEO", (40, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (80, 200, 250), 3, cv2.LINE_8)
        frames.append(f)
    out["code"] = _write(frames, d / "code.mp4")
    # 4) concatenation with known cuts at 2.0 and 5.0 s
    cat = d / "concat.txt"
    cat.write_text("".join(f"file '{out[k]}'\n" for k in ("static", "kenburns", "code")))
    out["edit"] = d / "edit.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(cat), "-c", "copy", str(out["edit"])], check=True)
    return out


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    """Never read or write the real ~/.rea-video during tests."""
    monkeypatch.setenv("REA_VIDEO_HOME", str(tmp_path / "rea_home"))
