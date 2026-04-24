# Deployment Guide

This document describes how to recreate the production deployment from the cleaned repository plus private runtime secrets.

## 1. Server Layout

The current code assumes this production path:

```bash
/root/garmin_assistant
```

Recommended runtime-only paths:

```bash
/root/garmin_assistant/config/system.json
/root/garmin_assistant/config/users.json
/root/garmin_assistant/data
/root/garmin_assistant/state
/root/garmin_assistant/logs
/root/garmin_assistant/tokens
/root/.env
/var/www/garmin-assistant/public
```

## 2. Install

```bash
cd /root
git clone git@github.com:lcz423666-commits/garmin-assistant.git garmin_assistant
cd /root/garmin_assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/system.example.json config/system.json
cp config/users.example.json config/users.json
cp .env.example /root/.env
```

Fill the copied config files with real credentials on the server only.

## 3. Static Frontend

```bash
mkdir -p /var/www/garmin-assistant/public/chat
rsync -a public/chat/ /var/www/garmin-assistant/public/chat/
```

Generated chart files should be written under `/var/www/garmin-assistant/public/charts/`.

## 4. Services

Reference files from the last production server are stored in:

```bash
deploy/systemd/garmin-chat.service
deploy/nginx/garmin-charts.conf
deploy/crontab.txt
```

Review paths and Python interpreter locations before applying them on a new server.

```bash
cp deploy/systemd/garmin-chat.service /etc/systemd/system/garmin-chat.service
systemctl daemon-reload
systemctl enable garmin-chat
systemctl restart garmin-chat
```

For nginx, copy the reviewed config into `/etc/nginx/sites-available/`, symlink it into `sites-enabled`, then run:

```bash
nginx -t
systemctl reload nginx
```

Install the cron file only after `config/users.json`, `config/system.json`, `/root/.env`, and Garmin token caches are ready:

```bash
crontab deploy/crontab.txt
```

## 5. Runtime Data Restore

Runtime data is intentionally not in Git. If restoring from the local raw backup, copy only the required private directories:

```bash
rsync -a _server_backup/YYYYMMDD/root/garmin_assistant/data/ /root/garmin_assistant/data/
rsync -a _server_backup/YYYYMMDD/root/garmin_assistant/state/ /root/garmin_assistant/state/
rsync -a _server_backup/YYYYMMDD/root/garmin_assistant/tokens/ /root/garmin_assistant/tokens/
rsync -a _server_backup/YYYYMMDD/root/.env /root/.env
```

Do not copy `_server_backup` into the repository or server web root.

## 6. Smoke Checks

```bash
cd /root/garmin_assistant
source .venv/bin/activate
python -m py_compile app/*.py chat_api/*.py scripts/*.py *.py
python app/icu_turning_points_validator.py
curl -fsS http://127.0.0.1:8100/api/health
```
