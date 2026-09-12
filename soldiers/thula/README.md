# Thula

Thula is Project Anissa's independent focus-telemetry and local-dashboard
worker. She converts complete Forest exports and typed completed-task credit
into an atomic, checksum-verified read-only publication. She cannot mutate an
agenda or access its workbook implementation.

The canonical executable is `soldiers/thula/cli.py`.

```sh
python -B soldiers/thula/cli.py sync --forest-json /path/to/extraction.json
python -B soldiers/thula/cli.py sync-drive --csv-base64 BASE64 --captured-at ISO_TIMESTAMP --source-file-id DRIVE_ID --source-modified-at ISO_TIMESTAMP
python -B soldiers/thula/cli.py snapshot
python -B soldiers/thula/cli.py ensure-server --host 127.0.0.1 --port 8765
```

Resolve settings and state through `ProjectEnvironment.worker("thula")`. Never
place credentials, raw exports, telemetry publications or private dashboard
assets in the public checkout.

## Mac login autostart

`launchd/com.anissa.thula-dashboard.plist.template` is the governed source for
the per-user LaunchAgent. Once rendered with absolute Python, project and
instance paths and installed under `~/Library/LaunchAgents`, it starts the
dashboard when the user logs in after a restart and relaunches it after an
unexpected exit. It does not run before login.

The agent runs as a background process with low-priority I/O, nice level 5,
bytecode generation disabled and a ten-second restart throttle. These settings
keep the always-on scheduling cost low without changing dashboard behavior or
telemetry accounting.
