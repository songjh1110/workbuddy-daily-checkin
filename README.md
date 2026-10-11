# WorkBuddy 加油站积分 · 每日自动签到

每天定时签到领积分，**电脑关不关机都不影响**（任务跑在 GitHub 云端），结果微信推送。
不依赖本机客户端在线，也不需要抓包、不需要 Cookie。

```
领取积分/
├─ main.py          主流程：令牌换票 → 查状态 → 签到 → 旅行闭环 → 推送
├─ wb_client.py     WorkBuddy 接口客户端（纯标准库，无第三方依赖）
├─ notifier.py      微信推送：Server 酱 / PushPlus / 企业微信机器人
├─ requirements.txt requests（推送用）
├─ bootstrap_github.py  一键建私有仓库 + 写 Secrets + 触发运行（免本机 git）
└─ .github/workflows/daily-claim.yml   每日定时任务（北京 09:00 + 09:40）
```

## 一、原理

本机 `/ 云端只需一个「长效令牌（refresh token）`，运行时换成短效 accessToken 再调接口：

| 步骤 | 接口 |
|---|---|
| 令牌换票 | `POST https://copilot.tencent.com/v2/plugin/auth/token/refresh`（头 `X-Refresh-Token`） |
| 查状态（只读） | `POST https://www.codebuddy.cn/v2/billing/meter/checkin-activity-status` |
| 签到（写，幂等） | `POST https://www.codebuddy.cn/v2/billing/meter/daily-checkin` |
| 派猫猫旅行（可选） | `GET /activity/growth/buddy/travel/status`、`POST .../claim`、`POST .../depart`，域名 `https://www.workbuddy.cn`，**路径不带 `/v2`** |

- 签到幂等：当天已签返回 `HTTP 400 / code 10001 / "今天已签到，请明天再来"`，**不重复发积分**，程序按成功处理。
- **令牌轮换不会让旧令牌失效**（已实测：轮换后旧令牌仍返回 `code 0`），所以一个 Secret 可以长期用。
- 写操作只有 `daily-checkin`、`travel/claim`、`travel/depart` 三个，绝不碰兑换/抽奖接口。

## 二、本机跑一次（验证）

```powershell
cd "i:/000/本地智能体/Python项目/领取积分"
pip install -r requirements.txt
$env:WB_REFRESH_TOKEN = "<长效令牌>"
$env:WB_TRAVEL_AUTO  = "1"
python main.py
```

看到 `✅ ... 已签到` 且退出码 0，说明链路通了，再上云。

> 拿长效令牌：`python "$env:USERPROFILE\.workbuddy\skills\totorosir-workbuddy-score\scripts\buddy_station.py" --export-rt`
> （本机安装着「WorkBuddy积分助手」Skill 时可用；输出等同账号钥匙，别外传、别贴群里。）

## 三、配置微信推送（二选一）

| 渠道 | 获取 | 环境变量 |
|---|---|---|
| Server 酱 Turbo | https://sct.ftqq.com/ 扫码登录 → 复制 SendKey | `SERVERCHAN_KEY` |
| PushPlus | https://www.pushplus.plus/ 微信登录 → 复制 token | `PUSHPLUS_TOKEN` |
| 企业微信群机器人（可选） | 群设置 → 添加机器人 → 复制 Webhook | `WECOM_WEBHOOK` |

自检：`python notifier.py`（手机收到「推送自检」即通道打通）。成功也想静默：设 `PUSH_ON_SUCCESS=0`。

## 四、部署到 GitHub Actions（关机也能签）

### 4.1 一键部署（推荐，本机无需 git）

1. 打开 <https://github.com/settings/tokens> → **Generate new token (classic)** → 勾选 **`repo`**（含 workflow）→ 生成并**复制保存**（只显示一次）。
2. 在项目目录里执行：

```powershell
cd "i:/000/本地智能体/Python项目/领取积分"
pip install pynacl
python bootstrap_github.py
```

3. 按提示粘贴 PAT（输入不回显）。脚本会自动完成：
   - 建**私有仓库** `workbuddy-daily-checkin`（默认名，可 `--repo xxx` 改）
   - 用 GitHub API 直接提交全部代码（不依赖本机 git）
   - 从本机 WorkBuddy 登录态**导出长效令牌并写入 Secret** `WB_REFRESH_TOKEN`
   - 若环境变量里有 `SERVERCHAN_KEY` / `PUSHPLUS_TOKEN` / `WECOM_WEBHOOK`，一并写入 Secret
   - 触发首次运行

最后到 `https://github.com/<你的用户名>/workbuddy-daily-checkin/actions` 确认结果即可。

> PAT 只在你自己的终端里输入，不入库、不进聊天记录。也可用 `--pat-file <路径>` 或环境变量 `GH_PAT` 提供。

### 4.2 手动部署（不想装 pynacl 时）

1. 手动建**私有仓库**（凭据泄露 = 账号被盗，务必 private），把本目录（不含令牌文件）推上去。
2. **Settings → Secrets and variables → Actions → New repository secret** 添加：
   - `WB_REFRESH_TOKEN`：长效令牌
   - `SERVERCHAN_KEY` / `PUSHPLUS_TOKEN` / `WECOM_WEBHOOK`：推送（按需）
3. **Actions** 页允许工作流 → **Run workflow** 手动验证一次。
4. 之后每天北京时间 **09:00** 自动跑（09:40 兜底一次）。改时间编辑 yml 里的 cron（UTC，北京时间减 8）。

> GitHub 规定仓库 60 天无 commit 会暂停 cron，届时手动 Run 一次即可恢复。

## 五、可选参数

| 变量 | 默认 | 说明 |
|---|---|---|
| `WB_TRAVEL_AUTO` | `0` | 设 `1` 跑「派猫猫旅行」闭环：到达先领，空闲再派（先查 `daily_limit_reached`，达上限不派） |
| `WB_LOCATION` | 随机 | 派遣地点 `1` 咖啡馆 / `2` 商场 / `3` 健身房 / `4` 古镇客栈（收益率相同，无需挑） |
| `PUSH_ON_SUCCESS` | `1` | 成功是否也推送 |
| `HTTP_TIMEOUT` | `20` | 超时秒数（代码内常量） |

## 六、排错

| 现象 | 原因 / 处理 |
|---|---|
| `令牌刷新失败` | 长效令牌失效或被撤销 → 重新 `--export-rt` 导出并更新 Secret |
| `接口令牌被拒 401/403` | 同上 |
| `今日已签到，无需重复领取` | **正常**，不是失败，退出码 0 |
| 没按时跑 | GitHub 定时有排队延迟（高峰几十分钟），已排两次兜底 |
| 想把轮换令牌自动写回 Secret | 配 `GH_PAT`（repo 权限 Secrets 读写）+ 装 `pynacl`。实测旧令牌轮换后仍有效，**不配也行** |
