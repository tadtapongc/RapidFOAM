"""Regenerate the committed golden snapshots (manual, opt-in).

The golden files in this directory are the artifact contract for case
generation. A diff against them is a behaviour-change decision, not an
automatic accept: run this script only after deciding the new output is
correct, then review ``git diff tests/golden`` before committing.

Usage (from the repository root):
    python tests/golden/regenerate.py

Mirrors ``tests/test_casegen_golden.py``: same config, same STL, same
normalisation (CRLF -> LF for text; parsed-JSON equality for the config).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from rapidfoam.cli import _do_generate
from rapidfoam.geometry.stl import write_stl

GOLDEN = Path(__file__).resolve().parent

TEXT_FILES = {
    "system/blockMeshDict": "blockMeshDict.golden",
    "system/snappyHexMeshDict": "snappyHexMeshDict.golden",
    "system/surfaceFeatureExtractDict": "surfaceFeatureExtractDict.golden",
    "Allrun.parallel": "Allrun.parallel.golden",
    "run.sh": "run.sh.golden",
}


def _generate_case(root: Path) -> Path:
    write_stl(root / "stl" / "body.stl", "body", [
        ((0, 0, 1), (0, 0, 0), (1, 0, 0), (0, 1, 3)),
    ])
    cfg = {
        "case_name": "golden_test",
        "stl_files": ["body.stl"],
        "fidelity": "standard",
        "flow": {"velocity": 20.0, "direction": "-z", "ground": True},
        "outputs": {"drag_axis": "-z", "downforce_axis": "-y"},
    }
    cfg_path = root / "config.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        _do_generate(cfg_path, root)
    return root / "cases" / "golden_test"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        case = _generate_case(Path(tmp))

        changed: list[str] = []
        for rel, golden_name in TEXT_FILES.items():
            text = (case / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
            target = GOLDEN / golden_name
            payload = text.encode("utf-8")
            if not target.exists() or target.read_bytes() != payload:
                changed.append(golden_name)
            target.write_bytes(payload)

        config = json.loads((case / "case_config.json").read_text(encoding="utf-8"))
        target = GOLDEN / "case_config.golden.json"
        payload = (json.dumps(config, indent=2) + "\n").encode("utf-8")
        if not target.exists() or target.read_bytes() != payload:
            changed.append(target.name)
        target.write_bytes(payload)

    print(f"Regenerated {len(TEXT_FILES) + 1} golden files in {GOLDEN}")
    if changed:
        print("Changed: " + ", ".join(sorted(changed)))
        print("Review the diff (git diff tests/golden) before committing.")
    else:
        print("No content changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
