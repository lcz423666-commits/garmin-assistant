#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/var/www/garmin-assistant/public/charts"

find "$BASE_DIR/morning" -type f -mtime +14 -delete 2>/dev/null || true
find "$BASE_DIR/activity" -type f -mtime +30 -delete 2>/dev/null || true
find "$BASE_DIR/weekly" -type f -mtime +90 -delete 2>/dev/null || true
find "$BASE_DIR/monthly" -type f -mtime +180 -delete 2>/dev/null || true
