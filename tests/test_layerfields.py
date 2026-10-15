"""Unit tests for the snappyHexMesh layer-field parser (first-layer coverage)."""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rapidfoam.postproc.layerfields import (
    find_layer_field_files,
    first_layer_coverage,
    parse_layer_field,
    read_layer_fields,
)

HEADER = b"""\
FoamFile
{
    version     2.0;
    format      %s;
    class       volScalarField;
    object      nSurfaceLayers;
}

dimensions      [0 0 0 0 0 0 0];

internalField   uniform 0;

boundaryField
{
    "Assem_AERO_BASE"
    {
        type            calculated;
        value           %s
    }
    ground
    {
        type            calculated;
        value           uniform 0;
    }
}
"""

ASCII_BODY = (
    b"nonuniform List<scalar> \n"
    b"4\n"
    b"(\n"
    b"0\n"
    b"2\n"
    b"8\n"
    b"8\n"
    b")\n"
    b";\n"
)


def ascii_field() -> bytes:
    return HEADER % (b"ascii", ASCII_BODY)


def binary_field(prefix: bytes = b"(\n", internal: bytes | None = None) -> bytes:
    values = struct.pack("<4d", 0.0, 2.0, 8.0, 8.0)
    body = b"nonuniform List<scalar> \n4\n" + prefix + values + b"\n)\n;\n"
    field = HEADER % (b"binary", body)
    if internal is not None:
        field = field.replace(b"internalField   uniform 0;", internal)
    return field


class ParseLayerFieldTest(unittest.TestCase):
    def test_ascii_values(self):
        parsed = parse_layer_field(ascii_field())
        self.assertEqual(parsed["Assem_AERO_BASE"]["values"], [0.0, 2.0, 8.0, 8.0])
        self.assertEqual(parsed["ground"]["uniform"], 0.0)

    def test_binary_values_data_after_newline(self):
        parsed = parse_layer_field(binary_field(prefix=b"(\n"))
        self.assertEqual(parsed["Assem_AERO_BASE"]["values"], [0.0, 2.0, 8.0, 8.0])

    def test_binary_values_data_immediately_after_paren(self):
        parsed = parse_layer_field(binary_field(prefix=b"("))
        self.assertEqual(parsed["Assem_AERO_BASE"]["values"], [0.0, 2.0, 8.0, 8.0])

    def test_binary_internal_field_is_skipped(self):
        internal = (b"internalField   nonuniform List<scalar> \n3\n(\n"
                    + struct.pack("<3d", 0.0, 0.0, 0.0) + b"\n)\n;")
        parsed = parse_layer_field(binary_field(prefix=b"(\n", internal=internal))
        self.assertEqual(parsed["Assem_AERO_BASE"]["values"], [0.0, 2.0, 8.0, 8.0])

    def test_empty_nonuniform_list_does_not_break_later_patches(self):
        # snappy writes an empty list as `List<scalar> 0;` (no parens) for
        # patches a processor does not own. Regression: this aborted the whole
        # patch scan, so the body patch after it never parsed.
        field = (
            b"FoamFile\n{\n    version 2.0;\n    format ascii;\n    class volScalarField;\n}\n\n"
            b"dimensions [0 0 0 0 0 0 0];\n\ninternalField uniform 0;\n\nboundaryField\n{\n"
            b"    farField\n    {\n        type fixedValue;\n"
            b"        value nonuniform List<scalar> 0;\n    }\n"
            b"    geometry\n    {\n        type fixedValue;\n"
            b"        value nonuniform List<scalar> \n2\n(\n5\n5\n)\n;\n    }\n}\n"
        )
        parsed = parse_layer_field(field)
        self.assertEqual(parsed["geometry"]["values"], [5.0, 5.0])
        self.assertEqual(parsed["farField"]["values"], [])

    def test_real_binary_layout_data_then_close_paren_semicolon(self):
        # Real snappy layout: data directly after "(", closed by ");" with no
        # newline between the blob and the closing token.
        values = struct.pack("<3d", 5.0, 0.0, 2.0)
        field = (
            b"FoamFile\n{\n    version 2.0;\n    format binary;\n    class volScalarField;\n}\n\n"
            b"dimensions [0 0 0 0 0 0 0];\n\ninternalField uniform 0;\n\nboundaryField\n{\n"
            b"    geometry\n    {\n        type fixedValue;\n"
            b"        value nonuniform List<scalar> \n3\n(" + values + b");\n    }\n}\n"
        )
        parsed = parse_layer_field(field)
        self.assertEqual(parsed["geometry"]["values"], [5.0, 0.0, 2.0])

    def test_unknown_entry_recovers_at_the_next_patch(self):
        field = (
            b"FoamFile\n{\n    version 2.0;\n    format ascii;\n    class volScalarField;\n}\n\n"
            b"dimensions [0 0 0 0 0 0 0];\n\ninternalField uniform 0;\n\nboundaryField\n{\n"
            b"    broken\n    {\n        type fixedValue;\n"
            b"        value bogus syntax;\n    }\n"
            b"    geometry\n    {\n        type fixedValue;\n"
            b"        value nonuniform List<scalar> \n2\n(\n5\n5\n)\n;\n    }\n}\n"
        )
        parsed = parse_layer_field(field)
        self.assertEqual(parsed["geometry"]["values"], [5.0, 5.0])

    def test_truncated_blob_is_declined(self):
        # Declared count exceeds the available bytes: the body patch must not be
        # parsed (recovery may still find later patches, but never fabricate it).
        body = b"nonuniform List<scalar> \n4\n(\n" + b"\x00" * 8 + b"\n)\n;\n"
        parsed = parse_layer_field(HEADER % (b"binary", body))
        self.assertNotIn("Assem_AERO_BASE", parsed)

    def test_missing_close_token_is_declined(self):
        # Data of the right length but no closing ")" after it.
        body = (b"nonuniform List<scalar> \n4\n(\n" + struct.pack("<4d", 0.0, 1.0, 2.0, 3.0)
                + b"\x00\x00\x00\x00")
        parsed = parse_layer_field(HEADER % (b"binary", body))
        self.assertNotIn("Assem_AERO_BASE", parsed)

    def test_garbage_is_declined(self):
        self.assertEqual(parse_layer_field(b"not a field"), {})


