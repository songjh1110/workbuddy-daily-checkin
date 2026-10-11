# -*- coding: utf-8 -*-
"""WorkBuddy 加油站积分 —— 每日自动签到主程序（云端 / 本机通用，纯标准库 + requests 推送）。

环境变量：
  WB_REFRESH_TOKEN  长效令牌（refresh token），必需。放 GitHub Secrets，绝不入库。
  SERVERCHAN_KEY / PUSHPLUS_TOKEN / WECOM_WEBHOOK   推送渠道（可选，见 notifier.py）
  PUSH_ON_SUCCESS   成功是否推送，默认 1
  WB_TRAVEL_AUTO    是否跑「派猫猫旅行」闭环（先领后派），默认 0
  WB_LOCATION       指定派遣地点 1-4，默认随机
  GH_PAT + GITHUB_REPOSITORY + WB_SECRET_NAME
                    轮换令牌后自动回写 GitHub Secret（可选，需 PyNaCl）

退出码：0 = 成功（含「今日已签到」），1 = 失败
"""
from __future__ import annotations

import base64
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import wb_client
from notifier import notify

CST = timezone(timedelta(hours=8))
now = lambda: datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731


def log(msg: str) -> None:
    print("[%s] %s" % (now(), msg), flush=True)


def mask(t: str) -> str:
    return (t[:6] + "..." + t[-4:]) if t and len(t) > 12 else "<empty>"


# ------------------------------------------------------------------ 令牌轮换回写
def update_github_secret(value: str) -> str:
    """把轮换后的新令牌写回仓库 Secret（尽力而为，失败不影响主流程）。"""
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    pat = os.environ.get("GH_PAT", "").strip()
    name = os.environ.get("WB_SECRET_NAME", "WB_REFRESH_TOKEN").strip()
    if not repo or not pat:
        return "未配置 GH_PAT，跳过轮换回写（令牌可能失效，需手动更新 Secret）"
    try:
        from nacl import encoding, public  # type: ignore
    except Exception:  # noqa: BLE001
        return "缺少 PyNaCl，跳过轮换回写"

    def call(method, path, body=None):
        req = urllib.request.Request("https://api.github.com" + path, method=method,
                                    data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Authorization", "Bearer " + pat)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")[:200]

    st, pub = call("GET", "/repos/%s/actions/secrets/public-key" % repo)
    if st != 200 or not isinstance(pub, dict):
        return "取公钥失败 HTTP %s，跳过轮换回写" % st
    try:
        box = public.SealedBox(public.PublicKey(base64.b64decode(pub["key"])))
        enc = base64.b64encode(box.encrypt(value.encode())).decode()
    except Exception as e:  # noqa: BLE001
        return "加密失败（%s），跳过轮换回写" % e
    st, _ = call("PUT", "/repos/%s/actions/secrets/%s" % (repo, name),
                 {"encrypted_value": enc, "key_id": pub["key_id"]})
    return ("轮换令牌已写回 Secret %s" % name) if st in (204, 200) else "回写失败 HTTP %s" % st


# ------------------------------------------------------------------ 主流程
def do_travel(token: str) -> str:
    """派猫猫旅行闭环：到达先领，空闲再派。返回人类可读日志。"""
    lines = []
    try:
        st, body = wb_client.travel_status(token)
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or str(body.get("code")) not in ("0", "None"):
            return "旅行状态不可用（HTTP %s），跳过" % st
        state = data.get("state")
        lines.append("旅行状态：%s" % wb_client.STATE_TEXT.get(state, state))

        if state == "arrived":
            reward = data.get("reward_credit")
            _, cbody = wb_client.travel_claim(token)
            if wb_client.checkin_ok(cbody):
                lines.append("已领取旅行积分 +%s" % (reward if reward is not None else "?"))
            else:
                lines.append("领取失败：%s" % str(cbody)[:120])
            _, body = wb_client.travel_status(token)
            data = body.get("data") if isinstance(body, dict) else {}
            state = data.get("state")

        if state == "idle":
            if data.get("daily_limit_reached"):
                lines.append("今日派遣已达上限，跳过")
            else:
                loc = os.environ.get("WB_LOCATION", "").strip()
                loc_id = int(loc) if loc.isdigit() and 1 <= int(loc) <= 4 else random.randint(1, 4)
                _, dbody = wb_client.travel_depart(token, loc_id)
                if wb_client.checkin_ok(dbody):
                    d2 = dbody.get("data") or {}
                    name = (d2.get("location") or {}).get("name") or ("地点%s" % loc_id)
                    arrive = d2.get("arrive_at")
                    base_ts = d2.get("server_now") or time.time()
                    hours = None
                    if isinstance(arrive, (int, float)):
                        hours = max(0.0, round((float(arrive) - float(base_ts)) / 3600, 1))
                    lines.append("已派 Buddy 前往%s，预计%s抵达，下次运行自动领取" % (
                        name, ("%s 小时后" % hours) if hours is not None else "稍后"))
                else:
                    lines.append("派遣失败：%s" % str(dbody)[:120])
        elif state == "traveling":
            lines.append("旅行进行中，等待抵达后自动领取")
    except Exception as e:  # noqa: BLE001
        lines.append("旅行环节异常：%s" % e)
    return "；".join(lines)


