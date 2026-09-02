#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anissa.core import AnissaCore
from logic.telemetry import telemetry_context
from logic.workbook_io import WorkbookGateway
from project.dispatch import DispatchGate
from project.environment import ProjectEnvironment, resolve_environment


ENVIRONMENT = resolve_environment(ROOT)


def _core(
    gateway: WorkbookGateway,
    environment: ProjectEnvironment | None = None,
) -> AnissaCore:
    return AnissaCore(
        environment or ENVIRONMENT,
        gateway=gateway,
        today_provider=date.today,
        telemetry_loader=telemetry_context,
    )


def compact_snapshot(
    gateway: WorkbookGateway,
    workflow: str,
    *,
    environment: ProjectEnvironment | None = None,
    week_ending: date | None = None,
    as_of: datetime | None = None,
) -> dict:
    """Compatibility interface retained for role prompts and existing tests."""
    return _core(gateway, environment).snapshot(
        workflow,
        week_ending=week_ending,
        as_of=as_of,
    )


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an ISO date (YYYY-MM-DD)") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("status")
    commands.add_parser("gate")
    commands.add_parser("list-tasks")
    detail = commands.add_parser("task-detail")
    detail.add_argument("task_id")
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument(
        "--workflow",
        default="status",
        choices=[
            "status", "weekday-morning", "weekday-reminder", "weekend-morning",
            "weekend-reminder", "weekday-close", "weekly-audit", "plan-impact",
        ],
    )
    snapshot.add_argument("--week-ending", type=_iso_date)
    task_status = commands.add_parser("set-task-status")
    task_status.add_argument("task_id")
    task_status.add_argument("status")
    task_status.add_argument("--evidence", default="")
    task_status.add_argument("--blocker", default="")
    task_status.add_argument("--unblock-action", default="")
    task_status.add_argument("--actual-minutes", type=int)
    control = commands.add_parser("set-control")
    control.add_argument("key")
    control.add_argument("value")
    reminder = commands.add_parser("record-reminder")
    reminder.add_argument("task_ids", nargs="+")
    preview = commands.add_parser("preview-replan")
    preview.add_argument("--event-json", required=True)
    preview.add_argument("--changes-json", default="[]")
    apply_command = commands.add_parser("apply-replan")
    apply_command.add_argument("--event-json", required=True)
    apply_command.add_argument("--changes-json", default="[]")
    apply_command.add_argument("--confirm", required=True)
    audit = commands.add_parser("record-weekly-audit")
    audit.add_argument("--week-ending", type=_iso_date, required=True)
    audit.add_argument("--strongest-achievement", default="")
    audit.add_argument("--failure-pattern", default="")
    audit.add_argument("--next-priorities", default="")
    audit.add_argument("--exact-next-action", required=True)
    audit.add_argument("--summary", default="")
    claim = commands.add_parser("claim-dispatch")
    claim.add_argument("slot")
    claim.add_argument("--date", type=_iso_date)
    claim.add_argument("--lease-seconds", type=int, default=3600)
    for name in ("complete-dispatch", "fail-dispatch"):
        finish = commands.add_parser(name)
        finish.add_argument("dispatch_id")
        finish.add_argument("claim_token")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "claim-dispatch":
        result = DispatchGate(ENVIRONMENT).claim(
            args.slot,
            on=args.date,
            lease_seconds=args.lease_seconds,
        )
        print(json.dumps(result, separators=(",", ":")))
        return 0
    if args.cmd in {"complete-dispatch", "fail-dispatch"}:
        gate = DispatchGate(ENVIRONMENT)
        result = (
            gate.complete(args.dispatch_id, args.claim_token)
            if args.cmd == "complete-dispatch"
            else gate.fail(args.dispatch_id, args.claim_token)
        )
        print(json.dumps(result, separators=(",", ":")))
        return 0
    gateway = WorkbookGateway(environment=ENVIRONMENT)
    core = _core(gateway)
    agenda = core.agenda
    if args.cmd == "status":
        result = agenda.control()
    elif args.cmd == "gate":
        result = core.effective_gate()
    elif args.cmd == "list-tasks":
        result = agenda.list_tasks()
    elif args.cmd == "task-detail":
        result = agenda.task_detail(args.task_id)
        if result is None:
            raise SystemExit(f"Unknown task: {args.task_id}")
    elif args.cmd == "snapshot":
        try:
            result = core.snapshot(args.workflow, week_ending=args.week_ending)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.cmd == "set-task-status":
        agenda.set_task_status(
            args.task_id,
            args.status,
            evidence=args.evidence,
            blocker=args.blocker,
            unblock_action=args.unblock_action,
            actual_minutes=args.actual_minutes,
        )
        print("updated")
        return 0
    elif args.cmd == "set-control":
        agenda.set_control(args.key, args.value)
        print("updated")
        return 0
    elif args.cmd == "record-reminder":
        agenda.record_reminders(args.task_ids)
        result = {"updated": args.task_ids}
    elif args.cmd in {"preview-replan", "apply-replan"}:
        event = json.loads(args.event_json)
        changes = json.loads(args.changes_json)
        if args.cmd == "preview-replan":
            result = agenda.preview_replan(event, changes)
        else:
            if args.confirm != "APPLY_APPROVED_REPLAN":
                raise SystemExit("Exact --confirm APPLY_APPROVED_REPLAN is required.")
            result = agenda.apply_replan(event, changes)
    elif args.cmd == "record-weekly-audit":
        try:
            action, metrics = agenda.record_weekly_audit(
                week_ending=args.week_ending,
                strongest_achievement=args.strongest_achievement,
                failure_pattern=args.failure_pattern,
                next_priorities=args.next_priorities,
                exact_next_action=args.exact_next_action,
                summary=args.summary,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        result = {"action": action, "metrics": metrics}
    else:
        return 2
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
