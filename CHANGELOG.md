# Changelog

本项目所有值得记录的变更都写在这里。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-10-10

首个版本。

### Added

- 「今日老婆」：当天首次抽取并从活跃成员池锁定一位今日老婆；之后再发只展示当前老婆
  （不换人、不消耗次数）。
- 「换老婆」：可配置命令词（`WAIFU_CHANGE_CMD`，生产覆盖为「换一只」），每次重新抽取
  并消耗 1 次配额。
- 「确认老婆」：锁定今日老婆，只影响自己（之后抽签与换一只时都会跳过 ta），每日恢复，
  不锁对方。
- 「娶老婆 / 离婚」：需双方互相发、3 分钟内有效；绑定后双方不再参与他人抽取；
  离婚后**发起方**进入 3 天冷却。
- 活跃度池：`on_message` catch-all（`block=False`，绝不阻断）记录群成员发言，
  不活跃的人抽不到；`WAIFU_MEMBER_TTL_DAYS` 可淘汰长期不发言者。
- 取向三档（`WAIFU_ORIENTATION`）：`normal` 只抽异性 / `all` 不限（默认）/ `reverse` 只抽同性；
  支持中文写法；性别隐藏或查不到的人一律判为通过。
- 结果卡片（可选 `nonebot-plugin-htmlkit`）：首行成句文案（`WAIFU_HEADLINE_TEMPLATE`
  可用 `{drawer}` / `{partner}` / `{user_id}` 三个占位符），正文含 @、QQ号、昵称、头像、
  性别、最后发言时间；头像走公共 CDN 并在插件侧转 base64 内联。
- 配额显示：卡片上显示「今日已用 N/上限 次」（`WAIFU_SHOW_QUOTA_IN_CARD`）。
- 容灾：状态全部落 sqlite（stdlib `sqlite3`），启动时重放并清理过期的待确认请求。
- 渲染整体失败时回退纯文本（字段一致），不会表现成「命令没反应」。

[Unreleased]: https://github.com/xinvxueyuan/nonebot-plugin-waifu/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/xinvxueyuan/nonebot-plugin-waifu/releases/tag/v0.1.0
