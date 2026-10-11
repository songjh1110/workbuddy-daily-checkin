# -*- coding: utf-8 -*-
"""结果推送：Server 酱(Turbo)、PushPlus、企业微信机器人，按需启用。

环境变量：
  SERVERCHAN_KEY   Server 酱 Turbo SendKey（sctapi.ftqq.com）
  PUSHPLUS_TOKEN   PushPlus token
  WECOM_WEBHOOK    企业微信群机器人 webhook（可选）
未配置则不推送，不会出现报错。
"""
from __future__ import annotations

import json
import os

import requests

TIMEOUT = 15


def enabled_channels() -> list[str]:
    r = []
    if os.environ.get('SERVERCHAN_KEY', '').strip():
        r.append('Server酱')
    if os.environ.get('PUSHPLUS_TOKEN', '').strip():
        r.append('PushPlus')
    if os.environ.get('WECOM_WEBHOOK', '').strip():
        r.append('企业微信机器人')
    return r


def notify(title: str, content: str) -> str:
    """推送到所有已配置的渠道；任何异常都不向上抛出。"""
    chans = enabled_channels()
    if not chans:
        return '未配置推送渠道，跳过'

    results = []
    key = os.environ.get('SERVERCHAN_KEY', '').strip()
    if key:
        try:
            r = requests.post(
                f'https://sctapi.ftqq.com/{key}.send',
                data={'title': title[:32], 'desp': content},
                timeout=TIMEOUT,
            )
            j = r.json()
            ok = j.get('code') == 0 or j.get('data', {}).get('error') == 'SUCCESS'
            results.append('Server酱:' + ('成功' if ok else f'失败 {str(j)[:120]}'))
        except Exception as e:  # noqa: BLE001
            results.append(f'Server酱:异常 {e}')

    token = os.environ.get('PUSHPLUS_TOKEN', '').strip()
    if token:
        try:
            r = requests.post(
                'https://www.pushplus.plus/send',
                json={'token': token, 'title': title, 'content': content, 'template': 'txt'},
                timeout=TIMEOUT,
            )
            j = r.json()
            results.append('PushPlus:' + ('成功' if j.get('code') == 200 else f'失败 {str(j)[:120]}'))
        except Exception as e:  # noqa: BLE001
            results.append(f'PushPlus:异常 {e}')

    hook = os.environ.get('WECOM_WEBHOOK', '').strip()
    if hook:
        try:
            r = requests.post(
                hook,
                json={'msgtype': 'text', 'text': {'content': f'{title}\n{content}'}},
                timeout=TIMEOUT,
            )
            j = r.json()
            results.append('企业微信:' + ('成功' if j.get('errcode') == 0 else f'失败 {str(j)[:120]}'))
        except Exception as e:  # noqa: BLE001
            results.append(f'企业微信:异常 {e}')

    return ' | '.join(results)


if __name__ == '__main__':
    print(notify('WorkBuddy 加油站 - 推送自检', '如果你能看到这条消息，说明推送通道已打通。'))
