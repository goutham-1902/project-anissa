# Lucan discovery routine

This is an activation-ready execution sequence, not an active schedule.

1. Accept one current, bounded discovery brief authored by Project Anissa. Never
   read the workbook, profile source documents or an agenda implementation.
2. Run `soldiers/lucan/cli.py prepare --brief BRIEF.json` and use exactly the
   returned model, reasoning effort and prompt. Search the web when the prompt
   requires it; stop at the returned `max_web_tool_calls` limit even when a
   source fails, and do not enter ChatGPT Work mode.
3. Save only the returned plain JSON result for validation, then run
   `soldiers/lucan/cli.py finalize` with the brief, result and timezone-aware run
   timestamps.
4. Stay quiet when the adapter returns `QUIET`. Report a compact candidate delta
   or failure only when it returns `REPORT`.
5. If browsing, parsing or validation fails, run `record-failure`. Never replace
   missing results with an empty successful result.

In `SETUP`/`SHADOW`, validation returns the typed payload to General and writes
nothing to the publication slot. `LIVE` publication is possible only after the
private settings coherently say `LIVE`, `ACTIVE` and `LIVE`; binding that state
and any recurring automation requires a later explicit approval.
