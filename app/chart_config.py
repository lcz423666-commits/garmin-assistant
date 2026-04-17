from __future__ import annotations

from pathlib import Path

from app_config import load_system_config


SYSTEM_CONFIG = load_system_config()
CHART_CONFIG = SYSTEM_CONFIG.get("charts") or {}

CHART_PUBLIC_DIR = Path(CHART_CONFIG.get("public_dir", "/var/www/garmin-assistant/public/charts"))
CHART_BASE_URL = str(CHART_CONFIG.get("base_url") or "https://43.99.84.162.sslip.io").rstrip("/")
CHART_IMAGE_TTL_DAYS = {
    "morning": int(CHART_CONFIG.get("morning_ttl_days", 14)),
    "activity": int(CHART_CONFIG.get("activity_ttl_days", 30)),
    "weekly": int(CHART_CONFIG.get("weekly_ttl_days", 90)),
    "monthly": int(CHART_CONFIG.get("monthly_ttl_days", 180)),
}
