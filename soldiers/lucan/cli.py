#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from project.discovery_contract import discovery_publication_payload
from project.discovery_publication import publish_discovery, record_discovery_failure
from project.environment import ProjectEnvironment, resolve_environment
from soldiers.lucan.prompt_adapter import (
    assemble_publication,
    build_prompt,
    execution_channel,
)


ENVIRONMENT = resolve_environment(ROOT)


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid JSON input: {path}") from exc


def _moment(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO-8601 datetime") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a timezone")
    return result


def _notification(settings: object, status: str, candidate_count: int) -> dict:
    policy = settings.get("notification_policy") if isinstance(settings, dict) else None
    if not isinstance(policy, dict):
        raise ValueError("Lucan notification policy is unavailable")
    if status == "COMPLETE":
        key = "complete_with_delta" if candidate_count else "complete_no_change"
    elif status == "PARTIAL":
        key = "partial"
    else:
        key = "failed"
    action = str(policy.get(key) or "").strip().upper()
    if action not in {"QUIET", "REPORT"}:
        raise ValueError(f"Lucan notification policy is invalid for {key}")
    return {"action": action, "reason": key}


def _failure(settings: object, stage: str, message: str) -> tuple[str, str, dict]:
    stage = " ".join(str(stage or "").split())
    message = " ".join(str(message or "").split())
    if not stage or not message or len(stage) > 80 or len(message) > 800:
        raise ValueError("failure stage or message is missing or exceeds its size limit")
    policy = settings.get("notification_policy") if isinstance(settings, dict) else None
    action = str((policy or {}).get("adapter_or_publication_error") or "").upper()
    if action != "REPORT":
        raise ValueError("adapter failure notification policy must be REPORT")
    return stage, message, {"action": action, "reason": "adapter_or_publication_error"}


def prepare_execution(brief: object, settings: object) -> dict:
    """Build one bounded prompt without browsing, persistence or campaign access."""

    package = build_prompt(brief, settings)
    return {
        **asdict(package),
        "publication_channel": execution_channel(settings),
    }


def finalize_execution(
    environment: ProjectEnvironment,
    brief: object,
    result: object,
    settings: object,
    *,
    started_at: datetime,
    completed_at: datetime,
) -> dict:
    """Validate a result and publish only when the private LIVE gate permits it."""

    channel = execution_channel(settings)
    publication = assemble_publication(
        brief,
        result,
        started_at=started_at,
        completed_at=completed_at,
    )
    payload = discovery_publication_payload(publication)
    notification = _notification(
        settings, publication.status, len(publication.candidates)
    )
    if channel == "SHADOW":
        return {
            "action": "VALIDATED_SHADOW",
            "published": False,
            "notification": notification,
            "publication": payload,
        }
    publication_root = environment.worker("lucan").publication_root
    if publication.status == "FAILED":
        stored = record_discovery_failure(
            publication_root,
            channel="LIVE",
            stage="research",
            message="; ".join(publication.source_failures),
            attempted_at=completed_at,
        )
    else:
        stored = publish_discovery(
            publication_root,
            payload,
            channel="LIVE",
            attempted_at=completed_at,
        )
    return {
        "action": "PUBLISHED_LIVE",
        "published": True,
        "notification": notification,
        "status": dict(stored.status),
    }


def record_execution_failure(
    environment: ProjectEnvironment,
    settings: object,
    *,
    stage: str,
    message: str,
    attempted_at: datetime,
) -> dict:
    """Report shadow failures locally; persist only under a coherent LIVE gate."""

    channel = execution_channel(settings)
    stage, message, notification = _failure(settings, stage, message)
    result = {
        "action": "REPORT_FAILURE",
        "published": False,
        "notification": notification,
        "failure": {"stage": stage, "message": message},
    }
    if channel == "SHADOW":
        return result
    stored = record_discovery_failure(
        environment.worker("lucan").publication_root,
        channel="LIVE",
        stage=stage,
        message=message,
        attempted_at=attempted_at,
    )
    return {**result, "published": True, "status": dict(stored.status)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lucan discovery execution adapter")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Render one bounded research prompt")
    prepare.add_argument("--brief", type=Path, required=True)
    finalize = commands.add_parser("finalize", help="Validate and gate one research result")
    finalize.add_argument("--brief", type=Path, required=True)
    finalize.add_argument("--result", type=Path, required=True)
    finalize.add_argument("--started-at", type=_moment, required=True)
    finalize.add_argument("--completed-at", type=_moment, required=True)
    failure = commands.add_parser("record-failure", help="Gate one execution failure")
    failure.add_argument("--stage", required=True)
    failure.add_argument("--message", required=True)
    failure.add_argument("--attempted-at", type=_moment, required=True)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = _read_json(ENVIRONMENT.worker("lucan").settings_path)
        if args.command == "prepare":
            output = prepare_execution(_read_json(args.brief), settings)
        elif args.command == "finalize":
            output = finalize_execution(
                ENVIRONMENT,
                _read_json(args.brief),
                _read_json(args.result),
                settings,
                started_at=args.started_at,
                completed_at=args.completed_at,
            )
        else:
            output = record_execution_failure(
                ENVIRONMENT,
                settings,
                stage=args.stage,
                message=args.message,
                attempted_at=args.attempted_at,
            )
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"action": "REPORT_FAILURE", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
