#!/usr/bin/env python3
"""Create a capsule-cinema capsule draft from a rea-video breakdown.

Runs inside capsule-cinema's Python environment. Uses capsule-cinema's own
run_video_to_capsule() and injects rea-video's analysis through its tool_factory hook,
so no capsule-cinema code is modified."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REA_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REA_ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--breakdown", required=True)
    ap.add_argument("--capsule-root", default=os.getenv("CAPSULE_CINEMA_ROOT", str(Path.home() / "code/github/capsule-cinema")))
    ap.add_argument("--capsule-name", default="")
    ap.add_argument("--capsule-display-name", default="")
    ap.add_argument("--write-capsule", action="store_true")
    ap.add_argument("--overwrite-capsule", action="store_true")
    args = ap.parse_args()

    from rea_video.integrations.capsule_cinema import export_capsule_analysis
    from rea_video.util import read_json

    bpath = Path(args.breakdown).expanduser().resolve()
    b = read_json(bpath)
    analysis_path = export_capsule_analysis(b, bpath.with_name("capsule_analysis.json"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

    root = Path(args.capsule_root).expanduser().resolve()
    sys.path.insert(0, str(root / "scripts"))
    sys.path.insert(0, str(root / "lib"))
    import analyze_video_to_capsule as a2c  # noqa: E402  (loads capsule-cinema env on import)

    class ReaVideoAnalyzer:
        def _run(self, video_path: str, prompt: str = "", analysis_focus: str = "content"):
            return analysis

    result = a2c.run_video_to_capsule(
        source_video_path=b["source"]["path"],
        video_analysis_tool="ReaVideoAnalyzer",
        capsule_name=args.capsule_name,
        capsule_display_name=args.capsule_display_name,
        write_capsule=args.write_capsule,
        overwrite_capsule=args.overwrite_capsule,
        tool_factory=lambda _name: ReaVideoAnalyzer(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
