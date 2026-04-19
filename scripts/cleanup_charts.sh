#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/var/www/garmin-assistant/public/charts}"

prune_message_type() {
  local message_type="$1"
  local ttl_days="$2"
  [[ -d "$BASE_DIR" ]] || return 0
  if [[ -d "$BASE_DIR/$message_type" ]]; then
    find "$BASE_DIR/$message_type" -type f -mtime +"$ttl_days" -delete
  fi
  find "$BASE_DIR" -mindepth 4 -type f -path "$BASE_DIR/*/$message_type/*/*" -mtime +"$ttl_days" -delete
}

prune_message_type "morning" 14
prune_message_type "activity" 30
prune_message_type "weekly" 90
prune_message_type "monthly" 180

find "$BASE_DIR" -depth -type d -empty -delete 2>/dev/null || true
