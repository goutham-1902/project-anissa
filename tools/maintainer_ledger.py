#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project.environment import resolve_environment
from project.governance import Governance, MAINTAINER_IDS


def main() -> None:
    parser = argparse.ArgumentParser(description="Project Anissa maintainer governance")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--version", required=True)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--maintainer", choices=MAINTAINER_IDS, required=True)
    evaluate.add_argument("files", nargs="+")
    append = commands.add_parser("append")
    append.add_argument("--maintainer", choices=MAINTAINER_IDS, required=True)
    append.add_argument("--writer", choices=MAINTAINER_IDS, required=True)
    append.add_argument("--change-json", required=True)
    args = parser.parse_args()

    governance = Governance(resolve_environment(ROOT))
    if args.command == "init":
        result = [str(path) for path in governance.initialize_ledgers(args.version)]
    elif args.command == "evaluate":
        result = governance.evaluate_scope(args.maintainer, args.files)
    else:
        result = governance.append_change(
            args.maintainer,
            args.writer,
            json.loads(args.change_json),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
