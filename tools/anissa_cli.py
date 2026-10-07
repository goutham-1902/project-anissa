#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from logic.telemetry import telemetry_context
from project.dispatch import DispatchGate
from project.environment import ProjectEnvironment, resolve_environment
from project.recovery import latest_audit_due_week_end, plan_recovery
from project.telemetry_contract import IST


ENVIRONMENT = resolve_environment(ROOT)

if TYPE_CHECKING:
    from logic.workbook_io import WorkbookGateway


def _new_gateway(environment: ProjectEnvironment):
    from logic.workbook_io import WorkbookGateway
    return WorkbookGateway(environment=environment)


def _core(
    gateway: WorkbookGateway,
    environment: ProjectEnvironment | None = None,
    *,
    today: date | None = None,
) -> "AnissaCore":
    from anissa.core import AnissaCore
    return AnissaCore(
        environment or ENVIRONMENT,
        gateway=gateway,
        today_provider=(lambda: today) if today is not None else date.today,
        telemetry_loader=telemetry_context,
    )


def compact_snapshot(
    gateway: WorkbookGateway,
    workflow: str,
    *,
    environment: ProjectEnvironment | None = None,
    agenda_id: str | None = None,
    week_ending: date | None = None,
    as_of: datetime | None = None,
) -> dict:
    """Compatibility interface retained for role prompts and existing tests."""
    return _core(gateway, environment).snapshot(
        workflow,
        agenda_id=agenda_id,
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
    campaign_commands = []
    campaign_commands.append(commands.add_parser("status"))
    campaign_commands.append(commands.add_parser("gate"))
    campaign_commands.append(commands.add_parser("portfolio"))
    campaign_commands.append(commands.add_parser("list-tasks"))
    detail = commands.add_parser("task-detail")
    campaign_commands.append(detail)
    detail.add_argument("task_id")
    snapshot = commands.add_parser("snapshot")
    campaign_commands.append(snapshot)
    snapshot.add_argument(
        "--workflow",
        default="status",
        choices=[
            "status", "weekday-morning", "weekday-reminder", "weekend-morning",
            "weekend-reminder", "weekday-close", "weekly-audit", "plan-impact",
        ],
    )
    snapshot.add_argument("--week-ending", type=_iso_date)
    snapshot.add_argument("--as-of", type=datetime.fromisoformat, help="logical reporting time for a recovered close")
    recovery = commands.add_parser("recovery-plan")
    campaign_commands.append(recovery)
    recovery.add_argument("--role", choices=["COMMAND", "WEEKDAY_OPS", "WEEKEND"], required=True)
    recovery.add_argument("--as-of", type=datetime.fromisoformat)
    task_status = commands.add_parser("set-task-status")
    campaign_commands.append(task_status)
    task_status.add_argument("task_id")
    task_status.add_argument("status")
    task_status.add_argument("--evidence", default="")
    task_status.add_argument("--blocker", default="")
    task_status.add_argument("--unblock-action", default="")
    task_status.add_argument("--actual-minutes", type=int)
    control = commands.add_parser("set-control")
    campaign_commands.append(control)
    control.add_argument("key")
    control.add_argument("value")
    reminder = commands.add_parser("record-reminder")
    campaign_commands.append(reminder)
    reminder.add_argument("task_ids", nargs="+")
    preview = commands.add_parser("preview-replan")
    campaign_commands.append(preview)
    preview.add_argument("--event-json", required=True)
    preview.add_argument("--changes-json", default="[]")
    apply_command = commands.add_parser("apply-replan")
    campaign_commands.append(apply_command)
    apply_command.add_argument("--event-json", required=True)
    apply_command.add_argument("--changes-json", default="[]")
    apply_command.add_argument("--confirm", required=True)
    audit = commands.add_parser("record-weekly-audit")
    campaign_commands.append(audit)
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
        if name == "complete-dispatch":
            finish.add_argument("--message-expected", action="store_true")
    delivered = commands.add_parser("deliver-dispatch")
    delivered.add_argument("dispatch_id")
    delivered.add_argument("claim_token")
    superseded = commands.add_parser("supersede-dispatch")
    superseded.add_argument("dispatch_ids", nargs="+")
    for command in campaign_commands:
        command.add_argument(
            "--agenda",
            dest="agenda_id",
            default=None,
            help="registered agenda ID (defaults to the portfolio default)",
        )
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
    if args.cmd in {"complete-dispatch", "fail-dispatch", "deliver-dispatch", "supersede-dispatch"}:
        gate = DispatchGate(ENVIRONMENT)
        if args.cmd == "complete-dispatch":
            result = gate.complete(args.dispatch_id, args.claim_token, message_expected=args.message_expected)
        elif args.cmd == "fail-dispatch":
            result = gate.fail(args.dispatch_id, args.claim_token)
        elif args.cmd == "deliver-dispatch":
            result = gate.deliver(args.dispatch_id, args.claim_token)
        else:
            result = gate.supersede(args.dispatch_ids)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    gateway = _new_gateway(ENVIRONMENT)
    snapshot_moment = args.as_of if args.cmd == "snapshot" else None
    if snapshot_moment is not None:
        snapshot_moment = (snapshot_moment.replace(tzinfo=IST) if snapshot_moment.tzinfo is None
                           else snapshot_moment.astimezone(IST))
    core = _core(gateway, today=snapshot_moment.date()) if snapshot_moment is not None else _core(gateway)
    agenda_id = args.agenda_id
    mutating = args.cmd in {
        "set-task-status",
        "set-control",
        "record-reminder",
        "apply-replan",
        "record-weekly-audit",
    }
    agenda = (
        None
        if args.cmd == "portfolio"
        else core.open_agenda(agenda_id, for_mutation=mutating)
    )
    if args.cmd == "portfolio":
        result = asdict(core.portfolio_projection(agenda_id))
    elif args.cmd == "status":
        result = agenda.control()
    elif args.cmd == "gate":
        result = core.effective_gate(agenda_id=agenda_id)
    elif args.cmd == "list-tasks":
        result = agenda.list_tasks()
    elif args.cmd == "task-detail":
        result = agenda.task_detail(args.task_id)
        if result is None:
            raise SystemExit(f"Unknown task: {args.task_id}")
    elif args.cmd == "snapshot":
        try:
            result = core.snapshot(
                args.workflow,
                agenda_id=agenda_id,
                week_ending=args.week_ending,
                as_of=snapshot_moment,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.cmd == "recovery-plan":
        moment = args.as_of or datetime.now(IST)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=IST)
        else:
            moment = moment.astimezone(IST)
        receipts = DispatchGate(ENVIRONMENT).state()
        roles = ("WEEKDAY_OPS", "WEEKEND") if args.role == "COMMAND" else (args.role,)
        plans = []
        for role in roles:
            initial = plan_recovery(role, now=moment, receipts=receipts)
            context = core.recovery_state(
                role, as_of=moment, agenda_id=args.agenda_id,
                since=datetime.fromisoformat(initial["since"]) if initial["since"] else None,
                week_ending=latest_audit_due_week_end(moment),
            )
            plan = plan_recovery(role, now=moment, receipts=receipts, state=context)
            plans.append({**plan, "context": context} if plan["actions"] else plan)
        if args.role == "COMMAND":
            settings = json.loads(ENVIRONMENT.runtime_settings_path.read_text())
            result = {"role": "COMMAND", "recoveries": [
                {**plan, "target_thread_id": settings["chat_bindings"][plan["role"]]}
                for plan in plans if plan["actions"]
            ]}
            if core.effective_gate(agenda_id=args.agenda_id).get("effective_mode") == "LIVE":
                worker_plan = plan_recovery("THULA", now=moment, receipts=receipts)
                thread = settings.get("worker_chat_bindings", {}).get("THULA")
                if thread and worker_plan["actions"]:
                    result["worker_recovery"] = {"target_thread_id": thread, "role": "THULA"}
        else:
            result = plans[0]
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
