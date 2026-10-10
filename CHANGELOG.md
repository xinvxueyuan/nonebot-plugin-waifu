# Changelog

本项目所有值得记录的变更都写在这里。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.2.0] - 2026-10-11

### Added

- 「结婚」：**「娶老婆」的同义命令**（生产实证：命令只注册在「娶老婆」上，
  群里发「结婚 @某人」毫无反应）。裸词 `结婚` 与 `/结婚` 都能触发，`/娶老婆` 照旧；
  **只加这一个词**，`离婚` 不加别名。
- 当日抽取历史（表 `waifu_drawn`）：「换一只」当天**不会重复抽到同一个人**
  （首次抽到的那位也算）；抽遍后提示「今天群里的人你已经抽遍了，明天再来」，次日恢复。
  只影响自己、不影响别人。
- `WAIFU_MAX_PARTNERS` 配置（默认 `1`，写 `0` = **不限**）：一个人最多能娶几个老婆。
  判定只查发起方，超额**整条拒绝、全有全无**。
- 「娶老婆」支持**一次 @ 多人**（也接受多个空格 / 逗号分隔的 QQ 号）：对每人各发一次
  请求、各算各的，谁先回发就绑定谁。
- 机器人判定与守卫（`robot_filter`：公开 API `get_robot_uin_range` + 生产实测的内置兜底
  区间 + 3600s TTL 缓存；任何失败都降级、**绝不抛**）：「娶老婆」的目标命中机器人
  （含本机器人自身）→ **整条拒绝**、一行不改。抽取路径不做机器人过滤。

### Changed

- 婚姻表改为**支持多配偶**：主键改为 `(group_id, user_id, partner_id)`；旧表仅在
  **零行**时安全重建（非零行不动，不冒丢数据的风险）。
- 「确认老婆」**即定死**：当天不能再「换一只」换人 —— 直接拒绝，且**不消耗次数**。
- 结果消息**完全不 @**（纯卡片图）：不 @ 抽取人、也不 @ 被抽中者，不另发通知消息，
  **被抽中者不会收到任何提示**；卡片里与「QQ号」重复的 `@` 列一并去掉。

### Fixed

- **确认锁被后续重抽冲掉**：锁原先存在 `waifu_daily` 那一行上，任何一次重抽的
  `DO UPDATE SET locked = 0` 都会把它清掉（生产实测该表 `locked = 1` 的行数为 0）。
  改为**独立表 `waifu_locked`**，键 = `(群, 用户, 日期)`，重抽不再影响它。
- **`locked_partners` 未按用户隔离**：查询少了「属于谁」这一维，于是 A 确认之后
  **B 也抽不到**那个人（全局泄漏）。改为一律按 `user_id` 过滤 —— 只影响自己。

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
  可用 `{drawer}` / `{partner}` / `{user_id}` 三个占位符），正文含 QQ号、昵称、头像、
  性别、最后发言时间；头像走公共 CDN 并在插件侧转 base64 内联。
- 配额显示：卡片上显示「今日已用 N/上限 次」（`WAIFU_SHOW_QUOTA_IN_CARD`）。
- 容灾：状态全部落 sqlite（stdlib `sqlite3`），启动时重放并清理过期的待确认请求。
- 渲染整体失败时回退纯文本（字段一致），不会表现成「命令没反应」。

[Unreleased]: https://github.com/xinvxueyuan/nonebot-plugin-waifu/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/xinvxueyuan/nonebot-plugin-waifu/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/xinvxueyuan/nonebot-plugin-waifu/releases/tag/v0.1.0
