"""STL file utilities — ASCII STL reader/writer for OpenFOAM.

Supports:
  - Reading ASCII STL files
  - Writing ASCII STL (required by snappyHexMesh)
  - Solid name rewriting for OpenFOAM patch matching
  - Bounding box computation
"""

from __future__ import annotations

import math
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

Triangle = tuple[
    tuple[float, float, float],  # normal
    tuple[float, float, float],  # v1
    tuple[float, float, float],  # v2
    tuple[float, float, float],  # v3
]

BBox = tuple[tuple[float, float, float], tuple[float, float, float]]

# Log-binned histogram for O(1)-memory edge-length percentiles. Spans twelve
# decades (1 pm .. 1 Tm) at 16 bins/decade, far wider than any CAD model.
_EDGE_LOG_MIN = -12.0
_EDGE_LOG_MAX = 12.0
_EDGE_BINS_PER_DECADE = 16
_EDGE_BINS = int((_EDGE_LOG_MAX - _EDGE_LOG_MIN) * _EDGE_BINS_PER_DECADE)


@dataclass
class EdgeStats:
    """Streaming edge-length statistics for a tessellated surface.

    Accumulates triangle edge lengths in a bounded log-histogram so that
    "small feature" percentiles can be estimated without retaining the mesh.
    Instances can be merged to combine several STL files.
    """

    n_edges: int = 0
    n_degenerate: int = 0
    min_edge: float = float("inf")
    max_edge: float = 0.0
    bins: list[int] = field(default_factory=lambda: [0] * _EDGE_BINS)

    def add(self, length: float) -> None:
        """Accumulate a single edge length (non-positive lengths are degenerate)."""
        if not math.isfinite(length) or length <= 0.0:
            self.n_degenerate += 1
            return
        self.n_edges += 1
        if length < self.min_edge:
            self.min_edge = length
        if length > self.max_edge:
            self.max_edge = length
        idx = int((math.log10(length) - _EDGE_LOG_MIN) * _EDGE_BINS_PER_DECADE)
        idx = min(max(idx, 0), _EDGE_BINS - 1)
        self.bins[idx] += 1

    def add_triangle(self, v0, v1, v2) -> None:
        """Accumulate the three edge lengths of one triangle."""
        self.add(math.dist(v0, v1))
        self.add(math.dist(v1, v2))
        self.add(math.dist(v2, v0))

    def merge(self, other: EdgeStats) -> None:
        """Combine another instance in place (histograms are additive)."""
        self.n_edges += other.n_edges
        self.n_degenerate += other.n_degenerate
        if other.min_edge < self.min_edge:
            self.min_edge = other.min_edge
        if other.max_edge > self.max_edge:
            self.max_edge = other.max_edge
        for i, count in enumerate(other.bins):
            self.bins[i] += count

    def percentile(self, pct: float) -> float:
        """Edge length at the given percentile (upper bin bound, so it over-estimates)."""
        if self.n_edges <= 0:
            return 0.0
        target = max(0.0, min(100.0, float(pct))) / 100.0 * self.n_edges
        if target <= 0.0:
            return self.min_edge
        cumulative = 0
        for i, count in enumerate(self.bins):
            cumulative += count
            if cumulative >= target:
                log_upper = _EDGE_LOG_MIN + (i + 1) / _EDGE_BINS_PER_DECADE
                return 10.0 ** log_upper
        return self.max_edge


def is_binary_stl(filepath: Path) -> bool:
    """Detect whether an STL file is binary or ASCII."""
    filepath = Path(filepath)
    size = filepath.stat().st_size

    if size < 84:
        return False

    # Check text content first to avoid false positives
    try:
        with open(filepath, "r", errors="ignore") as f:
            first_line = f.readline().strip()
            if first_line.startswith("solid"):
                for _ in range(5):
                    line = f.readline().strip()
                    if "facet" in line or "vertex" in line or "endsolid" in line:
                        return False
    except Exception:
        pass

    with open(filepath, "rb") as f:
        f.read(80)
        n_triangles = struct.unpack("<I", f.read(4))[0]

    expected_size = 84 + 50 * n_triangles
    return size == expected_size


