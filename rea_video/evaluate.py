"""Evaluate decomposition accuracy against labelled videos (eval/labels.yaml)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from .classify import LABELS
from .pipeline import decompose, finalize
from .util import LOG, read_json, round_floats, write_json


def _expand(path: str) -> str:
    root = os.getenv("CAPSULE_CINEMA_ROOT", str(Path.home() / "code/github/capsule-cinema"))
    p = os.path.expanduser(path.replace("${CAPSULE}", root))
    if not os.path.isabs(p):  # relative paths are relative to the repo root
        p = str(Path(__file__).resolve().parents[1] / p)
    return p


def _accept(label) -> set[str]:
    return set(label) if isinstance(label, list) else {label}


def gt_at(entry: dict, t: float) -> set[str] | None:
    if "segments" in entry:
        for a, b, lab in entry["segments"]:
            if a <= t < b:
                return _accept(lab)
        return None
    if "label" not in entry:  # audio-only entry
        return None
    return _accept(entry["label"])


def real_at(entry: dict, t: float) -> bool | None:
    rm = entry.get("real_material")
    if rm is None:
        return None
    if isinstance(rm, bool):
        return rm
    for a, b, v in rm:
        if a <= t < b:
            return bool(v)
    return None


def audio_check(gt: dict, b: dict) -> dict:
    a = b.get("audio", {})
    out = {}
    if "voice" in gt:
        out["voice"] = bool(a.get("voice", {}).get("present")) == bool(gt["voice"])
    if "music" in gt:
        out["music"] = bool(a.get("music", {}).get("present")) == bool(gt["music"])
    if "sfx" in gt:
        n = len((b.get("audio_plan") or {}).get("sfx_merged", []))
        out["sfx"] = (n >= 3) == bool(gt["sfx"])
    if gt.get("bpm"):
        est = (a.get("music") or {}).get("tempo_bpm")
        cands = [est] + list((a.get("music") or {}).get("tempo_alternatives", [])) if est else []
        out["bpm"] = any(abs(c - gt["bpm"]) / gt["bpm"] < 0.04 for c in cands)
    if gt.get("music_source"):
        ms = (b.get("sound_sources") or {}).get("music") or {}
        out["music_source"] = ms.get("leaning") == gt["music_source"]
    if gt.get("voice_source"):
        vs = (b.get("sound_sources") or {}).get("voice") or {}
        out["voice_source"] = vs.get("leaning") == gt["voice_source"]
    if gt.get("key"):
        k = (a.get("music") or {}).get("key") or {}
        out["key"] = f"{k.get('tonic')} {k.get('mode')}" == gt["key"]
    return out


def boundary_f1(pred: list[float], gt: list[float], tol: float = 0.1) -> dict:
    used = set()
    tp = 0
    for p in pred:
        for i, g in enumerate(gt):
            if i not in used and abs(p - g) <= tol:
                used.add(i)
                tp += 1
                break
    prec = tp / len(pred) if pred else 1.0
    rec = tp / len(gt) if gt else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)}


def evaluate(labels_path: Path, out: Path, use_vlm: bool = False, ocr: bool = True, reuse: bool = True, only: set[str] | None = None,
             stems: bool = False) -> dict:
    spec = yaml.safe_load(labels_path.read_text(encoding="utf-8"))
    rows, confusion = [], {g: {p: 0.0 for p in LABELS} for g in LABELS}
    tot_dur = ok_dur = 0.0
    real_tot = real_ok = 0.0
    audio_hits: dict[str, list[bool]] = {}
    aspect_hits: dict[str, list[bool]] = {}
    for entry in spec["videos"]:
        path = _expand(entry["path"])
        if not Path(path).is_file():
            LOG.warning("skip %s: missing %s", entry["id"], path)
            continue
        vout = out / entry["id"]
        bfile = vout / "breakdown.json"
        if (not only or entry["id"] not in only) and reuse and bfile.is_file() and (not use_vlm or read_json(bfile)["analysis"]["vlm"].get("used")):
            b = read_json(bfile)
            from .media import video_info
            from .probe import provenance

            b["provenance"] = {k: v for k, v in provenance(video_info(path)).items() if k != "tags"}  # signatures evolve
            if b["source"].get("has_audio"):  # audio analysis is cheap: always redo with current code
                from .audio import analyze_audio

                marks = [t for s in b["shots"] for t in s.get("beats", [])]
                b["audio"] = round_floats(analyze_audio(path, [s["start"] for s in b["shots"][1:]], marks))
                from .sound_source import per_clip_audio

                b["per_clip_audio"] = per_clip_audio(path, [s["start"] for s in b["shots"][1:]], b["source"]["duration"])
                if stems and entry.get("audio"):
                    from .audio import refine_with_stems
                    from .stems import extract_stems

                    voc = vout / "stems" / "vocals.wav"
                    if not voc.is_file():
                        b["stems"] = extract_stems(path, vout)
                    if voc.is_file():
                        refine_with_stems(b["audio"], str(voc))
            b = finalize(b, vout)  # re-classify with the current rules
        else:
            b = decompose(path, vout, use_vlm=use_vlm, ocr=ocr, whole_vlm=False, stems=stems and bool(entry.get("audio")), memory=False)
        v_tot = v_ok = 0.0
        m_tot = m_ok = 0.0
        shots = []
        for s in b["shots"]:
            mid = (s["start"] + s["end"]) / 2
            acc = gt_at(entry, mid)
            if acc is None:
                continue
            pred = s["base"]["label"]
            hit = pred in acc
            v_tot += s["duration"]
            v_ok += s["duration"] if hit else 0.0
            primary = sorted(acc)[0] if len(acc) > 1 else next(iter(acc))
            confusion[primary if not hit else pred][pred] += s["duration"]
            shots.append({"shot": s["index"], "t": [s["start"], s["end"]], "pred": pred, "conf": s["base"]["confidence"],
                          "gt": sorted(acc), "ok": hit})
            gt_real = real_at(entry, mid)
            if gt_real is not None:
                pred_real = bool((s.get("material") or {}).get("has_real_material"))
                m_tot += s["duration"]
                m_ok += s["duration"] if pred_real == gt_real else 0.0
                shots[-1]["real"] = {"gt": gt_real, "pred": pred_real}
        row = {"id": entry["id"], "accuracy": round(v_ok / v_tot, 3) if v_tot else None, "seconds": round(v_tot, 2),
               "verdict": b["summary"]["verdict"], "shots": shots}
        if m_tot:
            row["real_material_accuracy"] = round(m_ok / m_tot, 3)
            real_tot += m_tot
            real_ok += m_ok
        if entry.get("audio"):
            row["audio"] = audio_check(entry["audio"], b)
            for k, v in row["audio"].items():
                audio_hits.setdefault(k, []).append(v)
        if entry.get("aspects"):
            from .aspects import check as aspect_check

            row["aspects"] = aspect_check(entry["aspects"], b)
            for k, v in row["aspects"].items():
                aspect_hits.setdefault(k, []).append(v)
        if "cuts" in entry:
            row["cut_f1"] = boundary_f1([s["start"] for s in b["shots"][1:]], entry["cuts"], tol=float(entry.get("cut_tolerance", 0.1)))
        if entry.get("image_source"):
            srcs = {s["base"].get("image_source") for s in b["shots"]}
            row["image_source_ok"] = entry["image_source"] in srcs
        rows.append(row)
        tot_dur += v_tot
        ok_dur += v_ok
        LOG.info("%-28s acc=%s  %s", entry["id"], row["accuracy"], b["summary"]["verdict"][:80])
    summary = {"videos": len(rows), "duration_weighted_accuracy": round(ok_dur / tot_dur, 3) if tot_dur else None,
               "video_accuracy_mean": round(sum(r["accuracy"] for r in rows if r["accuracy"] is not None) / max(1, sum(r["accuracy"] is not None for r in rows)), 3),
               "per_video": {r["id"]: r["accuracy"] for r in rows if r["accuracy"] is not None}, "vlm": use_vlm}
    if real_tot:
        summary["real_material_accuracy"] = round(real_ok / real_tot, 3)
        summary["real_material_per_video"] = {r["id"]: r["real_material_accuracy"] for r in rows if "real_material_accuracy" in r}
    if audio_hits:
        summary["audio"] = {k: f"{sum(v)}/{len(v)}" for k, v in audio_hits.items()}
    if aspect_hits:
        summary["aspects"] = {k: f"{sum(v)}/{len(v)}" for k, v in aspect_hits.items()}
    cuts = [r["cut_f1"]["f1"] for r in rows if "cut_f1" in r]
    if cuts:
        summary["cut_f1_mean"] = round(sum(cuts) / len(cuts), 3)
    result = {"summary": summary, "rows": rows,
              "confusion_seconds": {g: {p: round(v, 1) for p, v in ps.items() if v} for g, ps in confusion.items() if any(ps.values())}}
    write_json(out / "eval_result.json", result)
    return result
