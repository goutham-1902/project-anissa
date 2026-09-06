# Thula nightly Forest routine

Use the interpreter configured in Thula's environment-resolved settings and
pass `-B`. Use Asia/Kolkata time and remain silent on success.

1. Run `soldiers/thula/cli.py ensure-server --host 127.0.0.1 --port 8765`.
2. Read the approved private Drive folder ID from Thula's settings. List only
   its direct CSV children and select the newest by provider `modified_time`.
3. Fetch the complete file as base64 without reproducing its name, notes or raw
   rows in the response.
4. Run `soldiers/thula/cli.py sync-drive` with the complete base64 payload, file
   ID and modified timestamp. Use that timestamp for both `--captured-at` and
   `--source-modified-at`.
5. Treat an unchanged export as a silent `STALE` no-op. Preserve pending gaps
   for a later full-history export; never record false zero activity.
6. On a new export, verify dashboard health, `COMPLETE` status, source identity
   and worklog checksum. Never retain the raw CSV.

On genuine acquisition or processing failure, use `record-failure`, preserve
the last good publication, and report the compact action required. Never modify
campaign state, code, settings, bindings or automations during this routine.
