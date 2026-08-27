#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project.instance import initialize_instance


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Project Anissa private instance")
    parser.add_argument("instance_root", type=Path)
    args = parser.parse_args()
    environment = initialize_instance(ROOT, args.instance_root)
    print(f"Project Anissa instance ready: {environment.instance_root}")
    print("Deployment pointer unchanged; activation was not performed.")


if __name__ == "__main__":
    main()
