"""rules 纯函数：日期边界、配额。全部不碰 IO。"""

from __future__ import annotations

from datetime import datetime, timezone


def test_date_key_uses_configured_timezone():
    """跨天判定必须按配置时区（默认 Asia/Shanghai），不是 UTC。"""
    from nonebot_plugin_waifu import rules

    # 2026-10-10 00:30 +08:00 == 2026-10-09 16:30 UTC
    ts = datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc).timestamp() - 8 * 3600
    assert rules.date_key(ts, tz_name="Asia/Shanghai") == "2026-10-10"
    assert rules.date_key(ts, tz_name="UTC") == "2026-10-09"


def test_quota_allows_under_limit():
    from nonebot_plugin_waifu import rules

    assert rules.can_draw(draws_used=0, daily_limit=20).ok
    assert rules.can_draw(draws_used=19, daily_limit=20).ok


def test_quota_blocks_at_limit_with_exact_message():
    """文案是用户明确指定的，逐字断言。"""
    from nonebot_plugin_waifu import rules

    r = rules.can_draw(draws_used=20, daily_limit=20)
    assert not r.ok
    assert r.reason == "quota"
    assert r.message == "渣男，你今天没有老婆了！"


def test_normalize_sex():
    from nonebot_plugin_waifu import rules

    assert rules.normalize_sex("female") == "female"
    assert rules.normalize_sex("FEMALE") == "female"
    assert rules.normalize_sex("男") == "male"
    assert rules.normalize_sex(None) == "unknown"
    assert rules.normalize_sex("whatever") == "unknown"


def test_pick_target_no_longer_excludes_married():
    """已婚者仍在抽取池里（用户 2026-10-14：结婚后双方仍可正常每日抽取老婆）。"""
    from nonebot_plugin_waifu import rules

    pool = [
        rules.Candidate(user_id=1, last_seen=1.0),
        rules.Candidate(user_id=2, last_seen=2.0),
    ]
    got = rules.pick_target(
        pool,
        me=9,
        my_sex=None,
        mode="all",
        excluded=set(),
        rng=lambda cands: cands[0],
    )
    assert got is not None and got.user_id == 1


def test_pick_target_excludes_self_and_locked():
    """**只排除**：自己 / 今日已锁定者（已婚者不再排除）；性别**不参与**筛选。"""
    from nonebot_plugin_waifu import rules

    pool = [
        rules.Candidate(user_id=1, last_seen=100.0),
        rules.Candidate(user_id=2, last_seen=90.0),
        rules.Candidate(user_id=3, last_seen=80.0),
        rules.Candidate(user_id=4, last_seen=70.0),
    ]
    got = rules.pick_target(
        pool,
        me=1,
        my_sex="unknown",  # mode=all 下不影响；显式写出以免被取向干扰
        mode="all",
        excluded={2},  # 今日已被锁定
        rng=lambda seq: seq[0],  # 注入 rng：一定挑第一个候选
    )
    assert got is not None
    assert got.user_id == 3


def test_pick_target_has_orientation_inputs():
    """取向三档筛**参与**抽取（用户 2026-10-10 拍板：正常/所有/相反）。

    结构性断言：`pick_target` 必须收 `my_sex` 与 `mode`，`Candidate` 必须有 `sex`。
    若有人删掉这些参数，说明筛选被悄悄取消了 —— 这里会红。
    """
    import dataclasses
    import inspect

    from nonebot_plugin_waifu import rules

    params = set(inspect.signature(rules.pick_target).parameters)
    assert {"my_sex", "mode"} <= params, params

    fields = {f.name for f in dataclasses.fields(rules.Candidate)}
    assert "sex" in fields, fields


def test_orientation_all_means_no_filtering():
    """**默认档「所有」= 不筛**（用户口径：默认 any / 所有，男女都可抽）。"""
    from nonebot_plugin_waifu import rules

    # 同性、异性、未知 —— 全都要通过
    for cand in ("male", "female", "unknown", None):
        assert rules.is_eligible_gender("male", cand, mode="all")


def test_orientation_normal_takes_opposite_sex():
    """「正常」= 只抽异性。"""
    from nonebot_plugin_waifu import rules

    assert rules.is_eligible_gender("male", "female", mode="normal")
    assert rules.is_eligible_gender("female", "male", mode="normal")
    assert not rules.is_eligible_gender("male", "male", mode="normal")
    assert not rules.is_eligible_gender("female", "female", mode="normal")


def test_orientation_reverse_takes_same_sex():
    """「相反」= 只抽同性。"""
    from nonebot_plugin_waifu import rules

    assert rules.is_eligible_gender("male", "male", mode="reverse")
    assert rules.is_eligible_gender("female", "female", mode="reverse")
    assert not rules.is_eligible_gender("male", "female", mode="reverse")


