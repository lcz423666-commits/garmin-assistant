"""
Garmin Connect cookie-based client.
用浏览器 session cookie 直接调用 connect.garmin.cn 网页会话对应的 /gc-api，
作为 garth OAuth token 失效时的备用方案。
"""
from __future__ import annotations

import json
import os

import requests


class _DummyGarth:
    """占位符，让 login_garmin 里的 client.garth.dump() 不报错。"""
    def dump(self, path):
        pass


class GarminCookieClient:
    """
    用浏览器 session cookie 模拟 garminconnect.Garmin 接口。
    只实现 garmin_monitor.py 实际用到的方法。
    """
    BASE_URL = "https://connect.garmin.cn/gc-api"

    garmin_connect_daily_summary_url = "/usersummary-service/usersummary/daily"
    garmin_connect_daily_sleep_url = "/wellness-service/wellness/dailySleepData"
    garmin_connect_daily_stress_url = "/wellness-service/wellness/dailyStress"
    garmin_connect_daily_body_battery_url = "/wellness-service/wellness/bodyBattery/reports/daily"
    garmin_connect_body_battery_events_url = "/wellness-service/wellness/bodyBattery/events"
    garmin_connect_hrv_url = "/hrv-service/hrv"
    garmin_connect_training_status_url = "/metrics-service/metrics/trainingstatus/aggregated"
    garmin_connect_user_summary_chart = "/wellness-service/wellness/dailySummaryChart"
    garmin_connect_heartrates_daily_url = "/wellness-service/wellness/dailyHeartRate"
    garmin_connect_daily_spo2_url = "/wellness-service/wellness/daily/spo2"
    garmin_connect_daily_intensity_minutes = "/wellness-service/wellness/daily/im"
    garmin_connect_rhr_url = "/userstats-service/wellness/daily"

    def __init__(self, cookie_file: str):
        with open(cookie_file, encoding="utf-8") as f:
            data = json.load(f)

        self.display_name = data["display_name"]
        self.full_name = data["full_name"]
        self.garth = _DummyGarth()

        self._sess = requests.Session()
        self._sess.trust_env = False
        for name, value in data["cookies"].items():
            self._sess.cookies.set(name, value, domain="connect.garmin.cn")
        self._headers = {
            "accept": "*/*",
            "Connect-Csrf-Token": data.get("csrf_token", ""),
            "NK": "NT",
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
        }

    def _get(self, path: str, params: dict | None = None):
        url = f"{self.BASE_URL}{path}"
        resp = self._sess.get(url, headers=self._headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def connectapi(self, path: str, params: dict | None = None):
        return self._get(path, params=params)

    # ── 登录接口（验证 cookie 是否还有效） ──────────────────────────────
    def login(self, tokenstore=None):
        self._get("/userprofile-service/socialProfile")

    # ── 用户信息 ────────────────────────────────────────────────────────
    def get_full_name(self) -> str:
        return self.full_name

    def get_profile(self) -> dict:
        return self._get("/userprofile-service/socialProfile")

    def get_user_profile(self) -> dict:
        return self._get("/userprofile-service/socialProfile")

    # ── 健康数据 ────────────────────────────────────────────────────────
    def get_stats(self, cdate: str) -> dict:
        return self.get_user_summary(cdate)

    def get_user_summary(self, cdate: str) -> dict:
        return self.connectapi(
            f"{self.garmin_connect_daily_summary_url}/{self.display_name}",
            params={"calendarDate": cdate},
        )

    def get_sleep_data(self, cdate: str) -> dict:
        return self.connectapi(
            f"{self.garmin_connect_daily_sleep_url}/{self.display_name}",
            params={"date": cdate, "nonSleepBufferMinutes": 60},
        )

    def get_hrv_data(self, cdate: str) -> dict:
        return self.connectapi(f"{self.garmin_connect_hrv_url}/{cdate}")

    def get_body_battery(self, startdate: str, enddate: str | None = None) -> list:
        if enddate is None:
            enddate = startdate
        return self.connectapi(
            self.garmin_connect_daily_body_battery_url,
            params={"startDate": startdate, "endDate": enddate},
        )

    def get_body_battery_events(self, cdate: str) -> list:
        return self.connectapi(f"{self.garmin_connect_body_battery_events_url}/{cdate}")

    def get_steps_data(self, cdate: str):
        response = self.connectapi(
            f"{self.garmin_connect_user_summary_chart}/{self.display_name}",
            params={"date": cdate},
        )
        return response or []

    def get_intensity_minutes_data(self, cdate: str) -> dict:
        return self.connectapi(f"{self.garmin_connect_daily_intensity_minutes}/{cdate}")

    def get_training_status(self, cdate: str) -> dict:
        return self.connectapi(f"{self.garmin_connect_training_status_url}/{cdate}")

    def get_stress_data(self, cdate: str) -> dict:
        return self.connectapi(f"{self.garmin_connect_daily_stress_url}/{cdate}")

    def get_all_day_stress(self, cdate: str) -> dict:
        return self.get_stress_data(cdate)

    def get_heart_rates(self, cdate: str) -> dict:
        return self.connectapi(
            f"{self.garmin_connect_heartrates_daily_url}/{self.display_name}",
            params={"date": cdate},
        )

    def get_spo2_data(self, cdate: str) -> dict:
        return self.connectapi(f"{self.garmin_connect_daily_spo2_url}/{cdate}")

    def get_rhr_day(self, cdate: str) -> dict:
        return self.connectapi(
            f"{self.garmin_connect_rhr_url}/{self.display_name}",
            params={"fromDate": cdate, "untilDate": cdate, "metricId": 60},
        )

    # ── 活动 ────────────────────────────────────────────────────────────
    def get_activities(self, start: int = 0, limit: int = 20, activitytype: str | None = None) -> list:
        params = {"start": start, "limit": limit}
        if activitytype:
            params["activityType"] = activitytype
        result = self._get(
            "/activitylist-service/activities/search/activities", params=params
        )
        return result if isinstance(result, list) else []

    def get_activity(self, activity_id) -> dict:
        return self._get(f"/activity-service/activity/{activity_id}")

    def get_activity_splits(self, activity_id) -> dict:
        return self._get(f"/activity-service/activity/{activity_id}/splits")

    # ── 徽章 ────────────────────────────────────────────────────────────
    def get_earned_badges(self) -> list:
        return self._get("/badge-service/badge/earned")
