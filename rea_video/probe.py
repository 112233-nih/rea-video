"""Container-level provenance: camera tags, encoder/editor signatures, AIGC labels, C2PA.

Metadata is strong evidence when present (an iPhone make/model tag means camera footage
entered the pipeline; an AIGC label means a generator declared itself) but it is often
stripped by social platforms, so absence proves nothing."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .media import VideoInfo

# signature -> (category, label). category: generator | editor | renderer | camera | platform
SIGNATURES: list[tuple[str, str, str]] = [
    (r"kling|kuaishou.*ai|可灵", "generator", "Kling"),
    (r"jimeng|dreamina|即梦", "generator", "Jimeng/Dreamina"),
    (r"seedance|doubao.*video", "generator", "Seedance"),
    (r"hailuo|minimax", "generator", "MiniMax/Hailuo"),
    (r"\bveo\b|google.*(deepmind|veo)|synthid", "generator", "Google Veo"),
    (r"\bsora\b|openai", "generator", "OpenAI Sora"),
    (r"runway|gen-?4", "generator", "Runway"),
    (r"pika", "generator", "Pika"),
    (r"vidu|shengshu|生数", "generator", "Vidu"),
    (r"pixverse", "generator", "PixVerse"),
    (r"luma|dream ?machine", "generator", "Luma"),
    (r"wanx|tongyi|万相", "generator", "Wan/Tongyi"),
    (r"capcut|剪映|jianying", "editor", "CapCut/剪映"),
    (r"premiere|adobe", "editor", "Adobe"),
    (r"final ?cut|imovie", "editor", "Apple editor"),
    (r"davinci|resolve", "editor", "DaVinci Resolve"),
    (r"remotion", "renderer", "Remotion"),
    (r"blender", "renderer", "Blender"),
    (r"after ?effects", "renderer", "After Effects"),
    (r"motion ?canvas", "renderer", "Motion Canvas"),
    (r"puppeteer|playwright|headless ?chrom", "renderer", "Headless browser"),
    (r"obs ?studio|\bobs\b", "screen", "OBS"),
    (r"screen ?record|屏幕录制|quicktime player", "screen", "Screen recorder"),
    (r"douyin|抖音|bytedance", "platform", "Douyin"),
    (r"Tencent CAPD MTS|\(T-tech\)", "platform", "WeChat transcode"),
    (r"Wxmm_", "platform", "WeChat Channels"),
    (r"\bvid:v[0-9a-z]{8,}", "platform", "Douyin/TikTok"),
    (r"kuaishou|快手", "platform", "Kuaishou"),
    (r"xiaohongshu|小红书", "platform", "Xiaohongshu"),
]

CAMERA_KEYS = ("com.apple.quicktime.make", "com.apple.quicktime.model", "make", "model", "com.android.manufacturer",
               "com.android.model", "location", "com.apple.quicktime.location.iso6709")
AIGC_KEYS = ("aigc", "ai_generated", "aigc_label", "contentproducer", "produceid")


def _flatten_tags(raw: dict) -> dict[str, str]:
    tags: dict[str, str] = {}
    for k, v in (raw.get("format", {}).get("tags") or {}).items():
        tags[f"format.{k}"] = str(v)
    for i, s in enumerate(raw.get("streams", [])):
        for k, v in (s.get("tags") or {}).items():
            tags[f"stream{i}.{k}"] = str(v)
    return tags


def _scan_bytes(path: Path, limit: int = 4 << 20) -> bytes:
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(min(limit, size))
        tail = b""
        if size > limit:
            f.seek(max(0, size - limit))
            tail = f.read(limit)
    return head + tail


AIGC_LABEL_ZH = {"1": "属于 AI 生成合成内容", "2": "可能为 AI 生成合成内容", "3": "疑似 AI 生成合成内容"}


def parse_aigc(value: str) -> dict | None:
    """GB 45438-2025 implicit label, e.g. {"Label": "1", "ContentProducer": "...", "ProduceID": "..."}."""
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", value or "")
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    label = str(data.get("Label", data.get("label", ""))).strip()
    return {"label": label, "meaning": AIGC_LABEL_ZH.get(label, "未知取值"),
            "content_producer": data.get("ContentProducer", ""), "produce_id": data.get("ProduceID", ""),
            "content_propagator": data.get("ContentPropagator", ""), "propagate_id": data.get("PropagateID", "")}


def _printable_strings(raw: bytes, min_len: int = 6) -> str:
    return " ".join(m.decode("ascii") for m in re.findall(rb"[\x20-\x7e]{%d,}" % min_len, raw))


def provenance(info: VideoInfo) -> dict:
    path = Path(info.path)
    tags = _flatten_tags(info.raw)
    evidence: list[dict] = []
    hints: dict[str, list[str]] = {"generator": [], "editor": [], "renderer": [], "camera": [], "screen": [], "platform": []}

    lower_tags = {k.lower(): v for k, v in tags.items()}
    aigc = None
    for key, value in lower_tags.items():
        short = key.split(".", 1)[-1]
        if short in CAMERA_KEYS:
            hints["camera"].append(f"{short}={value}")
        if any(a in short for a in AIGC_KEYS) or re.search(r"\baigc\b", value, re.I):
            aigc = parse_aigc(value) or {"label": "", "meaning": "无法解析", "raw": value[:200]}
            strong = aigc.get("label") in ("1", "2", "3")
            evidence.append({"kind": "aigc_label", "detail": f"AIGC 隐式标识 Label={aigc.get('label')}（{aigc.get('meaning')}），"
                             f"生成服务方编码 {aigc.get('content_producer') or '—'}", "weight": "strong" if strong else "weak"})
            if aigc.get("label") == "1":
                hints["generator"].append(f"AIGC 标识（{aigc.get('meaning')}）")

    blob = " ".join(f"{k}={v}" for k, v in tags.items() if "aigc" not in k.lower())
    raw_bytes = _scan_bytes(path)
    strings = _printable_strings(raw_bytes)
    for pattern, cat, label in SIGNATURES:
        if re.search(pattern, blob, re.I):
            hints[cat].append(label)
            evidence.append({"kind": f"{cat}_signature", "detail": f"元数据包含 {label}", "weight": "medium"})
        elif cat in ("generator", "renderer", "editor") and re.search(rf"\b(?:{pattern})\b", strings, re.I):
            # weaker: readable string inside container boxes (udta/uuid/xmp)
            hints[cat].append(f"{label}?")
            evidence.append({"kind": f"{cat}_bytes", "detail": f"容器内可读字符串出现 {label}", "weight": "weak"})

    if b"c2pa" in raw_bytes or b"jumb" in raw_bytes:
        evidence.append({"kind": "c2pa", "detail": "发现 C2PA/JUMBF 内容凭证盒", "weight": "strong"})
        hints["generator"].append("C2PA 凭证")
    if hints["camera"]:
        evidence.append({"kind": "camera_tags", "detail": "; ".join(hints["camera"][:4]), "weight": "strong"})

    encoder = lower_tags.get("format.encoder") or next((v for k, v in lower_tags.items() if k.endswith(".encoder")), "")
    handlers = sorted({v for k, v in lower_tags.items() if k.endswith("handler_name")})
    return {
        "container": info.raw.get("format", {}).get("format_name"),
        "encoder": encoder,
        "handlers": handlers,
        "creation_time": lower_tags.get("format.creation_time", ""),
        "aigc": aigc,
        "hints": {k: sorted(set(v)) for k, v in hints.items() if v},
        "evidence": evidence,
        "tags": tags,
    }
