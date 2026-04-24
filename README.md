# Garmin Assistant

Garmin Assistant is a Python service for personal health and training analysis. It pulls Garmin and Intervals.icu data, builds structured payloads, uses an OpenAI-compatible LLM for coaching text, sends PushPlus notifications, and exposes a small FastAPI chat interface.

## Repository Policy

This repository is intended to be safe for a private GitHub repo. It contains source code, knowledge files, deployment references, and configuration templates only.

Do not commit:

- real `config/system.json` or `config/users.json`
- `.env` files
- Garmin token caches
- `data/`, `state/`, `logs/`, `reports/`, `review_samples/`, or generated charts
- `_server_backup/`, which contains the local raw production backup

## Layout

- `app/` - monitor, Garmin/ICU analysis, chart rendering, payload builders, and shared helpers
- `chat_api/` - FastAPI chat and GPX analysis API
- `knowledge/` - coaching knowledge injected into prompts
- `scripts/` - onboarding, backfill, snapshots, cleanup, and test utilities
- `public/chat/` - static chat frontend copied from production
- `deploy/` - reference cron, systemd, and nginx files from production
- `config/*.example.json` and `.env.example` - templates for local or server configuration

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/system.example.json config/system.json
cp config/users.example.json config/users.json
cp .env.example .env
```

Most production scripts expect `/root/garmin_assistant`. For local development, set:

```bash
export GARMIN_ASSISTANT_ROOT="$PWD"
```

Some legacy entrypoints still contain production path assumptions. Treat local runs as development smoke tests until those paths are refactored.

## Common Commands

```bash
python -m py_compile app/*.py chat_api/*.py scripts/*.py *.py
uvicorn chat_api.main:app --host 127.0.0.1 --port 8100
python app/icu_turning_points_validator.py
```

## Production Notes

Production runtime data was backed up locally under `_server_backup/YYYYMMDD/` and is intentionally ignored by Git. Use `docs/deploy.md` when preparing a new server.
