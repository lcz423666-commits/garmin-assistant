#!/usr/bin/env python3
import json
import sys
import tempfile
from pathlib import Path

APP_DIR = Path("/root/garmin_assistant/app")
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from garmin_cookie_client import GarminCookieClient

sample = {
    "display_name": "demo-guid",
    "full_name": "Demo User",
    "csrf_token": "csrf-demo",
    "cookies": {
        "session": "sess-demo",
        "JWT_WEB": "jwt-demo",
        "SESSIONID": "sid-demo",
    },
}

with tempfile.TemporaryDirectory() as tmpdir:
    cookie_file = Path(tmpdir) / "cookie_auth.json"
    cookie_file.write_text(json.dumps(sample), encoding="utf-8")
    client = GarminCookieClient(str(cookie_file))

    assert client.BASE_URL == "https://connect.garmin.cn/gc-api", client.BASE_URL
    assert client._headers.get("Connect-Csrf-Token") == "csrf-demo", client._headers
    assert client._sess.cookies.get("session", domain="connect.garmin.cn", path="/") == "sess-demo"
    assert client._sess.cookies.get("JWT_WEB", domain="connect.garmin.cn", path="/") == "jwt-demo"
    assert client._sess.cookies.get("SESSIONID", domain="connect.garmin.cn", path="/") == "sid-demo"

print("ok")