def run() -> tuple[bool, list[str], int | None]:
    rt = os.environ.get("WB_REFRESH_TOKEN", "").strip()
    if not rt:
        return False, ["缺少环境变量 WB_REFRESH_TOKEN（长效令牌）"], None

    log("使用长效令牌 %s 换取接口令牌" % mask(rt))
    ok, at, new_rt, raw = wb_client.refresh(rt)
    if not ok:
        return False, ["令牌刷新失败：%s" % str(raw)[:300]], None

    msgs = []
    # 轮换后旧令牌实测仍有效，因此回写 Secret 属可选增强：配了 GH_PAT 才做
    if new_rt and new_rt != rt and os.environ.get("GH_PAT", "").strip() \
            and os.environ.get("GITHUB_REPOSITORY", "").strip():
        msgs.append("令牌已轮换，" + update_github_secret(new_rt))

    log("查询签到状态")
    st, sbody = wb_client.query_status(at)
    if st in (401, 403):
        return False, ["接口令牌被拒 HTTP %s（令牌可能已失效，请重新导出 Secret）" % st], None
    if st >= 500:
        return False, ["状态查询服务端异常 HTTP %s" % st], None

    balance = wb_client.extract_balance(sbody)
    bal_txt = "" if balance is None else "，当前积分余额 %s" % balance

    if wb_client.already_checked_in(sbody):
        log("今日已签到")
        lines = ["今日已签到，无需重复领取" + bal_txt]
    else:
        cst, cbody = wb_client.daily_checkin(at)
        if wb_client.checkin_ok(cbody):
            got = wb_client.extract_balance(cbody)
            log("签到成功")
            lines = ["签到成功%s" % ("" if got is None else "，当前积分余额 %s" % (got or balance))]
            if str(cbody.get("code")) == "10001":
                lines = ["服务端返回「今天已签到」，未重复发放" + bal_txt]
        else:
            return False, ["签到失败：HTTP %s %s" % (cst, str(cbody)[:300])], None

    if os.environ.get("WB_TRAVEL_AUTO", "0").strip().lower() in ("1", "true", "yes", "on"):
        msgs.append(do_travel(at))

    return True, lines + msgs, balance


def main() -> int:
    log("=" * 10 + " WorkBuddy 加油站签到开始 " + "=" * 10)
    try:
        ok, lines, balance = run()
    except Exception as e:  # noqa: BLE001
        ok, lines, balance = False, ["未捕获异常：%s: %s" % (type(e).__name__, e)], None
        log(str(lines[0]))

    detail = "\n".join("- " + x for x in lines)
    title = "✅ WorkBuddy 加油站已签到" if ok else "❌ WorkBuddy 加油站签到失败"
    body = (f"账号：哄哄（UIN 330100409068）\n"
            f"时间：{now()}（北京时间）\n"
            f"积分余额：{balance if balance is not None else '未知'}\n\n{detail}")

    if ok and os.environ.get("PUSH_ON_SUCCESS", "1").strip().lower() in ("1", "true", "yes", "on"):
        log("推送 → " + notify(title, body))
    elif not ok:
        log("推送 → " + notify(title, body))

    print()
    print(title)
    print(detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