def stl_analyze(filepath: str | Path) -> tuple[str, int, BBox, EdgeStats]:
    """Inspect an ASCII STL in a single streaming pass with O(1) memory.

    Computes the solid name, triangle count, bounding box and edge-length
    statistics (see :class:`EdgeStats`) used for feature-based mesh sizing.

    Returns:
        (solid_name, triangle_count, ((xmin, ymin, zmin), (xmax, ymax, zmax)), stats)

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If file is binary or malformed.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"STL file not found: {filepath}")

    if is_binary_stl(filepath):
        raise ValueError(
            f"Binary STL not supported: {filepath}\n"
            f"  Convert to ASCII in your CAD tool (SolidWorks: Save As → STL → ASCII)."
        )

    name: str | None = None
    min_x = min_y = min_z = float("inf")
    max_x = max_y = max_z = float("-inf")
    n_triangles = 0
    stats = EdgeStats()
    triangle_vertices: list[tuple[float, float, float]] = []

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if name is None and line.startswith("solid"):
                name = line[5:].strip() or filepath.stem
            elif line.startswith("vertex"):
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        x = float(parts[1])
                        y = float(parts[2])
                        z = float(parts[3])
                    except ValueError:
                        continue
                    triangle_vertices.append((x, y, z))
                    if x < min_x: min_x = x
                    if x > max_x: max_x = x
                    if y < min_y: min_y = y
                    if y > max_y: max_y = y
                    if z < min_z: min_z = z
                    if z > max_z: max_z = z
                    if len(triangle_vertices) == 3:
                        n_triangles += 1
                        stats.add_triangle(*triangle_vertices)
                        triangle_vertices = []
            elif line.startswith("endfacet"):
                triangle_vertices = []

    if name is None:
        raise ValueError(f"Not a valid ASCII STL: {filepath}")
    if n_triangles < 1:
        raise ValueError(f"No triangles found in {filepath}")

    bbox: BBox = ((min_x, min_y, min_z), (max_x, max_y, max_z))
    return name, n_triangles, bbox, stats


def stl_info(filepath: str | Path) -> tuple[str, int, BBox]:
    """Inspect an ASCII STL file in a single streaming pass with O(1) memory.

    Returns:
        (solid_name, triangle_count, ((xmin, ymin, zmin), (xmax, ymax, zmax)))

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If file is binary or malformed.
    """
    name, n_triangles, bbox, _ = stl_analyze(filepath)
    return name, n_triangles, bbox


def read_stl(filepath: str | Path) -> tuple[str, list[Triangle]]:
    """Read an ASCII STL file into triangle tuples.

    Returns:
        (solid_name, list_of_triangles)
        Each triangle is (normal, v1, v2, v3) where each is a 3-tuple of floats.

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If file is binary or malformed.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"STL file not found: {filepath}")

    if is_binary_stl(filepath):
        raise ValueError(
            f"Binary STL not supported: {filepath}\n"
            f"  Convert to ASCII in your CAD tool (SolidWorks: Save As → STL → ASCII)."
        )

    name: str | None = None
    triangles: list[Triangle] = []
    curr_normal = (0.0, 0.0, 0.0)
    curr_vertices: list[tuple[float, float, float]] = []

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if name is None and line.startswith("solid"):
                name = line[5:].strip() or filepath.stem
            elif line.startswith("facet"):
                parts = line.split()
                if len(parts) >= 5 and parts[1] == "normal":
                    try:
                        curr_normal = (float(parts[2]), float(parts[3]), float(parts[4]))
                    except ValueError:
                        curr_normal = (0.0, 0.0, 0.0)
                curr_vertices = []
            elif line.startswith("vertex"):
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        curr_vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
                    except ValueError:
                        pass
            elif line.startswith("endfacet"):
                if len(curr_vertices) == 3:
                    triangles.append((curr_normal, curr_vertices[0], curr_vertices[1], curr_vertices[2]))
                curr_vertices = []

    if name is None:
        raise ValueError(f"Not a valid ASCII STL: {filepath}")
    if not triangles:
        raise ValueError(f"No triangles found in {filepath}")

    return name, triangles


def write_stl(filepath: str | Path, name: str, triangles: Sequence[Triangle]) -> None:
    """Write triangles to an ASCII STL file."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"solid {name}\n")
        for normal, v1, v2, v3 in triangles:
            f.write(f"  facet normal {normal[0]:.6e} {normal[1]:.6e} {normal[2]:.6e}\n")
            f.write("    outer loop\n")
            f.write(f"      vertex {v1[0]:.6e} {v1[1]:.6e} {v1[2]:.6e}\n")
            f.write(f"      vertex {v2[0]:.6e} {v2[1]:.6e} {v2[2]:.6e}\n")
            f.write(f"      vertex {v3[0]:.6e} {v3[1]:.6e} {v3[2]:.6e}\n")
            f.write("    endloop\n")
            f.write("  endfacet\n")
        f.write(f"endsolid {name}\n")


def stl_bounds(filepath: str | Path) -> BBox:
    """Compute axis-aligned bounding box via streaming with O(1) memory.

    Returns:
        ((xmin, ymin, zmin), (xmax, ymax, zmax))
    """
    _, _, bbox = stl_info(filepath)
    return bbox


def copy_stl(
    src: str | Path,
    dst: str | Path,
    name: str | None = None,
    *,
    info: tuple[str, int, BBox] | None = None,
) -> int:
    """Copy STL to destination, optionally rewriting solid name.

    Uses a zero-memory fast OS copy only when no rename is requested; otherwise
    streams header/footer substitution with verbatim coordinate preservation so
    that every ``solid``/``endsolid`` line is rewritten. The fast path must not
    be taken merely because the requested name equals the *first* solid name:
    multi-body CAD exports can start with that solid while containing others,
    and those must still be merged.

    Returns:
        Number of triangles.
    """
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    if info is None:
        info = stl_info(src)
    _, n_triangles, _ = info

    if name is None:
        shutil.copy2(src, dst)
    else:
        # Stream lines directly, preserving exact CAD vertex representations.
        # Every solid/endsolid line is rewritten so that multi-body CAD exports
        # are merged under the single solid name snappyHexMesh expects.
        with open(src, "r", encoding="utf-8", errors="replace") as fin, \
             open(dst, "w", encoding="utf-8", newline="\n") as fout:
            for line in fin:
                stripped = line.strip()
                if stripped.startswith("solid"):
                    fout.write(f"solid {name}\n")
                elif stripped.startswith("endsolid"):
                    fout.write(f"endsolid {name}\n")
                else:
                    fout.write(line)

    return n_triangles
