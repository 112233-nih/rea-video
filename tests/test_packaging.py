"""Packaging layer: subtitle timeline + style, 花字, stickers / GIFs, SFX alignment, exports."""
import cv2
import numpy as np
import pytest

from conftest import _photo_like, _write
from rea_video import packaging as P
from rea_video.media import video_info

W, H, FPS = 360, 640, 30


def _outlined(img, text, org, scale, color, thick, outline=(0, 0, 0)):
    # the outline is the text stamped around the glyph (OpenCV 5 ignores thickness for anti-aliased text)
    r = max(2, int(round(3 * scale)))
    for dx in (-r, 0, r):
        for dy in (-r, 0, r):
            if dx or dy:
                cv2.putText(img, text, (org[0] + dx, org[1] + dy), cv2.FONT_HERSHEY_DUPLEX, scale, outline, thick, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_DUPLEX, scale, color, thick, cv2.LINE_AA)


def _emoji(frame_no: int) -> np.ndarray:
    """A 2-frame looping 'GIF': a yellow face whose mouth opens / closes every 0.2 s, with a white sticker rim."""
    s = np.zeros((96, 96, 3), np.uint8)
    cv2.circle(s, (48, 48), 46, (255, 255, 255), -1)
    cv2.circle(s, (48, 48), 40, (40, 210, 250), -1)
    cv2.circle(s, (34, 38), 6, (30, 30, 30), -1)
    cv2.circle(s, (62, 38), 6, (30, 30, 30), -1)
    if (frame_no // 6) % 2:
        cv2.ellipse(s, (48, 64), (16, 12), 0, 0, 360, (30, 30, 160), -1)
    else:
        cv2.line(s, (32, 64), (64, 64), (30, 30, 30), 4)
    return s


def _paste(f, s, x, y):
    mask = s.sum(axis=2) > 0
    f[y:y + s.shape[0], x:x + s.shape[1]][mask] = s[mask]


@pytest.fixture(scope="module")
def packed(tmp_path_factory):
    d = tmp_path_factory.mktemp("pack")
    rng = np.random.default_rng(3)
    shot_a = np.clip(_photo_like(W + 120, H, 11).astype(np.int16) + 70, 0, 255).astype(np.uint8)  # light enough for a black outline
    shot_b = np.clip(_photo_like(W + 120, H, 12).astype(np.int16) + 70, 0, 255).astype(np.uint8)
    frames = []
    for i in range(6 * FPS):
        t = i / FPS
        src = shot_a if t < 3.0 else shot_b
        off = int(40 * (t % 3.0) / 3.0)  # slow pan: live-ish background, never still
        f = np.ascontiguousarray(src[:, off:off + W]).copy()
        f = np.clip(f.astype(np.int16) + rng.normal(0, 2, f.shape).astype(np.int16), 0, 255).astype(np.uint8)
        # subtitles: two lines in a fixed bottom lane, white with a black outline
        if 0.5 <= t < 2.0:
            _outlined(f, "HELLO WORLD", (70, 560), 0.9, (255, 255, 255), 2)
        elif 2.0 <= t < 3.5:
            _outlined(f, "SECOND LINE", (70, 560), 0.9, (255, 255, 255), 2)
        elif 3.5 <= t < 5.5:
            _outlined(f, "THIRD ONE", (90, 560), 0.9, (255, 255, 255), 2)
        # looping GIF sticker, appears and leaves mid-shot
        if 1.0 <= t < 2.4:
            _paste(f, _emoji(i), 230, 90)
        # 花字: big yellow word that pops in (scales up over 0.2 s)
        if 4.0 <= t < 5.2:
            k = min(1.0, 0.4 + 0.6 * (t - 4.0) / 0.2)
            _outlined(f, "WOW!", (int(120 - 30 * (k - 1)), 300), 2.2 * k, (0, 230, 255), 5)
        frames.append(f)
    path = _write(frames, d / "pack.mp4", fps=FPS)
    info = video_info(path)
    return P.analyze(path, info, cuts=[3.0], out_dir=d, ocr=True), d


def _ocr_ok():
    from rea_video.overlay import get_ocr
    return get_ocr() is not None


@pytest.mark.skipif(not _ocr_ok(), reason="rapidocr not installed")
def test_subtitle_timeline(packed):
    pack, _ = packed
    subs = [c for c in pack["text_cues"] if c["kind"] == "subtitle"]
    texts = [P._norm(c["text"]) for c in subs]
    assert any("helloworld" in t for t in texts) and any("secondline" in t for t in texts)
    hello = next(c for c in subs if "helloworld" in P._norm(c["text"]))
    assert abs(hello["t0"] - 0.5) <= 0.25 and abs(hello["t1"] - 2.0) <= 0.25
    st = hello["style"]
    assert st.get("fill_name") == "白" and st.get("stroke_name") == "黑"
    srt = P.to_srt(pack["text_cues"])
    assert "HELLOWORLD" in srt.upper().replace(" ", "") and "-->" in srt


@pytest.mark.skipif(not _ocr_ok(), reason="rapidocr not installed")
def test_huazi_pop(packed):
    pack, _ = packed
    wow = [c for c in pack["text_cues"] if "wow" in P._norm(c["text"])]
    assert wow, [c["text"] for c in pack["text_cues"]]
    c = wow[0]
    assert c["kind"] == "huazi", c
    assert c["style"].get("fill_name") == "黄"
    assert abs(c["t0"] - 4.0) <= 0.25


# without OCR the subtitle band is not recognized as text and shows up as a second "sticker"
@pytest.mark.skipif(not _ocr_ok(), reason="rapidocr not installed")
def test_gif_sticker(packed):
    pack, d = packed
    st = [s for s in pack["stickers"] if s["t0"] < 2.0 and s["t1"] > 1.2]
    assert st, pack["stickers"]
    s = st[0]
    assert s["kind"] == "animated_sticker" and s["looping"]
    assert abs(s["t0"] - 1.0) <= 0.2 and abs(s["t1"] - 2.4) <= 0.3
    x, y, w, h = s["bbox"]
    assert abs((x + w / 2) - (230 + 48) / W) < 0.08 and abs((y + h / 2) - (90 + 48) / H) < 0.06
    assert (d / s["image"]).exists()
    # nothing else is called a sticker: the subtitles and the 花字 are text, the pan is the picture
    assert len(pack["stickers"]) == 1, pack["stickers"]


def test_sfx_alignment():
    cues = [{"id": "text_001", "kind": "huazi", "kind_zh": "花字", "text": "WOW", "t0": 4.0, "t1": 5.0}]
    stickers = [{"id": "sticker_01", "kind_zh": "动态贴纸/GIF", "t0": 1.0, "t1": 2.4, "disappears_mid_shot": True}]
    ev = P.visual_events(cues, stickers, [3.0])
    sfx = [{"t": 4.05, "type": "pop"}, {"t": 1.02, "type": "pop"}, {"t": 3.01, "type": "whoosh"}, {"t": 0.3, "type": "ding"}]
    res = P.align_sfx(sfx, ev)
    assert sfx[0]["on"]["type"] == "huazi_in" and sfx[1]["on"]["type"] == "sticker_in" and sfx[2]["on"]["type"] == "cut"
    assert "on" not in sfx[3]
    assert res["events_with_sfx"]["huazi_in"] == {"with_sfx": 1, "total": 1}
    assert res["events_with_sfx"]["sticker_out"] == {"with_sfx": 0, "total": 1}


def test_ass_export():
    cues = [{"kind": "subtitle", "text": "a/b", "t0": 1.0, "t1": 2.5, "bbox": [0.2, 0.8, 0.6, 0.08], "line_h": 0.035, "angle": 0.0,
             "style": {"fill": "#ffffff", "stroke": "#000000", "stroke_ratio": 0.05}, "anim_in": {"kind": "pop"}},
            {"kind": "scene", "text": "x", "t0": 0, "t1": 1, "bbox": [0, 0, 0.1, 0.1], "line_h": 0.02, "angle": 0.0, "style": {}}]
    ass = P.to_ass(cues, 1080, 1920)
    assert "Dialogue: 0,0:00:01.00,0:00:02.50" in ass and "a\\Nb" in ass and "\\fscx60" in ass
    assert ass.count("Dialogue:") == 1  # scene text is not exported


def test_clean_region():
    st = [{"bbox": [0.6, 0.1, 0.3, 0.15], "t0": 1.0, "t1": 3.0, "confidence": "medium"}]
    x, y, w, h = P.clean_region(st, 1.0, 3.0)
    assert w * h >= 0.45 and (x + w <= 0.6 + 1e-6 or y >= 0.25 - 1e-6)  # the band avoids the sticker
    assert P.clean_region(st, 5.0, 8.0) is None                                 # not on this shot
    assert P.clean_region([dict(st[0], confidence="low")], 1.0, 3.0) is None    # unconfirmed: leave the shot alone


def test_review_overrides_stickers_and_kinds():
    pack = {"text_cues": [{"id": "text_001", "kind": "label", "kind_zh": "文字标注", "text": "WOW", "t0": 1, "t1": 2}],
            "stickers": [{"id": "sticker_01", "kind": "static_sticker", "kind_zh": "静态贴纸/图片", "t0": 1, "t1": 2,
                          "bbox": [0.1, 0.1, 0.2, 0.2], "confidence": "low"},
                         {"id": "sticker_02", "kind": "animated_sticker", "kind_zh": "动态贴纸/GIF", "t0": 3, "t1": 4,
                          "bbox": [0.5, 0.5, 0.2, 0.2], "confidence": "low"}],
            "review": {"by": "claude", "text": {"text_001": "huazi"},
                       "stickers": {"sticker_01": {"is_overlay": False, "what": ""}, "sticker_02": {"is_overlay": True, "what": "猫猫表情包"}},
                       "missed": [{"t0": 6, "t1": 7, "what": "GIF 动图", "where": "右下"}]}}
    P.apply_review(pack)
    assert pack["text_cues"][0]["kind"] == "huazi"
    s1, s2, s3 = pack["stickers"]
    assert s1["in_picture"] and s2["confidence"] == "confirmed" and s2["what"] == "猫猫表情包"
    assert s3["source"] == "agent" and s3["kind"] == "animated_sticker"
    rec = {"start": 0.0, "end": 8.0}
    kinds = [l["kind"] for l in P.shot_layers(pack, rec, "cg_3d")]
    # confirmed stickers stay even on a 3D picture; the rejected one is gone
    assert kinds.count("animated_sticker") == 2 and "static_sticker" not in kinds and "huazi" in kinds


def test_pipeline_writes_packaging(tmp_path, media):
    from rea_video.pipeline import decompose

    b = decompose(media["edit"], tmp_path / "edit", use_vlm=False, ocr=False, stems=False, memory=False)
    pk = b["packaging"]
    assert pk["version"] >= 1 and (tmp_path / "edit" / pk["files"]["timeline"]).is_file()
    # the code-render card sliding in is the picture's own content, not a sticker
    assert not [s for s in pk["stickers"] if not s.get("in_picture")]
    md = (tmp_path / "edit" / "report.md").read_text(encoding="utf-8")
    assert "剪辑包装层" in md
