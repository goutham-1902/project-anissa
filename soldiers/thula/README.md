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
