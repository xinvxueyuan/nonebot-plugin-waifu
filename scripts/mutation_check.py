# ruff: noqa: T201  —— 这是命令行脚本：print 就是它的输出方式（不是漏删的调试语句）
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
RELATIONS = ROOT / "src/plugins/nonebot_plugin_waifu/relations.py"
#: 2026-10-14（v0.5.1）：`wife_superusers` 的裸数字护栏（int → str）
CONFIG = ROOT / "src/plugins/nonebot_plugin_waifu/config.py"
NOTICES = ROOT / "src/plugins/nonebot_plugin_waifu/notices.py"
LAYOUT = ROOT / "src/plugins/nonebot_plugin_waifu/layout.py"
#: 2026-10-15（v0.7.0）：关系图改用 G6 组件库渲染 —— 昵称转义与标签旋转这两个锚点
#: 原先落在 `render._node_html` / `_LABEL_TEMPLATE`（已随 div 绘制退役），
#: 现在钉到等价的**新点位**（同一风险、同一行为）。
GRAPH = ROOT / "src/plugins/nonebot_plugin_waifu/graph_g6.py"

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


# ⚠️ 锚点一律用 `str.count` 自查唯一（含多行锚点）：命中 0/2 次都会被判「跳过」，
# 而跳过会计进分母 → 退出码 1，看着像「测试漏网」，其实只是锚点写歪了。
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
        "M5 抽签不排除自己",
        RULES,
        "        if c.user_id != me\n        and c.user_id not in excluded",
        "        if True\n        and c.user_id not in excluded",
    ),
    (
        "M7 性别未知改成不通过（会让隐藏性别的人静默抽不到）",
        RULES,
        '    if me == "unknown" or cand == "unknown":\n'
        "        return True  # 任一方性别隐藏 → 判为通过",
        '    if me == "unknown" or cand == "unknown":\n        return False',
    ),
    (
        "M8 「正常」档退化成不筛（取向失效）",
        RULES,
        '    if mode not in {"normal", "reverse"}:\n'
        "        return True  # all / 未知取值：不过滤",
        "    return True\n"
        '    if mode not in {"normal", "reverse"}:\n'
        "        return True",
    ),
    (
        "M9 「今日老婆」已有记录时改成重新抽（违反只展示口径）",
        HANDLE,
        "    if existing is not None:\n"
        '        return Action(action="show", row=existing)',
        "    if existing is not None:\n"
        '        return Action(action="draw", row=existing)',
    ),
    (
        "M10 娶老婆不做双向确认（单方即可绑定）",
        HANDLE,
        "        reverse = store.get_pending(\n"
        '            group_id, requester=t, partner=requester, kind="marry"\n'
        "        )\n"
        "        if reverse is not None:\n"
        '            ts = float(reverse["requested_at"])\n'
        "            if rules.pending_alive(requested_at=ts, now=now):",
        "        reverse = store.get_pending(\n"
        '            group_id, requester=t, partner=requester, kind="marry"\n'
        "        )\n"
        "        if True:\n"
        "            ts = 0.0\n"
        "            if True:",
    ),
    (
        "M12 「确认老婆」顺手把对方也锁了（用户明确不要）",
        HANDLE,
        "    if not store.lock_daily(gid, me, today):",
        "    store.lock_daily("
        "gid, int((store.get_daily(gid, me, today) or {"
        '"partner_id": 0})["partner_id"]), today)\n'
        "    if not store.lock_daily(gid, me, today):",
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
        "        logger.warning("
        '"老婆卡片渲染失败，回退纯文本: %s: %s", type(e).__name__, e)\n'
        "        return None",
        "        raise",
    ),
    (
        "M15 文案模板写错时改成抛出（命令会变成「没反应」）",
        HANDLE,
        "    except (KeyError, IndexError, ValueError) as e:\n        logger.warning(",
        "    except (KeyError, IndexError, ValueError):\n"
        "        raise\n"
        "    if False:\n"
        "        logger.warning(",
    ),
    (
        "M16 把「@」加回结果消息（用户已要求关闭全部 @）",
        HANDLE,
        # 锚点带上一行 `group_id=event.group_id,`（`_h_graph` 里同文那一处是
        # `group_id=gid,`）：单行锚点会命中 2 次，被「多命中」整条跳过。
        "            group_id=event.group_id,\n"
        "            message=Message(MessageSegment.image(png)),",
        "            group_id=event.group_id,\n"
        "            message=MessageSegment.at(event.user_id)"
        " + MessageSegment.image(png),",
    ),
    (
        "M17 头像失败改成写负缓存（一次抖动永久缺图）",
        RENDER,
        "        logger.warning("
        '"取头像失败 user=%s: %s: %s", user_id, type(e).__name__, e)\n'
        "        return None",
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
        "M20 换一只忽略当日已抽历史（会重复抽到同一个人）",
        HANDLE,
        "        excluded=store.locked_partners(gid, me, today)\n"
        "        | store.drawn_partners(gid, me, today),",
        "        excluded=store.locked_partners(gid, me, today),",
    ),
    (
        "M21 娶老婆忽略上限配置（能无限娶）",
        HANDLE,
        "    if max_partners > 0:\n"
        "        current = store.count_partners(group_id, requester)",
        "    if False:  # 变异：彻底跳过上限判定\n"
        "        current = store.count_partners(group_id, requester)",
    ),
    (
        "M22 娶老婆不挡机器人（能娶官方机器人）",
        HANDLE,
        "        max_partners=plugin_config.waifu_max_partners,\n"
        "        robot_qqs=frozenset(\n"
        "            t\n"
        "            for t, hit in zip(\n"
        "                targets,\n"
        "                await asyncio.gather("
        "*(robot_filter.is_robot(bot, t) for t in targets)),\n"
        "                strict=False,\n"
        "            )\n"
        "            if hit\n"
        "        ),",
        "        max_partners=plugin_config.waifu_max_partners,\\\n"
        "        robot_qqs=frozenset(),",
    ),
    (
        "MN1 关系图不去重（同一对画两遍）",
        RELATIONS,
        "        if pair in seen:\n            continue",
        "        if False:\n            continue",
    ),
    (
        "MN2 关系图截断失效（大群画出长图）",
        RELATIONS,
        "    shown = edges if cap <= 0 else edges[:cap]",
        "    shown = edges",
    ),
    (
        "MN3 关系图昵称不转义（昵称里的 </script> 能炸掉整页脚本）",
        # 旧锚点 `render._node_html` 的 `html_mod.escape(...)` 已随 div 绘制退役；
        # 新点位 = 等价的注入防护：内联 JSON 里的 `<` 必须写成 `\u003c`。
        GRAPH,
        "    return json.dumps(data, ensure_ascii=False, "
        'separators=(",", ":")).replace(\n'
        '        "<", "\\\\u003c"\n'
        "    )",
        '    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))',
    ),
    (
        "MN4 倾心取成最早一次确认",
        STORE,
        '" WHERE group_id = ? ORDER BY date_key DESC, created_at DESC"',
        '" WHERE group_id = ? ORDER BY date_key ASC, created_at ASC"',
    ),
    (
        "MN5 「确认后」不再早退（回到复用抽取卡片的老路）",
        HANDLE,
        "    if locked is not None:",
        "    if False:",
    ),
    (
        "MN6 「已定人」回执的字段改字（用户口径被改坏）",
        NOTICES,
        '            ("状态", "已确认，今天不能再换"),',
        '            ("状态", "今天已经抽过了"),',
    ),
    (
        "MN7 ego 视图不过滤（@某人 时照样画全图）",
        RELATIONS,
        "        keep = tuple(e for e in self.edges if focus in (e.src, e.dst))",
        "        keep = tuple(self.edges)",
    ),
    (
        "MN8 已婚者又回到抽取池排除集里",
        HANDLE,
        # 计划原文用 `store.married_ids(gid)`，该函数已随 e01819c（已婚者照常抽）
        # 从 store 删除。改成等价的自建集合 —— 行为仍是「把已婚者塞回排除集」；
        # 沿用不存在的函数只会得到 AttributeError 式的**假捕获**（崩了，而不是
        # 测出「已婚者被排除」这个行为差异）。
        "        excluded=store.locked_partners(gid, me, today)\n"
        "        | store.drawn_partners(gid, me, today),",
        "        excluded=store.locked_partners(gid, me, today)\n"
        "        | store.drawn_partners(gid, me, today)\n"
        "        | {int(uid) for pair in store.list_marriages(gid)"
        " for uid in pair[:2]},",
    ),
    (
        "MN9 渲染不可用时不回退文本（命令会变成「没反应」）",
        HANDLE,
        # 计划原文是把 `render.render_notice` 的 `return None` 改成 `raise`：
        # 实测**漏网** —— 流程测试全部把 `render_notice` 换成了替身，真函数没人调。
        # 改钉同等风险、且**现有测试真的盖到**的点位：渲染失败后的回退文本分支
        # 被静默跳过（draw_flow / notice_flow / graph_flow 的兜底用例会红）。
        "    else:\n"
        "        await bot.send_group_msg(\n"
        "            group_id=event.group_id,\n"
        "            message=Message([\n"
        "                at,\n"
        "                MessageSegment.text(render.build_notice_fallback_text(ctx)),\n"
        "            ]),\n"
        "        )",
        "    else:\n        return  # 变异：渲染不可用时不回退文本（命令没反应）",
    ),
    (
        "MN10 回执卡丢掉字段行（图里没有关联信息）",
        RENDER,
        '  <div class="rows">{rows}</div>\n',
        "  \n",
    ),
    # ------------------------------------------------------------------ #
    # 2026-10-14（三）：新增「喜欢」关系 / 有向边真箭头 / 单人即时离婚
    # ------------------------------------------------------------------ #
    (
        "MN11 喜欢边无视「夫妻优先」（同一对既画夫妻又画喜欢 = 重边）",
        RELATIONS,
        "        if x == y or (min(x, y), max(x, y)) in seen or (x, y) in crush_dirs:",
        "        if x == y or (x, y) in crush_dirs:",
    ),
    (
        "MN12 喜欢边无视「倾心优先」（同方向既画倾心又画喜欢 = 重边）",
        RELATIONS,
        "        if x == y or (min(x, y), max(x, y)) in seen or (x, y) in crush_dirs:",
        "        if x == y or (min(x, y), max(x, y)) in seen:",
    ),
    (
        "MN13 喜欢边方向取反（src/dst 互换：箭头指向抽取者自己）",
        RELATIONS,
        "        edges.append(RelationEdge(x, y, KIND_LIKE))",
        "        edges.append(RelationEdge(y, x, KIND_LIKE))",
    ),
    (
        "MN14 喜欢取成最早一次抽取（换一只后仍指向第一只）",
        STORE,
        '" WHERE group_id = ? ORDER BY date_key DESC, updated_at DESC"',
        '" WHERE group_id = ? ORDER BY date_key ASC, updated_at ASC"',
    ),
    (
        "MN15 换一只不覆盖旧记录（喜欢停在今天第一次抽到的人）",
        STORE,
        '            f"INSERT INTO {_T_DAILY}"\n'
        '            " (group_id, user_id, date_key, partner_id,'
        ' locked, draws_used, updated_at)"\n'
        '            " VALUES (?, ?, ?, ?, 0, 1, ?)"\n'
        '            " ON CONFLICT (group_id, user_id, date_key) DO UPDATE SET"\n'
        '            "   partner_id = excluded.partner_id,',
        '            f"INSERT INTO {_T_DAILY}"\n'
        '            " (group_id, user_id, date_key, partner_id,'
        ' locked, draws_used, updated_at)"\n'
        '            " VALUES (?, ?, ?, ?, 0, 1, ?)"\n'
        '            " ON CONFLICT (group_id, user_id, date_key) DO UPDATE SET"\n'
        '            "   partner_id = partner_id,',
    ),
    (
        "MN16 有向边标记只给倾心不给喜欢（like 边退回没有方向）",
        RELATIONS,
        "        return self.kind in (KIND_CRUSH, KIND_LIKE)",
        "        return self.kind == KIND_CRUSH",
    ),
    (
        "MN17 夫妻也画端口圆（无向边不该有端口）",
        LAYOUT,
        "    if not edge.directed:\n        return line, ()",
        "    if False:  # 变异：夫妻（无向）也画端口圆\n        return line, ()",
    ),
    (
        "MN18 端口圆不区分实心/空心（方向读不出来）",
        LAYOUT,
        "        PortMark(edge.kind, edge.src, edge.dst, round(dst_x), round(dst_y),"
        " False),",
        "        PortMark(edge.kind, edge.src, edge.dst, round(dst_x), round(dst_y),"
        " True),",
    ),
    (
        "MN19 标签离线（沿法线硬推 31px = 回到用户报的「连线都对不齐」）",
        LAYOUT,
        "        ax, ay = mx + (t - 0.5) * dx, my + (t - 0.5) * dy",
        "        _nl = math.hypot(dx, dy) or 1.0\n"
        "        ax, ay = (\n"
        "            mx + (t - 0.5) * dx - dy / _nl * 31.0,\n"
        "            my + (t - 0.5) * dy + dx / _nl * 31.0,\n"
        "        )",
    ),
    (
        "MN25 端口圆画到线段中点（不在线的两端）",
        LAYOUT,
        "    return line, _port_marks(edge, x0, y0, x1, y1)",
        "    return line, _port_marks(edge, (x0 + x1) / 2, (y0 + y1) / 2, x1, y1)",
    ),
    (
        "MN26 chip 不避让节点盒（标签压到头像/名字上）",
        LAYOUT,
        "    blocked = [(b.x, b.y, b.x + b.w, b.y + b.h) for b in boxes]",
        "    blocked = []  # 变异：不再避让节点盒",
    ),
    (
        "MN28 状态胶囊回到基线对齐（标题行不再是 flex，胶囊又比文字低 4.5px）",
        RENDER,
        "  .title { display: flex; align-items: center; gap: 10px; }",
        "  .title { display: block; }",
    ),
    (
        "MN29 名单行小人头回到基线对齐（不再与文字垂直居中）",
        RENDER,
        "  .item { display: flex; align-items: center; margin-top: 8px; }",
        "  .item { display: block; margin-top: 8px; }",
    ),
    (
        "MN30 抽取卡头像行回到基线对齐（昵称掉到头像下缘，偏 9px）",
        RENDER,
        "  .body { display: flex; align-items: center; }",
        "  .body { display: block; }",
    ),
    (
        "MN27 标签不旋转（文本不跟随线条）",
        # 旧锚点是 `.elab` 的 `transform:rotate({deg}deg)`（div 绘制已退役）；
        # 新点位 = 自绘胶囊把旋转烘进 SVG 的那一步。
        GRAPH,
        '<g transform="rotate({deg} {gx:.1f} {gy:.1f})">',
        '<g transform="none">',
    ),
    (
        "MN20 图例丢掉「喜欢」一行（图上多一种线却不说是什么）",
        RENDER,
        '    "粉色=夫妻（双向） · 琥珀=倾心 · 蓝色=喜欢 · "\n'
        '    "有向边两端：实心圆=发起方，空心圆=被指向的一方"',
        '    "粉色=夫妻（双向） · 琥珀=倾心 · "',
    ),
    (
        "MN21 关系名标签回到「倾心→」字符（方向又只靠文字）",
        RENDER,
        '    "crush": "倾心",',
        '    "crush": "倾心→",',
    ),
    (
        "MN22 离婚后又悄悄写回 pending（回到双向确认）",
        HANDLE,
        "    store.divorce(group_id=group_id, a=requester, b=partner)",
        "    store.divorce(group_id=group_id, a=requester, b=partner)\n"
        "    store.set_pending(\n"
        "        group_id=group_id,\n"
        "        requester=requester,\n"
        "        partner=partner,\n"
        '        kind="divorce",\n'
        "        requested_at=0.0,\n"
        "    )",
    ),
    (
        "MN23 离婚改回「等对方确认」（不即时生效）",
        HANDLE,
        "    store.divorce(group_id=group_id, a=requester, b=partner)\n"
        "    return MarriageOutcome(\n"
        "        bound=False,\n"
        "        partner=partner,\n"
        '        message="已离婚",\n'
        '        lines=((partner, "已离婚"),),\n'
        "    )",
        "    store.set_pending(\n"
        "        group_id=group_id,\n"
        "        requester=requester,\n"
        "        partner=partner,\n"
        '        kind="divorce",\n'
        "        requested_at=0.0,\n"
        "    )\n"
        "    return MarriageOutcome(\n"
        "        bound=False,\n"
        "        partner=partner,\n"
        '        message="已申请离婚，等待对方确认",\n'
        '        lines=((partner, "已申请离婚"),),\n'
        "    )",
    ),
    (
        "MN35 wife_superusers 裸数字护栏失效（int 不转 str → 炸插件）",
        CONFIG,
        "        if isinstance(v, int):\n            return str(v)",
        "        if isinstance(v, int):\n            return v",
    ),
    (
        "MN31 指婚白名单失效（非白名单用户也能指婚）",
        HANDLE,
        '    return int(getattr(event, "user_id", 0) or 0) in wife_superuser_ids()',
        "    return True",
    ),
    (
        "MN32 指婚只绑第一个对象（a 只娶到 b）",
        HANDLE,
        "        store.clear_pending(group_id, requester=head, partner=t,"
        ' kind="marry")\n'
        "        store.marry(group_id=group_id, a=head, b=t)",
        "        break",
    ),
    (
        "MN33 指婚忘了剔自己（能把自己指给自己）",
        HANDLE,
        "    targets = [t for t in dict.fromkeys(partners) if t != head]",
        "    targets = list(dict.fromkeys(partners))",
    ),
    (
        "MN34 指婚不清旧的求婚请求（之后回发确认时语义矛盾）",
        HANDLE,
        "        store.clear_pending(group_id, requester=head, partner=t,"
        ' kind="marry")',
        "        pass",
    ),
    (
        "MN24 关系图接线漏传 likes（抽到的「喜欢」边全丢）",
        HANDLE,
        "        likes=store.latest_drawn_partners(gid),",
        "        likes={},",
    ),
    (
        "MN36 大簇退回单环硬塞（28 人又叠成一团，2026-10-15 实拍回归）",
        LAYOUT,
        "        plan = _ring_plan(len(ring))",
        "        plan = ((float(RING_R), len(ring)),)  # 变异：人多也硬塞单环",
    ),
    (
        "MN37 大簇时卡片不加宽（分环后的簇被裁掉一圈）",
        RENDER,
        "    return min(GRAPH_CARD_WIDTH_MAX, max(GRAPH_CARD_WIDTH,"
        " widest + 2 * _CARD_PAD_X))",
        "    return GRAPH_CARD_WIDTH  # 变异：卡片永远 880",
    ),
    (
        "MN38 用尽时不擦（用尽后最后抽到的仍在关系图上算「喜欢」）",
        HANDLE,
        "        store.clear_drawn_partner(group_id=gid, user_id=me, date_key=today)",
        "        pass  # 变异：被配额挡下也不清",
    ),
    (
        "MN39 清掉后「今日老婆」又露出那一位（少一次自指向判断）",
        HANDLE,
        "        if partner == me:",
        "        if False:  # 变异：继续露出那一位",
    ),
    (
        "MN40 确认老婆把「已清掉」的自指向锁成倾心（把自己锁给自己）",
        STORE,
        """        if partner_id == user_id:
            # 已被配额清掉的那天（partner 自指向）→ 没有可确认的对象，
            # 别把自己锁成「倾心」（用户 2026-10-17 口径）。
            return False
""",
        """        if False:  # 变异：把自指向也锁成倾心
            return False
""",
    ),
    (
        "MN41 清掉时把计数也清了（白送一次抽取）",
        STORE,
        '            f"UPDATE {_T_DAILY} SET partner_id = user_id, updated_at = ?"',
        '            f"UPDATE {_T_DAILY} SET partner_id = user_id,"'
        ' " draws_used = 0, updated_at = ?"',
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
        # ⚠️ 必须用 read_text：工作树是 CRLF（git autocrlf=true），而锚点里的换行
        # 写的是 \n —— 字节级比对会一条都命中不了。
        orig = path.read_text(encoding="utf-8")
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
