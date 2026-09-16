"""Resolve canvas monorepo root (directory containing package.json + packages/)."""

from __future__ import annotations

from pathlib import Path


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from *start* until the canvas monorepo root is found."""
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / "package.json").is_file() and (
            (parent / "packages" / "web").is_dir() or (parent / "packages" / "api").is_dir()
        ):
            return parent
    # Docker API image: /app/app/...
    return here.parents[3]


def web_public_dir(root: Path | None = None) -> Path:
    base = root or find_project_root()
    return base / "packages" / "web" / "public"
