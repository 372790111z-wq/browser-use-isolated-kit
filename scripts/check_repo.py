#!/usr/bin/env python3
"""Fail when repository files contain device-local or sensitive artifacts."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", "__pycache__", ".pytest_cache"}
FORBIDDEN_PATHS = (
    re.compile(r"/Users/[^/@\s]+/"),
    re.compile(r"/home/[^/@\s]+/"),
    re.compile(r"file:///Users/"),
)
FORBIDDEN_NAMES = {
    "Cookies",
    "History",
    "Login Data",
    "Local State",
    "Preferences",
    "state.json",
    "state.lock",
}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|password|client[_-]?secret)"
    r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{12,}"
)


def main() -> int:
    problems = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        relative = path.relative_to(ROOT)
        if path.name in FORBIDDEN_NAMES:
            problems.append(f"{relative}: forbidden browser-state filename")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in FORBIDDEN_PATHS:
            if pattern.search(text):
                problems.append(f"{relative}: device-local absolute path")
                break
        if SECRET_ASSIGNMENT.search(text):
            problems.append(f"{relative}: possible embedded secret")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print("Repository privacy and portability checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
