"""娶老婆/离婚：双向确认、窗口、绑定后的互斥。"""

from __future__ import annotations

from test_draw_flow import GROUP, ME, OTHER


def test_first_marry_request_creates_pending():
    """第一次「娶老婆 @B」只**创建请求**，不绑定。"""
    from nonebot_plugin_waifu import handle, store

    out = handle.apply_marry(group_id=GROUP, requester=ME, partner=OTHER, now=100.0)
    assert out.bound is False
    pending = store.get_pending(GROUP, requester=ME, partner=OTHER, kind="marry")
    assert pending is not None


def test_reciprocal_marry_binds():
    """B 回发「娶老婆 @A」→ 绑定成功。"""
    from nonebot_plugin_waifu import handle, store

    handle.apply_marry(group_id=GROUP, requester=ME, partner=OTHER, now=100.0)
    out = handle.apply_marry(group_id=GROUP, requester=OTHER, partner=ME, now=110.0)
    assert out.bound is True
    assert store.get_partner(GROUP, ME) == OTHER
    assert store.get_partner(GROUP, OTHER) == ME


def test_expired_window_does_not_bind():
    """**窗口外视为失效** —— 超过 3 分钟回复不算确认。"""
    from nonebot_plugin_waifu import handle, store

    handle.apply_marry(group_id=GROUP, requester=ME, partner=OTHER, now=0.0)
    out = handle.apply_marry(group_id=GROUP, requester=OTHER, partner=ME, now=181.0)
    assert out.bound is False
    assert store.get_partner(GROUP, ME) is None


def test_marry_self_rejected():
    from nonebot_plugin_waifu import handle

    out = handle.apply_marry(group_id=GROUP, requester=ME, partner=ME, now=1.0)
    assert out.error


def test_marry_blocks_when_already_married():
    from nonebot_plugin_waifu import handle, store

    store.marry(group_id=GROUP, a=ME, b=OTHER)
    out = handle.apply_marry(group_id=GROUP, requester=ME, partner=30003, now=1.0)
    assert out.error


def test_marry_marks_both_as_married():
    """结婚后双方都是「已婚」状态（**不再影响抽取** —— 用户 2026-10-14）。"""
    from nonebot_plugin_waifu import store

    store.marry(group_id=GROUP, a=ME, b=OTHER)
    assert store.is_married(GROUP, ME)
    assert store.is_married(GROUP, OTHER)


def test_binding_clears_pending():
    from nonebot_plugin_waifu import handle, store

    handle.apply_marry(group_id=GROUP, requester=ME, partner=OTHER, now=1.0)
    handle.apply_marry(group_id=GROUP, requester=OTHER, partner=ME, now=2.0)
    assert store.get_pending(GROUP, requester=ME, partner=OTHER, kind="marry") is None
