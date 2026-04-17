#!/bin/sh
set -eu

BASE_DIR="/var/www/garmin-assistant/public/charts"

prune() {
  target="$1"
  ttl_days="$2"
  if [ -d "$target" ]; then
    find "$target" -type f -mtime +"$ttl_days" -delete
  fi
}

prune "$BASE_DIR/morning" 14
prune "$BASE_DIR/activity" 30
prune "$BASE_DIR/weekly" 90
prune "$BASE_DIR/monthly" 180

if [ -d "$BASE_DIR" ]; then
  find "$BASE_DIR" -depth -type d -empty -delete
fi
