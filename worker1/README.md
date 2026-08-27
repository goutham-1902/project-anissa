# A2A worker

A2A is a narrow local telemetry worker. It imports completed focus-history
exports, performs deterministic time accounting, combines typed completed-task
credit and publishes an atomic checksum-verified work log for the dashboard.

It cannot import the workbook gateway, mutate agenda state, create tasks or
message role chats. A failed or stale acquisition preserves the last good log
and never records a false zero.
