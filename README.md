# nonebot-plugin-waifu

今日老婆：从群内活跃成员里抽一位当老婆，支持换一只/确认/娶老婆/离婚。

> 「今日老婆」从一个**活跃度池**里抽人：只要在群里发过言的人就会进池子
> （不活跃的人抽不到）。抽签、配额、冷却、跨天判定全部落 sqlite，
> 进程被杀 / 机器重启都不会丢。
>
> 结果卡片用 [nonebot-plugin-htmlkit](https://pypi.org/project/nonebot-plugin-htmlkit/)
> 渲染成图；它是**可选依赖**，装不上 / 渲染失败时自动回退纯文本 ——
> 渲染问题**绝不会**表现成「命令没反应」。

## 命令

| 命令 | 别名 | 说明 |
|---|---|---|
| `今日老婆` | `/今日老婆`、`抽老婆`、`/抽老婆` | 当天**首次**抽取并锁定为今日老婆；之后再发**只展示**（不换人、不消耗次数） |
| `换老婆`（**可配**） | `/换老婆`（生产 `/换一只`） | **这个才是更换词**，每次重新抽（消耗 1 次配额）。命令词由 `WAIFU_CHANGE_CMD` 决定：默认 `换老婆`，**生产用 `换一只`** |
| `确认老婆` | `/确认老婆` | 锁定今日老婆。**只影响自己**：你之后抽签不会再抽到 ta；**不锁对方**（别人仍可能抽到 ta）。每日恢复 |
| `娶老婆 @某人`（或 `娶老婆 QQ号`） | `/娶老婆` | 需**双方互相发**；3 分钟内有效；绑定后双方都不再参与他人抽取，也不能再抽群友 |
| `离婚 @某人`（或 `离婚 QQ号`） | `/离婚` | 需**双方互相发**；离婚后**发起方** 3 天内不能再抽 |

「换老婆」的命令词是**配置可变**的（`WAIFU_CHANGE_CMD`）：源码默认 `换老婆`，
生产 `.env` 写 `WAIFU_CHANGE_CMD=换一只` 即切换命令词，斜杠别名 `/换一只` 会跟着变。

## 口径说明

- **配额**：每人每天最多抽 **20** 次（`WAIFU_DAILY_LIMIT`）。**所有「抽」都计数** ——
  当天首次抽取 + 每次「换老婆」都算一次。超限文案是逐字指定的「渣男，你今天没有老婆了！」。
- **确认窗口**：娶老婆 / 离婚都要**双方互相发**才算生效，**3 分钟**内有效
  （`WAIFU_PENDING_MINUTES`，窗口外作废）。
- **离婚冷却**：离婚成功后**只有发起方**进入 **3 天**冷却（`WAIFU_DIVORCE_CD_DAYS`，
  写 `0` = 不冷却）。
- **确认老婆**：只把「自己的」当日记录标记为已确认 → 效果是**你自己**再抽时不会抽到 ta
  （换一只时也会跳过），**第二天恢复**；**不锁对方**。
- **跨天**：按 `Asia/Shanghai` 算「当天」，每天 0 点重置当日老婆与配额。

## 取向（`WAIFU_ORIENTATION`）三档

| 写法 | 中文 | 行为 |
|---|---|---|
| `normal` / `正常` | 正常 | **只抽异性** |
| `all` / `所有` | 所有 | **不限**（**默认**）；此时不查性别、零额外 API 调用 |
| `reverse` / `相反` | 相反 | **只抽同性** |

- **群员性别隐藏 / 查不到的一律判为通过** —— 不会因为查不到性别就把人排除。
- `.env` 里**写中文也行**（`WAIFU_ORIENTATION=正常`），会被自动映射。
- 「所有」是默认档，也就是「不判断性取向」那种行为。
- 也收下英文别名：`any` → `all`、`opposite` → `reverse`。

## 结果卡片首行（可配）

默认（源码里）是：

```
{你的昵称}, 你今天的群友老婆是 {老婆昵称}（{QQ}）
```

**生产用 `WAIFU_HEADLINE_TEMPLATE` 覆盖成「群幼」**：

```
WAIFU_HEADLINE_TEMPLATE={drawer}, 你今天的群幼老婆是 {partner}（{user_id}）
```

三个占位符固定为 `{drawer}`（抽的人的昵称）/ `{partner}`（老婆昵称）/ `{user_id}`（QQ 号）。
**占位符写错会让文案回退成默认模板并在日志里告警** —— 不会让命令变成「没反应」。

卡片正文另行列 @、QQ号、昵称、性别、头像、最后发言时间。

## 被抽中的人怎么收到通知

**结果消息里直接 `@` 他**（`@你` + `@ta` + 卡片图）—— 不另发消息、不写任何文案。
被 @ 到 QQ 会自然给他提示，这就是全部机制。

## 环境变量

`.env` / `.env.prod`（NoneBot 会读取）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `WAIFU_DAILY_LIMIT` | `20` | 每天每人抽取次数上限（含首次与每次换一只） |
| `WAIFU_PENDING_MINUTES` | `3` | 娶老婆/离婚确认窗口（分钟） |
| `WAIFU_DIVORCE_CD_DAYS` | `3` | 离婚后发起方冷却天数；0 = 不冷却 |
| `WAIFU_ORIENTATION` | `all` | `normal` 正常（只抽异性）/ `all` 所有（不限，默认）/ `reverse` 相反（只抽同性）；也可写中文 |
| `WAIFU_HEADLINE_TEMPLATE` | `{drawer}, 你今天的群友老婆是 {partner}（{user_id}）` | 卡片首行文案；生产覆盖为「群幼」 |
| `WAIFU_CHANGE_CMD` | `换老婆` | 「换老婆」命令词；**生产覆盖为 `换一只`** |
| `WAIFU_SHOW_QUOTA_IN_CARD` | `true` | 卡片上是否显示「今日已用 N/上限 次」 |
| `WAIFU_MEMBER_TTL_DAYS` | `0` | 活跃度保留天数；0 = 不淘汰 |
| `WAIFU_MATCH_PRIORITY` | `5` | 活跃度监听 matcher 优先级（必须 > 命令的 3） |
| `WAIFU_COMMAND_PRIORITY` | `3` | 「抽」类命令的 matcher 优先级（越小越先） |
| `WAIFU_LEFT_RECENTLY_MESSAGE` | `你刚离过婚，{days} 天后再来吧` | 离婚冷却期内的提示模板；`{days}` 会被替换为剩余天数 |

> **`WAIFU_MATCH_PRIORITY` 必须大于 `WAIFU_COMMAND_PRIORITY`**：监听是 catch-all，
> 优先级写反了命令会被监听抢先。写反**不会让插件起不来** —— 只告警并自动把监听优先级
> 调成 `命令优先级 + 2`。

## 结果卡片

htmlkit 渲染，**首行是成句文案**（见上），正文包含：**@、QQ号、昵称、头像、性别、最后发言时间**。
头像走公共 CDN（`q.qlogo.cn/headimg_dl`，与 lingchu-bot 前端同一套），
插件侧下载后转 base64 内联 —— 因为 htmlkit 的取图函数不带请求头、失败还是静默的，
直接把 http 地址写进模板会得到碎图且无报错。头像取不到时显示占位块。
渲染整体失败时回退纯文本（字段一致），**绝不表现成「命令没反应」**。

## 依赖

- `nonebot2 >= 2.5`
- `nonebot-adapter-onebot >= 2.4.6`（OneBot V11）
- `nonebot-plugin-localstore >= 0.7.4`（数据目录）
- `tzdata >= 2025.1`（`Asia/Shanghai` 时区数据）
- `httpx >= 0.26,<1.0`（头像下载）
- （可选）`nonebot-plugin-htmlkit == 0.1.0rc5`（结果卡片渲染成图；缺了回退纯文本）

## 安装（宿主以 git 子模块引入）

```bash
git submodule add -b main https://github.com/xinvxueyuan/nonebot-plugin-waifu.git \
  vendor/nonebot-plugin-waifu
git -C vendor/nonebot-plugin-waifu checkout <钉定的 SHA>
```

宿主 `pyproject.toml`：

```toml
[tool.nonebot]
plugin_dirs = ["vendor/nonebot-plugin-waifu/src/plugins"]
```

`.env` 里加两行（示例，按需改）：

```
WAIFU_CHANGE_CMD=换一只
WAIFU_HEADLINE_TEMPLATE={drawer}, 你今天的群幼老婆是 {partner}（{user_id}）
```

## 开发

```bash
env -u UV_PYTHON -u UV_PROJECT_ENVIRONMENT -u SSL_CERT_FILE -u VIRTUAL_ENV -u PYTHONPATH uv sync --all-groups
env -u UV_PYTHON -u UV_PROJECT_ENVIRONMENT -u SSL_CERT_FILE -u VIRTUAL_ENV -u PYTHONPATH uv run pytest tests/ -q
env -u UV_PYTHON -u UV_PROJECT_ENVIRONMENT -u SSL_CERT_FILE -u VIRTUAL_ENV -u PYTHONPATH uv run ruff check src/ tests/
env -u UV_PYTHON -u UV_PROJECT_ENVIRONMENT -u SSL_CERT_FILE -u VIRTUAL_ENV -u PYTHONPATH uv run pyright
```

## License

MIT。
