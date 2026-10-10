"""结果图渲染（nonebot-plugin-htmlkit，**可选依赖**）。

## 三条必须遵守的约定

1. **失败一律回退文本，绝不抛**：渲染是锦上添花，渲染不通不能表现成「命令没反应」。
   `render_card()` 返回 `None` 就由调用方发纯文本。
2. **所有用户输入必须 `html.escape`**（昵称是用户随手写的）。
3. **别用浏览器语义写 CSS**：htmlkit 用的是自带渲染器（litehtml），**不是 Chromium**。
   实测：`dpi` 无效、`max_width` 是**上限而非缩放器**（内容更宽会被压扁）、flex 的 `gap`
   **不生效**。宽度/字号/间距全部显式写死，间距用 `margin`。

## 头像为什么要转 data URI

htmlkit 的 `combined_img_fetcher` 里 `network_img_fetcher` **不带任何自定义请求头**，
且**失败是静默的**（只记 warning，不抛给调用方）。而 `data_scheme_img_fetcher` 是第一步
尝试、原生解码 —— 所以头像由插件自己下载好（带 Referer 等头）再转 base64 塞进模板，
既绕开取图限制，也不依赖宿主装了哪个 HTTP 驱动。
"""

from __future__ import annotations

from dataclasses import dataclass
import html as html_mod
import logging
from typing import Any, Final

logger = logging.getLogger("nonebot_plugin_waifu")

#: 卡片宽度；`MAX_WIDTH` 必须 ≥ 卡片宽 + padding（否则被压扁）。
CARD_WIDTH: Final[int] = 640
MAX_WIDTH: Final[int] = 720

_SEX_LABELS: Final[dict[str, str]] = {
    "male": "男",
    "female": "女",
    "unknown": "未知",
}

_CSS: Final[str] = f"""
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 16px; background: #fdf2f8;
    /* ⚠️ 必须显式声明 CJK 字体：宿主默认 sans-serif 不含中文字形时整张图是方块。 */
    font-family: "WenQuanYi Zen Hei", "Noto Sans CJK SC", "Microsoft YaHei",
                 "PingFang SC", sans-serif;
  }}
  .card {{
    width: {CARD_WIDTH}px; background: #ffffff; border-radius: 14px;
    padding: 22px 24px; border: 1px solid #fbcfe8;
  }}
  .title {{
    font-size: 26px; font-weight: bold; color: #831843;
    padding-left: 12px; border-left: 6px solid #db2777; margin-bottom: 18px;
  }}
  .body {{ display: block; }}
  .avatar {{
    width: 120px; height: 120px; border-radius: 60px;
    border: 3px solid #fbcfe8; margin-right: 22px;
  }}
  .ph {{
    display: inline-block; width: 120px; height: 120px; border-radius: 60px;
    background: #fce7f3; color: #9d174d; font-size: 44px;
    text-align: center; line-height: 120px; margin-right: 22px;
  }}
  .line {{ font-size: 20px; color: #334155; line-height: 32px; }}
  .key {{ font-size: 18px; color: #64748b; }}
  .val {{ font-size: 21px; color: #0f172a; }}
  .footer {{
    margin-top: 18px; padding-top: 12px; border-top: 1px dashed #f9a8d4;
    font-size: 17px; color: #9d174d; line-height: 26px;
  }}
"""

_TEMPLATE: Final[str] = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body><div class="card">
  <div class="title">{title}</div>
  <div class="body">
    {avatar}
    <span class="line"><span class="key">昵称：</span>
      <span class="val">{nickname}</span></span>
  </div>
  <div class="line"><span class="key">@：</span>
    <span class="val">{mention}</span></div>
  <div class="line"><span class="key">QQ号：</span>
    <span class="val">{user_id}</span></div>
  <div class="line"><span class="key">性别：</span>
    <span class="val" data-sex="{sex_code}">{sex}</span></div>
  <div class="line"><span class="key">最后发言：</span>
    <span class="val">{last_seen}</span></div>
  {footer}
