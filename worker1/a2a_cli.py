#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project.environment import resolve_environment
from project.a2a_workflow import run_a2a_csv_sync, run_a2a_sync
from project.dashboard import compose_dashboard
from worker1.src.dashboard_server import run_server
from worker1.src.forest_csv import decode_csv_base64
from worker1.src.sync import load_payload, record_failure, record_stale


WORKER = ROOT / "worker1"
ENVIRONMENT = resolve_environment(ROOT)
PRIVATE = ENVIRONMENT.a2a_private_root
SHARED = ENVIRONMENT.telemetry_root
DEFAULTS = {
    "store_path": PRIVATE / "a2a_state.sqlite3",
    "worklog_path": SHARED / "worklog.csv",
    "status_path": SHARED / "status.json",
    "static_root": WORKER / "dashboard",
}


def _paths(args) -> dict:
    return {
        key: Path(getattr(args, key, None) or default)
        for key, default in DEFAULTS.items()
    }


def _healthy(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with urlopen(f"http://{host}:{port}/health", timeout=timeout) as response:
            return response.status == 200 and json.loads(response.read()).get("ok") is True
    except Exception:
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A2A focus telemetry worker")
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="Import one completed Forest extraction")
    sync.add_argument("--forest-json", required=True)

    drive = sub.add_parser("sync-drive", help="Import one complete Forest CSV fetched from Google Drive")
    drive_input = drive.add_mutually_exclusive_group(required=True)
    drive_input.add_argument("--forest-csv")
    drive_input.add_argument("--csv-base64")
    drive.add_argument("--captured-at", required=True)
    drive.add_argument("--source-file-id", required=True)
    drive.add_argument("--source-modified-at", required=True)

    failure = sub.add_parser("record-failure", help="Record acquisition/processing failure without replacing worklog")
    failure.add_argument("--stage", required=True)
    failure.add_argument("--message", required=True)

    stale = sub.add_parser("record-stale", help="Record an expected missing export without replacing worklog")
    stale.add_argument("--stage", required=True)
    stale.add_argument("--message", required=True)

    sub.add_parser("snapshot", help="Print the compact dashboard snapshot")

    serve = sub.add_parser("serve", help="Run the Mac-local dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    ensure = sub.add_parser("ensure-server", help="Start the dashboard when its health endpoint is down")
    ensure.add_argument("--host", default="127.0.0.1")
    ensure.add_argument("--port", type=int, default=8765)
    ensure.add_argument("--wait-seconds", type=float, default=5.0)

    for child in (sync, drive, failure, stale, serve, ensure):
        child.add_argument("--store-path")
        child.add_argument("--worklog-path")
        child.add_argument("--status-path")
        child.add_argument("--static-root")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    paths = _paths(args)
    if args.command == "sync":
        result = run_a2a_sync(
            environment=ENVIRONMENT,
            payload=load_payload(args.forest_json),
            **{key: paths[key] for key in ("store_path", "worklog_path", "status_path")},
        )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    if args.command == "sync-drive":
        csv_data = (
            Path(args.forest_csv).read_bytes()
            if args.forest_csv else decode_csv_base64(args.csv_base64)
        )
        result = run_a2a_csv_sync(
            environment=ENVIRONMENT,
            csv_data=csv_data,
            captured_at=args.captured_at,
            source_file_id=args.source_file_id,
            source_modified_at=args.source_modified_at,
            **{key: paths[key] for key in ("store_path", "worklog_path", "status_path")},
        )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    if args.command == "record-failure":
        result = record_failure(status_path=paths["status_path"], stage=args.stage, message=args.message)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    if args.command == "record-stale":
        result = record_stale(status_path=paths["status_path"], stage=args.stage, message=args.message)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    if args.command == "snapshot":
        result = compose_dashboard(
            environment=ENVIRONMENT,
            worklog_path=DEFAULTS["worklog_path"], status_path=DEFAULTS["status_path"],
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "serve":
        run_server(
            host=args.host, port=args.port, static_root=paths["static_root"],
            worklog_path=paths["worklog_path"], status_path=paths["status_path"],
            environment=ENVIRONMENT,
        )
        return 0
    if args.command == "ensure-server":
        if _healthy(args.host, args.port):
            print(json.dumps({"state": "running", "url": f"http://{args.host}:{args.port}/"}))
            return 0
        PRIVATE.mkdir(parents=True, exist_ok=True)
        log = (PRIVATE / "dashboard.log").open("ab")
        command = [
            sys.executable, str(Path(__file__).resolve()), "serve",
            "--host", args.host, "--port", str(args.port),
            "--worklog-path", str(paths["worklog_path"]),
            "--status-path", str(paths["status_path"]),
            "--static-root", str(paths["static_root"]),
        ]
        process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
        (PRIVATE / "dashboard.pid").write_text(str(process.pid), encoding="utf-8")
        deadline = time.monotonic() + args.wait_seconds
        while time.monotonic() < deadline:
            if _healthy(args.host, args.port):
                print(json.dumps({"state": "started", "pid": process.pid, "url": f"http://{args.host}:{args.port}/"}))
                return 0
            time.sleep(0.2)
        print(json.dumps({"state": "failed", "pid": process.pid}), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
