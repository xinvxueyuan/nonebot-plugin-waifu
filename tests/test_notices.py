"""notices 编译器（业务状态 → 回执内容）：字段齐全、用户逐字文案、可渲染。

A3 的「已定人」回执、配额与「已抽遍」/「空池」回执都从这里编译 —— 纯函数，
不碰 IO、不读配置，所以每条断言都能确定性跑。
"""

from __future__ import annotations


def test_locked_notice_carries_state_data_and_next_step():
    """**A3 信息密度守卫**：标题 / 当事人 / 状态 / 今日数据 / 下一步一个不少。"""
    from nonebot_plugin_waifu import notices

    n = notices.locked_notice(
        me_name="甲", partner_id=20002, partner_name="乙", used=1, limit=20
    )
    assert n.title == "今天已经定人", "不是抽取卡片，而是「已定人」回执"
    assert n.tone == "deny"
    assert n.avatar_user_id == 20002  # 当事人头像在
    assert "甲" in n.headline and "乙" in n.headline and "20002" in n.headline
    assert ("状态", "已确认，今天不能再换") in n.fields
    assert any("1/20" in str(v) for _k, v in n.fields)  # 数据在
    assert n.note  # 下一步在


def test_quota_notice_keeps_exact_message_and_data():
    from nonebot_plugin_waifu import notices, rules

    n = notices.quota_notice(used=20, limit=20)
    assert n.title == rules.QUOTA_MESSAGE  # 用户逐字指定，不许改
    assert any("20/20" in str(v) for _k, v in n.fields)
    assert n.note


def test_all_drawn_notice_states_drawn_and_candidates():
    """「今天抽遍了」：抽到过几人 / 群里可选几人 —— 与「空池」分开。"""
    from nonebot_plugin_waifu import notices, rules

    n = notices.all_drawn_notice(drawn=3, candidates=3)
    assert n.title == rules.ALL_DRAWN_MESSAGE
    assert n.tone == "warn"
    assert any("3 人" in str(v) for _k, v in n.fields)
    assert n.note


def test_empty_pool_notice_keeps_exact_wording():
    """池子本来就没人 —— 旧文案逐字保留（不得误报「抽遍了」）。"""
    from nonebot_plugin_waifu import notices

    n = notices.empty_pool_notice()
    assert n.title == "群里还没有能抽的人（要有人发过言才会进池子）"
    assert n.note


def test_every_notice_is_renderable_html():
    """所有编译器产物都能转成 HTML、**无网络 URL**、且有等价文本（防「空图」）。"""
    from nonebot_plugin_waifu import notices, render

    cases = [
        notices.locked_notice(
            me_name="甲", partner_id=2, partner_name="乙", used=1, limit=20
        ),
        notices.quota_notice(used=20, limit=20),
        notices.all_drawn_notice(drawn=3, candidates=3),
        notices.empty_pool_notice(),
        notices.confirm_notice(
            me_name="甲",
            partner_id=2,
            partner_name="乙",
            last_seen_text="2026-10-14 00:31",
            used=1,
            limit=20,
        ),
        notices.marry_notice(
            requester_name="甲", results=[(2, "等待确认")], minutes=3, my_count=0, cap=1
        ),
        notices.divorce_notice(me_name="甲", partner_id=2, partner_name="乙"),
        notices.usage_notice(cmd="娶老婆", lines=("娶老婆 @某人",)),
        notices.graph_empty_notice(members=107),
        notices.no_relations_notice(focus_id=2),
    ]
    for ctx in cases:
        html = render.build_notice_html(ctx)
        assert ctx.title in html or ctx.headline in html
        assert "http" not in html
        assert render.build_notice_fallback_text(ctx).strip()


# --------------------------------------------------------------------------- #
# Task 8A：确认 / 娶 / 离 / 用法 / 空状态 —— 全部图片化
# --------------------------------------------------------------------------- #
def test_marry_notice_lists_every_target():
    """**多目标逐行**：一次 @ 多人时每人都要有自己的状态行（一张图看全）。"""
    from nonebot_plugin_waifu import notices

    n = notices.marry_notice(
        requester_name="甲",
        results=[(20002, "等待确认"), (30003, "已绑定")],
        minutes=3,
        my_count=1,
        cap=3,
    )
    assert [it.user_id for it in n.items] == [20002, 30003]
    assert any("3 分钟" in str(v) for _k, v in n.fields)


