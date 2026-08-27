# Project Anissa

Project Anissa is a local, chat-first personal research-operations framework.
It separates a reusable release from a Mac-local private instance containing the
canonical workbook, profile, presentation overlay, bindings and worker state.

## Architecture

- **Anissa Core** coordinates stable command, weekday and weekend interfaces.
- **Portfolio** registers independent agendas without owning their task state.
- **Graduate Applications** is the first agenda and owns its workbook vocabulary.
- **Workers** consume typed read-only projections and cannot mutate agenda state.
- **Project Environment** resolves every private path outside the Git checkout.
- **Maintainer governance** evaluates scope before execution and records verified
  changes in private append-only ledgers.

## Install and verify

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tools/verify.py
```

The verifier creates a temporary synthetic SETUP instance. It does not activate
automations, submit applications or create chats.

## Create a local instance

```bash
.venv/bin/python tools/init_instance.py "$HOME/Library/Application Support/Project Anissa/instances/default"
export PROJECT_ANISSA_INSTANCE="$HOME/Library/Application Support/Project Anissa/instances/default"
.venv/bin/python tools/preflight.py
```

Keep the instance directory out of version control. Replace synthetic profile
facts only through your own onboarding workflow, and require explicit go-live
authorization before binding scheduled work.
