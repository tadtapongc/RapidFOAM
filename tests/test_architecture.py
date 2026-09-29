"""Architecture boundary tests (refactor-prep/TARGET_ARCHITECTURE.md §6).

Walks the ``ast`` import graph of ``src/rapidfoam`` and fails when a bounded
context imports a context it must not. This keeps the Phase 1–6 boundaries from
eroding. Two pre-existing inversions are listed in ``KNOWN_DEBT`` with the phase
that removes them; a new violation is a hard failure.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "rapidfoam"
sys.path.insert(0, str(SRC.parent))

# source context -> contexts it must not import
FORBIDDEN = {
    "core": {"geometry", "meshing", "postproc", "writers", "web", "cli"},
    "geometry": {"meshing", "postproc", "writers", "web", "cli"},
    "meshing": {"postproc", "writers", "web", "cli"},
    "postproc": {"meshing", "writers", "web", "cli"},
    "writers": {"postproc", "web", "cli"},
    "casegen": {"postproc", "web", "cli"},
    "web": {"cli"},
}

# (source module, imported module) pairs that are known debt.
KNOWN_DEBT: set[tuple[str, str]] = set()

# (source context, imported context) pairs allowed during migration.
CONTEXT_DEBT: set[tuple[str, str]] = set()

CONTEXTS = set(FORBIDDEN) | {"cli"}


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.append(node.module)
    return modules


def _context(module: str) -> str | None:
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "rapidfoam" and parts[1] in CONTEXTS:
        return parts[1]
    return None


class ArchitectureBoundaryTest(unittest.TestCase):
    def test_no_forbidden_context_imports(self):
        violations: list[str] = []
        for path in sorted(SRC.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            source_module = _module_name(path)
            source_context = _context(source_module)
            if source_context is None or source_context not in FORBIDDEN:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for imported in _imported_modules(tree):
                imported_context = _context(imported)
                if imported_context is None:
                    continue
                if imported_context not in FORBIDDEN[source_context]:
                    continue
                if (source_context, imported_context) in CONTEXT_DEBT:
                    continue
                if (source_module, imported) in KNOWN_DEBT:
                    continue
                violations.append(f"{source_module} -> {imported}")
        self.assertEqual([], sorted(set(violations)),
                         "forbidden cross-context imports found")


if __name__ == "__main__":
    unittest.main()