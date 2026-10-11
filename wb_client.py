# -*- coding: utf-8 -*-
"""WorkBuddy 加油站签到客户端（纯标准库 urllib，无第三方依赖）。

接口事实（已在本机实测抓取，勿凭猜测修改）：
  · 令牌刷新  POST https://copilot.tencent.com/v2/plugin/auth/token/refresh
              头：X-Refresh-Token / X-Auth-Refresh-Source: plugin / X-Domain: copilot.tencent.com
              体：{}     回：{code:0, data:{accessToken, refreshToken}}   ← refreshToken 会轮换
  · 签到接口  https://www.codebuddy.cn/v2
              POST /billing/meter/checkin-activity-status  查询（只读）
              POST /billing/meter/daily-checkin            签到（写，幂等；code 10001=今日已签）
  · 派猫猫旅行 https://www.workbuddy.cn  ← 注意与签到域名不同，且路径不带 /v2 前缀
              GET  /activity/growth/buddy/travel/status
              POST /activity/growth/buddy/travel/claim       领取（写）
              POST /activity/growth/buddy/travel/depart      派出（写）body {"location_id":N}
  三处只需 Bearer Token，无需设备指纹签名。
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request

REFRESH_URL = "https://copilot.tencent.com/v2/plugin/auth/token/refresh"
BILLING_BASE = "https://www.codebuddy.cn/v2"
STATUS_PATH = "/billing/meter/checkin-activity-status"
CHECKIN_PATH = "/billing/meter/daily-checkin"

TRAVEL_BASE = "https://www.workbuddy.cn"
TRAVEL_STATUS_PATH = "/activity/growth/buddy/travel/status"
TRAVEL_CLAIM_PATH = "/activity/growth/buddy/travel/claim"
TRAVEL_DEPART_PATH = "/activity/growth/buddy/travel/depart"

STATE_TEXT = {"idle": "空闲", "traveling": "旅行中", "arrived": "已到达待领取"}

UA = "WorkBuddy-Checkin-Script/1.1"
DEFAULT_TIMEOUT = 20


class ApiError(Exception):
    """接口调用失败（网络层）。"""


def _request(url, *, method="POST", token=None, payload=None, extra_headers=None,
             timeout=DEFAULT_TIMEOUT, retries=3):
    data = None
    if method == "GET" and payload is None:
        data = None
    else:
        data = json.dumps(payload if payload is not None else {}).encode("utf-8")

    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", UA)
        if payload is not None or method != "GET":
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer %s" % token)
        for k, v in (extra_headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
                return resp.status, _parse(text)
        except urllib.error.HTTPError as e:
            text = ""
            try:
                text = e.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            return e.code, _parse(text)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(2 ** attempt + random.uniform(0, 1.2))
    raise ApiError("网络请求失败 %s: %s" % (url, last))


def _parse(text):
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {"raw": (text or "")[:500]}


# ---------------------------------------------------------------- 令牌刷新
def refresh(refresh_token: str, timeout=DEFAULT_TIMEOUT):
    """用长效令牌换新 accessToken。返回 (ok, access_token, new_refresh_token, raw)。"""
    status, raw = _request(
        REFRESH_URL,
        payload={},
        extra_headers={
            "X-Refresh-Token": refresh_token,
            "X-Auth-Refresh-Source": "plugin",
            "X-Domain": "copilot.tencent.com",
        },
        timeout=timeout,
    )
    data = raw.get("data") if isinstance(raw, dict) else None
    if raw.get("code") == 0 and isinstance(data, dict) and data.get("accessToken"):
        return True, data["accessToken"], data.get("refreshToken") or "", raw
    return False, "", "", raw


# ---------------------------------------------------------------- 签到
def query_status(token: str, timeout=DEFAULT_TIMEOUT):
    return _request(BILLING_BASE + STATUS_PATH, token=token, timeout=timeout)


def daily_checkin(token: str, timeout=DEFAULT_TIMEOUT):
    return _request(BILLING_BASE + CHECKIN_PATH, token=token, timeout=timeout)


def already_checked_in(body) -> bool:
    """兼容多种返回形态判断是否今日已签。"""
    if isinstance(body, dict):
        if body.get("today_checked_in") is True:
            return True
        if isinstance(body.get("data"), dict) and body["data"].get("today_checked_in") is True:
            return True
        if str(body.get("code")) == "10001":
            return True
    return False


BALANCE_KEYS = ("total_credits", "total_credit", "total_points", "credit_balance",
                "points_balance", "balance", "remain_credit", "total")


def extract_balance(body):
    """尽力提取积分余额，取不到返回 None。"""
    if not isinstance(body, dict):
        return None
    scope = body.get("data") if isinstance(body.get("data"), dict) else body
    for key in BALANCE_KEYS:
        v = scope.get(key)
        if isinstance(v, (int, float)):
            return v
    return None


def checkin_ok(body) -> bool:
    """签到响应是否成功（含 code 10001「今天已签到」的幂等返回）。"""
    if not isinstance(body, dict):
        return False
    code = str(body.get("code"))
    return code in ("0", "10001")


# ------------------------------------------------------------ 派猫猫旅行
def travel_status(token: str, timeout=DEFAULT_TIMEOUT):
    return _request(TRAVEL_BASE + TRAVEL_STATUS_PATH, method="GET", token=token,
                    timeout=timeout)


def travel_claim(token: str, timeout=DEFAULT_TIMEOUT):
    return _request(TRAVEL_BASE + TRAVEL_CLAIM_PATH, token=token, payload={},
                    timeout=timeout)


def travel_depart(token: str, location_id: int, timeout=DEFAULT_TIMEOUT):
    return _request(TRAVEL_BASE + TRAVEL_DEPART_PATH, token=token,
                    payload={"location_id": location_id}, timeout=timeout)
