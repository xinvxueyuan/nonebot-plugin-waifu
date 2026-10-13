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
    ]
    for ctx in cases:
        html = render.build_notice_html(ctx)
        assert ctx.title in html or ctx.headline in html
        assert "http" not in html
        assert render.build_notice_fallback_text(ctx).strip()
