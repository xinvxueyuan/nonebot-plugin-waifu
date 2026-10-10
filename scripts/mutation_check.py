"""变异检验：把每处修复**故意改坏**，确认对应测试真的变红。

没红过的断言只是装饰 —— 它可能断言了恒真条件、或读错了属性名，
这两种情况在正常代码上都是绿的。用法：

    env -u UV_PYTHON ... uv run python scripts/mutation_check.py
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HANDLE = ROOT / "src/plugins/nonebot_plugin_waifu/handle.py"
PKG = ROOT / "src/plugins/nonebot_plugin_waifu/__init__.py"
RULES = ROOT / "src/plugins/nonebot_plugin_waifu/rules.py"
RENDER = ROOT / "src/plugins/nonebot_plugin_waifu/render.py"
STORE = ROOT / "src/plugins/nonebot_plugin_waifu/store.py"

# ⚠️ 不要加 `--no-cov`（本仓未装 pytest-cov，会报 unrecognized arguments）
TESTS = ["tests/", "-q", "-p", "no:cacheprovider", "-x"]

#: 剔除会干扰子进程的环境变量（本机 uv 约定）。
_DROP_ENV = (
    "UV_PYTHON",
    "UV_PROJECT_ENVIRONMENT",
    "SSL_CERT_FILE",
    "VIRTUAL_ENV",
    "PYTHONPATH",
)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in _DROP_ENV:
        env.pop(key, None)
    return env


def run() -> bool:
    r = subprocess.run(
        ["uv", "run", "python", "-m", "pytest", *TESTS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=_child_env(),
        encoding="utf-8",
        errors="replace",
    )
    return r.returncode == 0


MUTATIONS = [
    (
        "M1 catch-all 改成 block=True（会饿死别的插件）",
        PKG,
        "block=False,",
        "block=True,",
    ),
    (
        "M2 配额文案改字",
        RULES,
        'QUOTA_MESSAGE: Final[str] = "渣男，你今天没有老婆了！"',
        'QUOTA_MESSAGE: Final[str] = "你今天没有老婆了！"',
    ),
    (
        "M3 确认窗口从 3 分钟放大",
        RULES,
        "PENDING_WINDOW_SECONDS: Final[int] = 180",
        "PENDING_WINDOW_SECONDS: Final[int] = 99999",
    ),
    (
        "M4 离婚 CD 从 3 天改 0",
        RULES,
        "DIVORCE_CD_SECONDS: Final[int] = 3 * 86400",
        "DIVORCE_CD_SECONDS: Final[int] = 0",
    ),
    (
        "M5 抽签不排除自己",
        RULES,
        "        if c.user_id != me\n        and c.user_id not in excluded",
        "        if True\n        and c.user_id not in excluded",
    ),
    (
        "M6 抽签不排除已婚",
        RULES,
        "        and c.user_id not in married",
        "        and True",
    ),
    (
        "M7 性别未知改成不通过（会让隐藏性别的人静默抽不到）",
        RULES,
        '    if me == "unknown" or cand == "unknown":\n        return True  # 任一方性别隐藏 → 判为通过',
        '    if me == "unknown" or cand == "unknown":\n        return False',
    ),
    (
        "M8 「正常」档退化成不筛（取向失效）",
        RULES,
        '    if mode not in {"normal", "reverse"}:\n        return True  # all / 未知取值：不过滤',
        '    return True\n    if mode not in {"normal", "reverse"}:\n        return True',
    ),
    (
        "M9 「今日老婆」已有记录时改成重新抽（违反只展示口径）",
        HANDLE,
        '    if existing is not None:\n        return Action(action="show", row=existing)',
        '    if existing is not None:\n        return Action(action="draw", row=existing)',
    ),
    (
        "M10 娶老婆不做双向确认（单方即可绑定）",
        HANDLE,
        '        reverse = store.get_pending(\n'
        '            group_id, requester=t, partner=requester, kind="marry"\n'
        '        )\n'
        '        if reverse is not None:\n'
        '            ts = float(reverse["requested_at"])\n'
        '            if rules.pending_alive(requested_at=ts, now=now):',
        '        reverse = store.get_pending(\n'
        '            group_id, requester=t, partner=requester, kind="marry"\n'
        '        )\n'
        '        if True:\n'
        '            ts = 0.0\n'
        '            if True:',
    ),
    (
        "M11 离婚 CD 给双方而不是只给发起方",
        HANDLE,
        "                    user_id=requester,",
        "                    user_id=partner,",
    ),
    (
        "M12 「确认老婆」顺手把对方也锁了（用户明确不要）",
        HANDLE,
        "    if not store.lock_daily(gid, me, today):",
        '    store.lock_daily(gid, int((store.get_daily(gid, me, today) or {"partner_id": 0})["partner_id"]), today)\n    if not store.lock_daily(gid, me, today):',
    ),
    (
        "M13 昵称不转义（XSS/版面破坏）",
        RENDER,
        "        nickname=html_mod.escape(ctx.nickname),",
        "        nickname=ctx.nickname,",
    ),
    (
        "M14 渲染失败改成抛出（会表现成命令无反应）",
        RENDER,
        '        logger.warning("老婆卡片渲染失败，回退纯文本: %s: %s", type(e).__name__, e)\n        return None',
        "        raise",
    ),
    (
        "M15 文案模板写错时改成抛出（命令会变成「没反应」）",
        HANDLE,
        "    except (KeyError, IndexError, ValueError) as e:\n        logger.warning(",
        "    except (KeyError, IndexError, ValueError):\n        raise\n    if False:\n        logger.warning(",
    ),
    (
        "M16 把「@」加回结果消息（用户已要求关闭全部 @）",
        HANDLE,
        "            message=Message(MessageSegment.image(png)),",
        "            message=MessageSegment.at(event.user_id) + MessageSegment.image(png),",
    ),
    (
        "M17 头像失败改成写负缓存（一次抖动永久缺图）",
        RENDER,
        '        logger.warning("取头像失败 user=%s: %s: %s", user_id, type(e).__name__, e)\n        return None',
        '        _AVATAR_CACHE[key] = ""\n        return None',
    ),
    (
        "M18 确认锁改回读 waifu_daily.locked（重抽会冲掉、且全局泄漏给他人）",
        STORE,
        '            f"SELECT partner_id FROM {_T_LOCK}"\n'
        '            " WHERE group_id = ? AND user_id = ? AND date_key = ?",\n'
        "            (group_id, user_id, date_key),",
        '            f"SELECT partner_id FROM {_T_DAILY}"\n'
        '            " WHERE group_id = ? AND date_key = ? AND locked = 1",\n'
        "            (group_id, date_key),",
    ),
    (
        "M19 换一只忽略已确认锁（确认后还能换人）",
        HANDLE,
        "            locked_today=store.has_lock(gid, me, today),",
        "            locked_today=False,",
    ),
    (
        "M20 换一只忽略当日已抽历史（会重复抽到同一个人）",
        HANDLE,
        "        excluded=store.locked_partners(gid, me, today)\n"
        "        | store.drawn_partners(gid, me, today),",
        "        excluded=store.locked_partners(gid, me, today),",
    ),
    (
        "M21 娶老婆忽略上限配置（能无限娶）",
        HANDLE,
        "    if max_partners > 0:\n        current = store.count_partners(group_id, requester)",
        "    if False:  # 变异：彻底跳过上限判定\n        current = store.count_partners(group_id, requester)",
    ),
    (
        "M22 娶老婆不挡机器人（能娶官方机器人）",
        HANDLE,
        "        max_partners=plugin_config.waifu_max_partners,\n"
        "        robot_qqs=frozenset(\n"
        "            t\n"
        "            for t, hit in zip(\n"
        "                targets,\n"
        "                await asyncio.gather(*(robot_filter.is_robot(bot, t) for t in targets)),\n"
        "                strict=False,\n"
        "            )\n"
        "            if hit\n"
        "        ),",
        "        max_partners=plugin_config.waifu_max_partners,\n"
        "        robot_qqs=frozenset(),",
    ),
]


def main() -> int:
    print("=== 基线 ===")
    if not run():
        print("  ✗ 基线已红 —— 先修好再来做变异检验")
        return 1
    print("  ✓ 全绿")

    results = []
    for label, path, old, new in MUTATIONS:
        orig = path.read_bytes().decode("utf-8")
        cnt = orig.count(old)
        if cnt != 1:
            print(f"  ⚠ {label}: 锚点命中 {cnt} 次 —— 跳过（锚点要能唯一定位）")
            results.append((label, None))
            continue
        path.write_bytes(orig.replace(old, new).encode("utf-8"))
        try:
            caught = not run()
        finally:
            path.write_bytes(orig.encode("utf-8"))
        results.append((label, caught))
        print(f"  {'[捕获]' if caught else '[漏网]'} {label}")

    caught_n = sum(1 for _, c in results if c is True)
    print(f"\n=== 捕获 {caught_n}/{len(results)} ===")
    for label, c in results:
        mark = "✓" if c else ("✗" if c is False else "⚠")
        print(f"  {mark} {label}")
    return 0 if caught_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
