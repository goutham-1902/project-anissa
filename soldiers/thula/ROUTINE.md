# Thula nightly Forest routine

Use the interpreter configured in Thula's environment-resolved settings and
pass `-B`. Use Asia/Kolkata time and remain silent on success.

At the next scheduled check or an actual Thula interaction/resume, run
`soldiers/thula/cli.py recovery-plan` once. It uses only technical settings and
shared dispatch receipts. If there is no action, stop silently. A `delivery`
action handles one pending technical report; claim its dated receipt, deliver
the report once, and mark it delivered without fetching or importing again.
For a `run` action, claim `thula-ingestion` through the shared dispatch CLI
using `--date` equal to the plan's `run_date`. If the claim is `busy` or
`existing`, stop silently. Keep its `dispatch_id` and `claim_token` in the
technical command flow, never in a model-facing report.

1. Run `soldiers/thula/cli.py ensure-server --host 127.0.0.1 --port 8765`.
2. Read the approved private Drive folder ID from Thula's settings. List only
   its direct CSV children and select the newest by provider `modified_time`.
3. Fetch the newest complete full-history export once as base64 without
   reproducing its name, notes or raw rows in the response. Do not replay older
   missed slots.
4. Run `soldiers/thula/cli.py sync-drive` with the complete base64 payload, file
   ID and modified timestamp. Use that timestamp for both `--captured-at` and
   `--source-modified-at`.
5. Treat an unchanged export as a silent `STALE` no-op. Preserve pending gaps
   for a later full-history export; never record false zero activity.
6. On a new export, verify dashboard health, `COMPLETE` status, source identity
   and worklog checksum. Never retain the raw CSV.
7. Complete the claimed technical receipt with `message_expected=false` for
   `COMPLETE` or `STALE`. On a real acquisition or processing failure, fail the
   receipt. Mark older technical receipt IDs from the plan's `superseded_ids`
   through the shared receipt interface after handling the selected slot. A
   repeat reconnect must not re-import or repeat an unchanged failure report.

On genuine acquisition or processing failure, use `record-failure`, preserve
the last good publication, and report the compact action required. Never modify
campaign state, code, settings, bindings or automations during this routine.

Monday–Sunday is the reporting week. Sunday 20:00 IST closes its telemetry
window; the 21:00 Sunday check should ingest the export before Earth's 21:30
audit. A missed or stale export remains backfillable, not zero. The daily
`[20:00,20:00)` accounting boundary does not change.
