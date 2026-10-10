"""持久化与容灾：重启后状态仍在；监听钩子只收集不阻断。"""

from __future__ import annotations


def test_replay_reports_counts(tmp_store, monkeypatch):
    import nonebot_plugin_waifu as pkg
    from nonebot_plugin_waifu import store

    store.touch_member(group_id=100, user_id=1, nickname="甲")
    store.marry(group_id=100, a=1, b=2)
    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)

    summary = pkg.replay_from_store()
    assert summary["members"] == 1
    assert summary["married"] == 2


def test_replay_clears_stale_pending(tmp_store):
    import nonebot_plugin_waifu as pkg
    from nonebot_plugin_waifu import store

    store.set_pending(
        group_id=100, requester=1, partner=2, kind="marry", requested_at=1.0
    )
    summary = pkg.replay_from_store()
    assert summary["pending_cleared"] == 1
    assert store.get_pending(100, requester=1, partner=2, kind="marry") is None


def test_recording_does_not_block_other_plugins():
    """监听 matcher 必须 `block=False` —— 它兜住**所有**群消息，阻断会毁掉别的插件。"""
    import inspect

    import nonebot_plugin_waifu as pkg

    src = inspect.getsource(pkg)
    assert "block=False" in src


def test_watch_priority_is_above_command_priority():
    from nonebot_plugin_waifu.config import plugin_config

    assert plugin_config.waifu_match_priority > plugin_config.waifu_command_priority