class CoverageTest(unittest.TestCase):
    def _write(self, root: Path, name: str, data: bytes) -> Path:
        path = root / "meshDiagnostics" / name / "nSurfaceLayers"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_body_coverage_and_histogram(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [self._write(root, "processor0", ascii_field())]
            summary = first_layer_coverage(files)
            self.assertTrue(summary["available"])
            body = summary["body"]
            self.assertEqual(body["faces"], 4)
            self.assertEqual(body["layered"], 3)
            self.assertAlmostEqual(body["coverage"], 0.75)
            self.assertEqual(body["histogram"], {0: 1, 2: 1, 8: 2})
            self.assertIn("75%", summary["note"])

    def test_domain_patches_are_excluded_from_the_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [self._write(root, "processor0", ascii_field())]
            summary = first_layer_coverage(files)
            self.assertNotIn("ground", [p for p, s in summary["patches"].items() if s.get("faces")])
            self.assertEqual(summary["body"]["faces"], 4)

    def test_processor_files_are_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p0 = self._write(root, "processor0", ascii_field())
            p1 = self._write(root, "processor1", binary_field())
            merged = read_layer_fields([p0, p1])
            self.assertEqual(len(merged["Assem_AERO_BASE"]["values"]), 8)
            summary = first_layer_coverage([p0, p1])
            self.assertEqual(summary["body"]["faces"], 8)
            self.assertEqual(summary["body"]["layered"], 6)

    def test_find_prefers_mesh_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(find_layer_field_files(root), [])
            expected = self._write(root, "processor0", ascii_field())
            self.assertEqual(find_layer_field_files(root), [expected])

    def test_missing_field_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = first_layer_coverage(find_layer_field_files(Path(tmp)))
            self.assertFalse(summary["available"])


if __name__ == "__main__":
    unittest.main()
