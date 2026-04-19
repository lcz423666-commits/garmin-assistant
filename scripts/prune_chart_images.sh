#!/bin/sh
set -eu

BASE_DIR="${BASE_DIR:-/var/www/garmin-assistant/public/charts}"

prune_message_type() {
  message_type="$1"
  ttl_days="$2"
  if [ ! -d "$BASE_DIR" ]; then
    return 0
  fi
  if [ -d "$BASE_DIR/$message_type" ]; then
    find "$BASE_DIR/$message_type" -type f -mtime +"$ttl_days" -delete
  fi
  find "$BASE_DIR" -mindepth 4 -type f -path "$BASE_DIR/*/$message_type/*/*" -mtime +"$ttl_days" -delete
}

prune_message_type "morning" 14
prune_message_type "activity" 30
prune_message_type "weekly" 90
prune_message_type "monthly" 180

if [ -d "$BASE_DIR" ]; then
  find "$BASE_DIR" -depth -type d -empty -delete
fi
