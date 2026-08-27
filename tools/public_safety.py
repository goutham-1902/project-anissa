#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


TEXT_SUFFIXES = {
    "", ".css", ".html", ".ini", ".js", ".json", ".md", ".plist",
    ".py", ".toml", ".txt", ".yaml", ".yml",
}
PRIVATE_MARKERS = tuple(part.lower() for part in (
    "gou" + "tham",
    "luna" + "tic",
    "sds" + "kodali",
    "mis" + "tress",
    "ero" + "tic",
    "sex" + "ual",
    "adult" + "_preferences",
    "attraction" + "_policy",
    "explicit" + "_examples",
    "persona" + "_research_notes",
    "vilt" + "rum",
    "inv" + "incible",
))
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|password|client[_-]?secret)\s*[:=]\s*['\"][^'\"]+"),
)
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
ABSOLUTE_PATH_MARKERS = ("/" + "Users" + "/", "C:" + "\\Users" + "\\")


def scan_text(text: str, label: str) -> list[str]:
    lowered = text.lower()
    problems = [f"{label}: private marker {marker!r}" for marker in PRIVATE_MARKERS if marker in lowered]
    problems.extend(
        f"{label}: absolute local path" for marker in ABSOLUTE_PATH_MARKERS if marker in text
    )
    if EMAIL.search(text):
        problems.append(f"{label}: email address")
    if UUID.search(text):
        problems.append(f"{label}: UUID-like identifier")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            problems.append(f"{label}: possible secret")
    return problems


def audit_public_tree(root: Path, *, include_history: bool = True) -> dict:
    root = Path(root).resolve()
    problems = []
    files = sorted(
        path for path in root.rglob("*")
        if (
            path.is_file()
            and ".git" not in path.relative_to(root).parts
            and "__pycache__" not in path.relative_to(root).parts
            and path.suffix != ".pyc"
            and path.name != ".DS_Store"
        )
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.suffix.lower() not in TEXT_SUFFIXES:
            problems.append(f"{relative}: unexpected binary or file type")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            problems.append(f"{relative}: non-UTF-8 content")
            continue
        problems.extend(scan_text(text, relative))

    history_objects = 0
    git_root = root / ".git"
    if include_history and git_root.is_dir():
        listing = subprocess.check_output(
            ["git", "-C", str(root), "rev-list", "--objects", "--all"],
            text=True,
        ).splitlines()
        for row in listing:
            object_id, _, name = row.partition(" ")
            if not name:
                continue
            history_objects += 1
            problems.extend(scan_text(name, f"history path {name}"))
            kind = subprocess.check_output(
                ["git", "-C", str(root), "cat-file", "-t", object_id],
                text=True,
            ).strip()
            if kind != "blob":
                continue
            raw = subprocess.check_output(
                ["git", "-C", str(root), "cat-file", "-p", object_id]
            )
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                problems.append(f"history blob {name}: binary content")
                continue
            problems.extend(scan_text(text, f"history blob {name}"))
    return {
        "root": str(root),
        "file_count": len(files),
        "history_objects": history_objects,
        "problems": sorted(set(problems)),
        "ok": not problems,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit a clean Project Anissa public tree")
    parser.add_argument("root", type=Path)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()
    result = audit_public_tree(args.root, include_history=not args.no_history)
    print(json.dumps(result, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
