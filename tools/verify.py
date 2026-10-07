#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import re
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project.governance import _atomic_json, verification_identity
from project.telemetry_contract import IST


def run(command: list[str], environment: dict) -> dict:
    print("+", " ".join(command))
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    result.check_returncode()
    counts = re.findall(r"Ran (\d+) tests? in", result.stderr)
    return {"command": " ".join(command[2:]), "status": "PASSED",
            "tests": int(counts[-1]) if counts else 0}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the complete Project Anissa verification suite")
    parser.add_argument("--with-preflight", action="store_true")
    parser.add_argument("--record", action="store_true", help="Record revision-bound verification in the private instance")
    args = parser.parse_args()
    private = (ROOT / "schemas/repository_classification.json").is_file()
    if args.record and not args.with_preflight:
        parser.error("--record requires --with-preflight for the installed instance")
    identity = verification_identity(ROOT)
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    checks = []
    with tempfile.TemporaryDirectory() as directory:
        if not private:
            from project.instance import initialize_instance
            instance = initialize_instance(ROOT, Path(directory) / "instance")
            environment["PROJECT_ANISSA_INSTANCE"] = str(instance.instance_root)
        for folder in ("tests", "anissa/tests", "soldiers/thula/tests"):
            if (ROOT / folder).is_dir():
                checks.append(run([sys.executable, "-B", "-m", "unittest", "discover", "-s", folder, "-v"], environment))
        if private:
            checks.append(run([sys.executable, "-B", "tools/repository_hygiene.py", "--summary"], environment))
        if args.with_preflight or not private:
            command = [sys.executable, "-B", "tools/preflight.py"]
            if not private:
                command += ["--automations-root", str(Path(directory) / "automations")]
            checks.append(run(command, environment))
    if args.record and not private:
        checks.append(run([sys.executable, "-B", "tools/preflight.py"],
                          {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}))
    if identity != verification_identity(ROOT):
        raise RuntimeError("Source changed during verification; rerun before release")
    if args.record:
        from project.environment import resolve_environment
        deployment = resolve_environment(ROOT)
        timestamp = datetime.now(IST).isoformat(timespec="seconds")
        record = {**identity, "timestamp": timestamp, "status": "PASSED", "checks": checks,
                  "operational_acceptance": "CONFIGURATION_VALIDATED_ONLY"}
        _atomic_json(deployment.instance_root / "runtime/release_verification.json", record)
        settings = json.loads(deployment.runtime_settings_path.read_text())
        _atomic_json(deployment.runtime_settings_path, {**settings, "last_preflight": {
            "timestamp": timestamp, "status": "PASS", "mode": settings["mode"],
            "schema_version": settings["schema_version"],
            "tests": f"{sum(check['tests'] for check in checks)} tests passed",
            "active_bound_automations": len(settings.get("automation_bindings", {})),
            **identity,
        }})
    print("PROJECT ANISSA VERIFICATION OK")


if __name__ == "__main__":
    main()
