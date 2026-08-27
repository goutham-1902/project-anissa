# Capability Adapters

This package intentionally does not hardcode a specific Codex/ChatGPT desktop version.

During setup, inspect current capabilities for:
- project/thread persistence;
- scheduled/recurring automations;
- reusing an existing conversation thread for automation output;
- browser/web search;
- local file permissions;
- Python environment/dependency installation.

Map those capabilities onto the workflow intent in `automation_specs/`. If the product behavior has changed, preserve the invariants: one shared workbook, no duplicate per-run chats, setup-before-live, workbook-first writes and explicit go-live authorization.