def test_hidden_gender_always_passes_in_every_mode():
    """**硬规则**：群员性别隐藏/取不到的一律判为通过（用户原话）。

    这条在三个档下都要成立 —— 不能因为查不到性别就把人静默排除
    （失败是静默的：群里只会看到「还没有能抽的人」，永远查不到原因）。
    """
    from nonebot_plugin_waifu import rules

    for mode in ("normal", "all", "reverse"):
        # 候选性别未知 / None / 空串
        assert rules.is_eligible_gender("male", "unknown", mode=mode)
        assert rules.is_eligible_gender("male", None, mode=mode)
        assert rules.is_eligible_gender("male", "", mode=mode)
        # **我自己**的性别未知
        assert rules.is_eligible_gender("unknown", "male", mode=mode)
        assert rules.is_eligible_gender(None, "female", mode=mode)
        # 双方都未知
        assert rules.is_eligible_gender(None, None, mode=mode)


def test_unknown_mode_value_does_not_filter():
    """`mode` 传了没见过的值 → 不过滤（宁可放过，不要静默卡死）。"""
    from nonebot_plugin_waifu import rules

    assert rules.is_eligible_gender("male", "male", mode="whatever")


def test_pick_target_filters_by_orientation():
    """端到端：`normal` 档下同性候选被筛掉。"""
    from nonebot_plugin_waifu import rules

    pool = [
        rules.Candidate(user_id=2, last_seen=1.0, sex="male"),
        rules.Candidate(user_id=3, last_seen=1.0, sex="female"),
    ]
    got = rules.pick_target(pool, me=1, my_sex="male", mode="normal", excluded=set())
    assert got is not None and got.user_id == 3


def test_pick_target_keeps_unknown_sex_candidates():
    """端到端：候选性别未知时即使在 `normal` 档也不会被筛掉。"""
    from nonebot_plugin_waifu import rules

    pool = [rules.Candidate(user_id=2, last_seen=1.0, sex="unknown")]
    got = rules.pick_target(pool, me=1, my_sex="male", mode="normal", excluded=set())
    assert got is not None and got.user_id == 2


def test_pick_target_returns_none_on_empty_pool():
    from nonebot_plugin_waifu import rules

    assert (
        rules.pick_target([], me=1, my_sex="unknown", mode="all", excluded=set())
        is None
    )


def test_pick_target_never_returns_me():
    from nonebot_plugin_waifu import rules

    pool = [rules.Candidate(user_id=1, last_seen=1.0)]
    assert (
        rules.pick_target(pool, me=1, my_sex="unknown", mode="all", excluded=set())
        is None
    )


def test_pick_target_survives_all_excluded():
    """全被排除时返回 None（不抛）—— 只锁自己时也不该崩。"""
    from nonebot_plugin_waifu import rules

    pool = [rules.Candidate(user_id=2, last_seen=1.0)]
    kw = {"my_sex": "unknown", "mode": "all"}
    assert rules.pick_target(pool, me=1, excluded={2}, **kw) is None


def test_pending_window_is_three_minutes():
    from nonebot_plugin_waifu import rules

    assert rules.PENDING_WINDOW_SECONDS == 180
    assert rules.pending_alive(requested_at=0.0, now=179.0)
    assert not rules.pending_alive(requested_at=0.0, now=181.0)


def test_config_defaults_are_the_confirmed_values():
    """默认值必须等于与用户确认的口径（改这里前先回去问）。"""
    from nonebot_plugin_waifu.config import Config

    c = Config()
    assert c.waifu_daily_limit == 20  # 一天最多 20 次
    assert c.waifu_pending_minutes == 3  # 娶老婆/离婚确认窗口 3 分钟
    assert c.waifu_orientation == "all"  # 用户口径：默认「所有」= 不限
    assert c.waifu_match_priority > c.waifu_command_priority
    assert c.waifu_show_quota_in_card is True
    assert c.waifu_change_cmd == "换老婆"  # 源码默认；生产覆盖成「换一只」


def test_config_change_cmd_is_configurable():
    """「换老婆」命令词必须可配（用户 2026-10-10：生产用「换一只」）。"""
    from nonebot_plugin_waifu.config import DEFAULT_CHANGE_CMD, Config

    assert DEFAULT_CHANGE_CMD == "换老婆"
    assert Config(waifu_change_cmd="换一只").waifu_change_cmd == "换一只"
    assert Config().waifu_change_cmd == "换老婆"


def test_config_accepts_chinese_orientation():
    """`.env` 里写中文**不能让插件起不来**（用户就是用中文表达这三档的）。"""
    from nonebot_plugin_waifu.config import Config

    assert Config(waifu_orientation="正常").waifu_orientation == "normal"
    assert Config(waifu_orientation="所有").waifu_orientation == "all"
    assert Config(waifu_orientation="相反").waifu_orientation == "reverse"


def test_config_accepts_english_and_case_insensitive():
    from nonebot_plugin_waifu.config import Config

    assert Config(waifu_orientation="NORMAL").waifu_orientation == "normal"
    assert Config(waifu_orientation="Reverse").waifu_orientation == "reverse"


def test_config_rejects_unknown_orientation():
    """真的乱写仍要报错（不能静默吞掉）。"""
    import pydantic
    import pytest

    from nonebot_plugin_waifu.config import Config

    with pytest.raises(pydantic.ValidationError):
        Config(waifu_orientation="whatever")
