"""Unit tests for near-wall y+ reading and target verification."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.postproc.yplus import (
    check_yplus_target,
    find_yplus_files,
    read_yplus,
)

HEADER = "# Time\tpatch\tmin\tmax\taverage\n"


def write_yplus(root: Path, time: str, rows: list[tuple[str, float, float, float]]) -> None:
    d = root / "postProcessing" / "yPlus" / time
    d.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{time}\t{p}\t{lo}\t{hi}\t{avg}\n" for p, lo, hi, avg in rows)
    (d / "yPlus.dat").write_text(HEADER + body, encoding="utf-8")


class TestFindYplusFiles(unittest.TestCase):
    def test_finds_and_orders_time_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_yplus(root, "100", [("body", 40, 60, 50)])
            write_yplus(root, "200", [("body", 40, 60, 50)])
            files = find_yplus_files(root)
            self.assertEqual([f.parent.name for f in files], ["100", "200"])

    def test_falls_back_to_processor_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_yplus(root / "processor0", "100", [("body", 40, 60, 50)])
            files = find_yplus_files(root)
            self.assertTrue(files)

    def test_empty_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(find_yplus_files(Path(tmp)), [])


class TestReadYplus(unittest.TestCase):
    def test_reads_patches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_yplus(root, "100", [("body", 1.0, 5.0, 3.0), ("ground", 0.5, 2.0, 1.2)])
            data = read_yplus(find_yplus_files(root))
            self.assertAlmostEqual(data["body"]["average"], 3.0)
            self.assertAlmostEqual(data["ground"]["min"], 0.5)

    def test_later_time_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_yplus(root, "100", [("body", 1.0, 5.0, 3.0)])
            write_yplus(root, "200", [("body", 10.0, 20.0, 15.0)])
            data = read_yplus(find_yplus_files(root))
            self.assertAlmostEqual(data["body"]["average"], 15.0)

    def test_malformed_rows_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "postProcessing" / "yPlus" / "100"
            d.mkdir(parents=True)
            (d / "yPlus.dat").write_text(
                HEADER + "100\tbody\tnot\ta\tnumber\n200\tbody\t1\t2\t1.5\n", encoding="utf-8"
            )
            data = read_yplus(find_yplus_files(root))
            self.assertIn("body", data)
            self.assertAlmostEqual(data["body"]["average"], 1.5)


class TestCheckYplusTarget(unittest.TestCase):
    def _data(self, avg, patch="body"):
        return {patch: {"min": avg * 0.5, "max": avg * 2, "average": avg, "time": 100.0}}

    def test_on_target(self):
        res = check_yplus_target(self._data(40.0), target=40.0)
        self.assertEqual(res["patches"]["body"]["status"], "on_target")
        self.assertEqual(res["off_target"], [])

    def test_acceptable_wall_function_tier(self):
        res = check_yplus_target(self._data(120.0), target=40.0)
        self.assertEqual(res["patches"]["body"]["status"], "acceptable")
        self.assertEqual(res["off_target"], [])

    def test_wall_resolved_target_rejects_high_yplus(self):
        # A y+ 1 target realised at 250 is off-target, not acceptable.
        res = check_yplus_target(self._data(250.0), target=1.0)
        self.assertEqual(res["patches"]["body"]["status"], "off_target")
        self.assertIn("body", res["off_target"])

    def test_wall_resolved_target_accepts_low_yplus(self):
        res = check_yplus_target(self._data(3.0), target=1.0)
        self.assertEqual(res["patches"]["body"]["status"], "acceptable")

    def test_off_target(self):
        res = check_yplus_target(self._data(500.0), target=40.0)
        self.assertEqual(res["patches"]["body"]["status"], "off_target")
        self.assertIn("body", res["off_target"])
        self.assertIn("missed", res["note"])

    def test_unavailable_without_data(self):
        self.assertFalse(check_yplus_target({}, target=40.0)["available"])

    def test_no_target_lists_values(self):
        res = check_yplus_target(self._data(40.0), target=None)
        self.assertTrue(res["available"])
        self.assertIsNone(res["target"])
        self.assertEqual(res["patches"]["body"]["status"], "on_target")

    def test_zero_target_treated_as_none(self):
        res = check_yplus_target(self._data(40.0), target=0.0)
        self.assertIsNone(res["target"])


if __name__ == "__main__":
    unittest.main()