def test_marry_notice_failure_keeps_reason_in_the_image():
    """失败（机器人/超上限/娶自己）时原因必须是 headline —— 不许只在日志里。"""
    from nonebot_plugin_waifu import notices

    n = notices.marry_notice(
        requester_name="甲",
        results=[(4014989500, "不能娶机器人")],
        minutes=3,
        my_count=0,
        cap=1,
        failed="不能娶机器人当老婆（4014989500）",
    )
    assert n.title == "娶老婆没成功"
    assert n.tone == "deny"
    assert n.headline == "不能娶机器人当老婆（4014989500）"
    assert [it.user_id for it in n.items] == [4014989500]


def test_marry_notice_says_the_cap_is_unlimited_when_zero():
    """`cap=0` = 不限：不能显示成「0/0」（会被读成「一个都不许娶」）。"""
    from nonebot_plugin_waifu import notices

    n = notices.marry_notice(
        requester_name="甲", results=[(2, "等待确认")], minutes=3, my_count=2, cap=0
    )
    assert ("你的老婆数", "2（不限）") in n.fields


def test_confirm_notice_carries_partner_profile():
    """确认成功回执要带**当事人资料**（昵称/QQ号/最后发言）与锁定范围。"""
    from nonebot_plugin_waifu import notices

    n = notices.confirm_notice(
        me_name="甲",
        partner_id=20002,
        partner_name="乙",
        last_seen_text="2026-10-14 00:31",
        used=1,
        limit=20,
    )
    keys = {k for k, _v in n.fields}
    assert {"昵称", "QQ号", "最后发言", "状态", "影响范围", "今日抽取"} <= keys
    assert n.avatar_user_id == 20002  # 头像在
    assert "乙" in n.headline and "甲" in n.headline
    assert n.note


def test_divorce_notice_covers_done_and_failed():
    """离婚回执只剩两态：**已离婚**（双方昵称都带上）/ 失败原因在 headline。"""
    from nonebot_plugin_waifu import notices

    done = notices.divorce_notice(me_name="甲", partner_id=20002, partner_name="乙")
    assert done.title == "已离婚"
    assert done.tone == "ok"
    assert done.avatar_user_id == 20002
    assert "甲" in done.headline and "乙" in done.headline  # 双方昵称都在
    assert any("无需对方同意" in str(v) for _k, v in done.fields)

    failed = notices.divorce_notice(
        me_name="甲",
        partner_id=20002,
        partner_name="乙",
        failed="你还没娶老婆",
    )
    assert failed.title == "离婚没成功"
    assert failed.tone == "deny"
    assert failed.headline == "你还没娶老婆"


def test_usage_notice_is_how_to_not_an_error():
    """用法提示给**可照做的步骤**，并说明为什么是图（用户口径，逐字）。"""
    from nonebot_plugin_waifu import notices

    n = notices.usage_notice(
        cmd="娶老婆", lines=("娶老婆 @某人（可一次 @ 多人）", "或 娶老婆 QQ号")
    )
    assert n.title == "「娶老婆」怎么用"
    assert n.tone == "info"
    assert [v for _k, v in n.fields] == [
        "娶老婆 @某人（可一次 @ 多人）",
        "或 娶老婆 QQ号",
    ]
    assert n.note == "本插件的回复一律以图片呈现，避免在群里被刷掉"


def test_graph_empty_and_no_relations_notices_are_information_dense():
    from nonebot_plugin_waifu import notices

    empty = notices.graph_empty_notice(members=107)
    assert empty.title == "本群还没有老婆关系"
    assert any("107" in str(v) for _k, v in empty.fields)
    assert empty.note  # 下一步在

    focus = notices.no_relations_notice(focus_id=20002)
    assert focus.title == "ta 还没有任何老婆关系"
    assert focus.avatar_user_id == 20002  # 当事人头像在
    assert focus.note
