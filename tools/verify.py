#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        instance = Path(directory) / "instance"
        subprocess.run(
            [sys.executable, "tools/init_instance.py", str(instance)],
            cwd=ROOT,
            check=True,
        )
        environment = os.environ.copy()
        environment["HOME"] = directory
        environment["PROJECT_ANISSA_INSTANCE"] = str(instance)
        subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=ROOT,
            env=environment,
            check=True,
        )
        subprocess.run(
            [sys.executable, "tools/preflight.py"],
            cwd=ROOT,
            env=environment,
            check=True,
        )
    print("PUBLIC PROJECT ANISSA VERIFICATION OK")


if __name__ == "__main__":
    main()