</div></body></html>"""


@dataclass(frozen=True)
class CardContext:
    """卡片要展示的全部字段（**用户要求的六项一个不少**）。"""

    title: str
    mention: str
    user_id: int
    nickname: str
    sex: str
    last_seen_text: str
    avatar_data_uri: str | None = None


def sex_label(sex: str | None) -> str:
    """性别显示名。"""
    return _SEX_LABELS.get(str(sex or "unknown").casefold(), "未知")


def format_last_seen(ts: float | None, *, tz_name: str = "Asia/Shanghai") -> str:
    """最后发言时间 → `YYYY-MM-DD HH:MM`；取不到给「未知」。

    ⚠️ 用配置时区而不是 `datetime.fromtimestamp` 的本地时区：服务器时区一变
    展示就会整体偏移 8 小时（这类偏差只能靠事后复盘发现）。
    """
    if ts is None:
        return "未知"
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:
        from datetime import timezone

        tz = timezone.utc
    from datetime import datetime

    try:
        return datetime.fromtimestamp(float(ts), tz=tz).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "未知"


def build_html(ctx: CardContext, *, footer: str | None = None) -> str:
    """拼 HTML（**纯函数**，可单测；转义都在这里做）。

    抽成纯函数是为了能在不装 htmlkit 的环境里断言「转义做了没」「必含字段在不在」——
    这两点靠肉眼看图是验证不了的。
    """
    if ctx.avatar_data_uri:
        avatar = f'<img class="avatar" src="{html_mod.escape(ctx.avatar_data_uri)}">'
    else:
        # 取头像失败 → 占位块。绝不把 http URL 直接塞进模板
        # （network fetcher 会静默失败）。
        first = html_mod.escape((ctx.nickname or "?")[:1])
        avatar = f'<span class="ph">{first}</span>'

    footer_html = (
        f'<div class="footer">{html_mod.escape(footer)}</div>' if footer else ""
    )
    return _TEMPLATE.format(
        css=_CSS,
        title=html_mod.escape(ctx.title),
        avatar=avatar,
        mention=html_mod.escape(ctx.mention),
        user_id=ctx.user_id,
        nickname=html_mod.escape(ctx.nickname),
        sex=html_mod.escape(sex_label(ctx.sex)),
        sex_code=html_mod.escape(str(ctx.sex or "unknown").casefold()),
        last_seen=html_mod.escape(ctx.last_seen_text),
        footer=footer_html,
    )


def _load_htmlkit() -> Any:
    """惰性取 `html_to_pic`（**单独抽出来**，测试可 monkeypatch，不必真装 htmlkit）。"""
    from nonebot_plugin_htmlkit import html_to_pic  # pyright: ignore[reportMissingImports]

    return html_to_pic


async def render_card(ctx: CardContext, *, footer: str | None = None) -> bytes | None:
    """渲染成 PNG。**任何失败都只 warning 并返回 None**（调用方回退纯文本）。"""
    try:
        html_to_pic = _load_htmlkit()
        return await html_to_pic(build_html(ctx, footer=footer), max_width=MAX_WIDTH)
    except Exception as e:
        logger.warning("老婆卡片渲染失败，回退纯文本: %s: %s", type(e).__name__, e)
        return None


def build_fallback_text(ctx: CardContext, *, footer: str | None = None) -> str:
    """渲染不可用时的纯文本等价物（**字段与图一致**）。"""
    lines = [
        ctx.title,
        f"@{ctx.mention}",
        f"QQ号：{ctx.user_id}",
        f"昵称：{ctx.nickname}",
        f"性别：{sex_label(ctx.sex)}",
        f"最后发言：{ctx.last_seen_text}",
    ]
    if footer:
        lines.append(footer)
    return "\n".join(lines)


__all__ = [
    "CARD_WIDTH",
    "MAX_WIDTH",
    "CardContext",
    "build_fallback_text",
    "build_html",
    "format_last_seen",
    "render_card",
    "sex_label",
]
