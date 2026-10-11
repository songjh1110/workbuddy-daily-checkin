# -*- coding: utf-8 -*-
"""一键把本项目建为 GitHub 私有仓库并配好 Secrets / 触发首次运行。

本机无需安装 git：全程走 GitHub REST API。
凭据获取顺序：--pat-file / 环境变量 GH_PAT / 交互式输入（不回显）。
长效令牌会自动从本机 WorkBuddy 登录态导出，无需手工复制。

用法（在你自己的终端里跑，别让 PAT 出现在聊天记录里）：
    pip install pynacl
    python bootstrap_github.py                       # 仓库名默认 workbuddy-daily-checkin
    python bootstrap_github.py --repo my-private-name
    python bootstrap_github.py --pat-file C:\\path\\gh_pat.txt
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
UA = "workbuddy-checkin-bootstrap"
TIMEOUT = 30

# 需要推送到仓库的文件（相对本脚本目录）
FILES = [
    "main.py",
    "wb_client.py",
    "notifier.py",
    "requirements.txt",
    ".gitignore",
    "README.md",
    ".github/workflows/daily-claim.yml",
    "bootstrap_github.py",
]

SKILL_SCRIPT = os.path.expanduser(
    "~/.workbuddy/skills/totorosir-workbuddy-score/scripts/buddy_station.py")


# ------------------------------------------------------------------ HTTP 工具
def api(method, path, token, body=None, raw_ok=()):
    last = None
    for attempt in range(1, 6):
        req = urllib.request.Request(API + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Authorization", "Bearer " + token)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", UA)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                txt = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(txt) if txt else {})
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(txt)
            except Exception:  # noqa: BLE001
                return e.code, {"raw": txt[:300]}
        except Exception as e:  # noqa: BLE001  网络抖动：退避重试
            last = e
            if attempt < 5:
                time.sleep(2 * attempt)
    raise RuntimeError("GitHub API 网络失败（已重试 5 次）：%s" % last)


def mask(t):
    return (t[:6] + "..." + t[-4:]) if t and len(t) > 12 else "<empty>"


# ------------------------------------------------------------------ 凭据获取
def get_pat(args):
    if args.pat_file:
        with open(args.pat_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    env = os.environ.get("GH_PAT", "").strip()
    if env:
        return env
    print("请粘贴 GitHub Personal Access Token（输入不回显，回车确认）：")
    print("  需要权限：repo（含 workflow）")
    try:
        return getpass.getpass("  PAT> ").strip()
    except Exception:  # noqa: BLE001
        return ""


def export_refresh_token():
    """从本机 WorkBuddy 登录态导出长效令牌（不落地、不打印明文）。"""
    if not os.path.isfile(SKILL_SCRIPT):
        return None, "本机未找到 WorkBuddy 积分助手脚本，跳过自动导出"
    try:
        out = subprocess.run([sys.executable, SKILL_SCRIPT, "--export-rt"],
                             capture_output=True, timeout=120)
    except Exception as e:  # noqa: BLE001
        return None, "导出失败：%s" % e
    if out.returncode != 0:
        return None, "导出失败：%s" % (out.stderr.decode("utf-8", "replace")[:200] or "未知错误")
    lines = [l.strip() for l in out.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
    token = max(lines, key=len) if lines else ""
    if len(token) < 200 or token.count(".") != 2:
        return None, "导出内容不像有效令牌"
    return token, None


# ------------------------------------------------------------------ Secret 加密写入
def put_secret(token, repo_full, name, value):
    st, pub = api("GET", "/repos/%s/actions/secrets/public-key" % repo_full, token)
    if st != 200 or not isinstance(pub, dict) or not pub.get("key"):
        return False, "取仓库公钥失败 HTTP %s" % st
    try:
        from nacl import public  # type: ignore
    except Exception:  # noqa: BLE001
        return False, "缺少 PyNaCl（pip install pynacl）"
    try:
        box = public.SealedBox(public.PublicKey(base64.b64decode(pub["key"])))
        enc = base64.b64encode(box.encrypt(value.encode())).decode()
    except Exception as e:  # noqa: BLE001
        return False, "加密失败：%s" % e
    st, _ = api("PUT", "/repos/%s/actions/secrets/%s" % (repo_full, name),
                token, {"encrypted_value": enc, "key_id": pub["key_id"]})
    return (st in (200, 201, 204)), ("成功" if st in (200, 201, 204) else "写入失败 HTTP %s" % st)


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(description="建 GitHub 私有仓库并部署 WorkBuddy 每日签到")
    ap.add_argument("--repo", default="workbuddy-daily-checkin", help="仓库名（私有）")
    ap.add_argument("--pat-file", help="存放 PAT 的文件路径（也可设环境变量 GH_PAT）")
    ap.add_argument("--no-dispatch", action="store_true", help="建好后不立即触发工作流")
    ap.add_argument("--skip-secrets", action="store_true",
                    help="跳过 API 写 Secret（令牌没有 Secrets 权限时用，改在网页手动加）")
    args = ap.parse_args()

    token = get_pat(args)
    if not token:
        print("× 未取得 GitHub PAT，退出。")
        return 1

    st, me = api("GET", "/user", token)
    if st != 200:
        print("× PAT 无效或过期 HTTP %s：%s" % (st, str(me)[:200]))
        return 1
    owner = me.get("login")
    print("√ GitHub 身份：%s" % owner)

    # 1) 建仓（已存在则复用——便于用「细粒度令牌」先手动建仓再跑本脚本）
    repo_full = "%s/%s" % (owner, args.repo)
    st_get, _ = api("GET", "/repos/%s" % repo_full, token)
    if st_get == 200:
        print("√ 复用已存在仓库：https://github.com/%s" % repo_full)
    else:
        st, r = api("POST", "/user/repos", token,
                    {"name": args.repo, "private": True, "auto_init": False,
                     "description": "WorkBuddy 加油站积分每日自动签到"})
        if st == 201:
            print("√ 已创建私有仓库：https://github.com/%s" % repo_full)
        else:
            print("× 建仓失败 HTTP %s：%s" % (st, str(r)[:300]))
            if st == 403:
                print("  提示：细粒度令牌无法新建仓库。请先在 GitHub 网页端手动建一个私有空仓库，")
                print("       再重跑本脚本（会检测并复用同名仓库）。")
            return 1

    # 2) 逐个文件提交
    base = os.path.dirname(os.path.abspath(__file__))
    for rel in FILES:
        p = os.path.join(base, rel)
        if not os.path.isfile(p):
            print("  - 跳过（不存在）：%s" % rel)
            continue
        content = base64.b64encode(open(p, "rb").read()).decode()
        path_rel = rel.replace("\\", "/")
        st2, existing = api("GET", "/repos/%s/contents/%s" % (repo_full, path_rel), token)
        body = {"message": "chore: WorkBuddy 每日签到自动化", "content": content, "branch": "main"}
        if st2 == 200 and isinstance(existing, dict):
            body["sha"] = existing.get("sha")
        st2, res = api("PUT", "/repos/%s/contents/%s" % (repo_full, path_rel), token, body)
        print("  %s %s" % ("√" if st2 in (200, 201) else "×", path_rel))
        if st2 not in (200, 201):
            print("     HTTP %s：%s" % (st2, str(res)[:200]))

    # 3) 写 Secrets
    print("\n== 配置仓库 Secrets ==")
    if args.skip_secrets:
        print("  --skip-secrets：跳过自动写入。请在浏览器手动添加：")
        print("  %s/settings/secrets/actions → New repository secret" % repo_full)
        print("  Name 填 WB_REFRESH_TOKEN，Value 用下面命令导出（在另一个窗口运行，复制输出）：")
        print('    python "%s"' % SKILL_SCRIPT)
        print("  （输出是一整段 eyJ 开头的长串，等同账号钥匙，只贴进 GitHub Secret）")
        if any(os.environ.get(n, "").strip() for n in ("SERVERCHAN_KEY", "PUSHPLUS_TOKEN", "WECOM_WEBHOOK")):
            print("  推送渠道同理：SERVERCHAN_KEY / PUSHPLUS_TOKEN / WECOM_WEBHOOK 分别建一个 Secret。")
    else:
        rt, err = export_refresh_token()
        if rt:
            ok, msg = put_secret(token, repo_full, "WB_REFRESH_TOKEN", rt)
            print("  %s WB_REFRESH_TOKEN（%s）：%s" % ("√" if ok else "×", mask(rt), msg))
            if not ok:
                print("    也可改在网页手动添加：%s/settings/secrets/actions" % repo_full)
        else:
            print("  × WB_REFRESH_TOKEN 未写入：%s" % err)
            print("    请手动添加 Settings → Secrets → Actions → New repository secret")

        for name in ("SERVERCHAN_KEY", "PUSHPLUS_TOKEN", "WECOM_WEBHOOK"):
            v = os.environ.get(name, "").strip()
            if v:
                ok, msg = put_secret(token, repo_full, name, v)
                print("  %s %s：%s" % ("√" if ok else "×", name, msg))
        if not any(os.environ.get(n, "").strip() for n in ("SERVERCHAN_KEY", "PUSHPLUS_TOKEN", "WECOM_WEBHOOK")):
            print("  - 未检测到推送渠道环境变量，跳过（可之后在网页端补 SERVERCHAN_KEY / PUSHPLUS_TOKEN）")

    # 4) 触发首次运行
    if args.no_dispatch:
        return 0
    if args.skip_secrets:
        print("\n- 因 Secret 需手动添加，暂不自动触发。加好 WB_REFRESH_TOKEN 后到 Actions 页手动 Run 一次。")
        return 0
    time.sleep(2)
    st, _ = api("POST", "/repos/%s/actions/workflows/daily-claim.yml/dispatches" % repo_full,
                token, {"ref": "main"})
    print("\n%s 已触发首次运行" % ("√" if st == 204 else "×"))

    print("\n完成。稍后请到 https://github.com/%s/actions 确认运行结果。" % repo_full)
    print("手机装了 Server 酱 / PushPlus 的话，会直接收到一条签到推送。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
