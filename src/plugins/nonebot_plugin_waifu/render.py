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

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import html as html_mod
import logging
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger("nonebot_plugin_waifu")

#: 卡片宽度；`MAX_WIDTH` 必须 ≥ 卡片宽 + padding（否则被压扁）。
CARD_WIDTH: Final[int] = 640
MAX_WIDTH: Final[int] = 720

#: 头像 CDN 基址与规格 —— **照 lingchu-bot 前端**
#: `apps/webui/server/qq.ts`（用户点名参照）：
#:   QQ_AVATAR_BASE = "https://q.qlogo.cn/headimg_dl"
#:   实际 URL = `${BASE}?dst_uin=${qq}&spec=${spec}&img_type=jpg`
#: spec 语义：1/2/3→40px，4→140px，5→640px（亦可传 100 等）。
_AVATAR_BASE: Final[str] = "https://q.qlogo.cn/headimg_dl"
_AVATAR_SPEC: Final[int] = 640

#: 头像缓存（进程内，key = URL 的 sha256；**只缓存成功**）
_AVATAR_CACHE: dict[str, str] = {}

#: 下载超时（秒）。**照 lingchu-bot 前端的 8000ms**（`handlers.ts:81`
#: `AbortSignal.timeout(8000)`）。取不到就占位，不能让命令卡住。
_AVATAR_TIMEOUT: Final[float] = 8.0

_SEX_LABELS: Final[dict[str, str]] = {
    "male": "男",
    "female": "女",
    "unknown": "未知",
}

#: 公共 CSS（抽取卡片 / 关系图 / 回执卡共用）：字体、卡片框、标题条、键值行只写一处，
#: 否则「换个字体要改三遍」。花括号转义写法（`{{` `}}`）与原来的 `_CSS` 一致。
_BASE_CSS: Final[str] = f"""
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
  .line {{ font-size: 20px; color: #334155; line-height: 32px; }}
  .key {{ font-size: 18px; color: #64748b; }}
  .val {{ font-size: 21px; color: #0f172a; }}
  .footer {{
    margin-top: 18px; padding-top: 12px; border-top: 1px dashed #f9a8d4;
    font-size: 17px; color: #9d174d; line-height: 26px;
  }}
"""

#: 抽取卡片私有部分（公共段见 `_BASE_CSS`）
_CSS: Final[str] = (
    _BASE_CSS
    + """
  .body { display: block; }
  .avatar {
    width: 120px; height: 120px; border-radius: 60px;
    border: 3px solid #fbcfe8; margin-right: 22px;
  }
  .ph {
    display: inline-block; width: 120px; height: 120px; border-radius: 60px;
    background: #fce7f3; color: #9d174d; font-size: 44px;
    text-align: center; line-height: 120px; margin-right: 22px;
  }
"""
)

_TEMPLATE: Final[str] = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body><div class="card">
  <div class="title">{title}</div>
  <div class="body">
    {avatar}
    <span class="line"><span class="key">昵称：</span>
      <span class="val">{nickname}</span></span>
  </div>
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
        f"QQ号：{ctx.user_id}",
        f"昵称：{ctx.nickname}",
        f"性别：{sex_label(ctx.sex)}",
        f"最后发言：{ctx.last_seen_text}",
    ]
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def avatar_url(user_id: int, *, spec: int = _AVATAR_SPEC) -> str:
    """QQ 头像直链（公共 CDN，无需登录）。签名与命名对齐前端 `getQQAvatarUrl`。"""
    return f"{_AVATAR_BASE}?dst_uin={int(user_id)}&spec={spec}&img_type=jpg"


def to_data_uri(data: bytes, *, mime: str = "image/jpeg") -> str:
    """把字节转成 data URI。

    ⚠️ `base64` 字样**必须**出现在逗号前的 header 里 —— htmlkit 的
    `data_scheme_img_fetcher` 靠它判断编码方式。
    """
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _avatar_cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


async def _download(url: str) -> tuple[bytes, str] | bytes:
    """下载头像，返回 `(bytes, content_type)`。

    **照 lingchu-bot 前端 `apps/webui/server/handlers.ts` 的做法**（用户点名参照）：
    服务端自己 fetch 上游 → 透传 body 与 `content-type`，8 秒超时。
    单独抽成函数是为了让测试能 monkeypatch 它，不必真发网络请求。

    ⚠️ `httpx` 必须**显式声明**在 `pyproject.toml` 的 `[project] dependencies` 里：
    它**不是** nonebot2 的传递依赖（实测 nonebot2 把 httpx 放在 `extra == "httpx"`
    下，onebot 适配器只依赖 msgpack/nonebot2/pydantic/typing-extensions）。缺了它，
    干净克隆 `uv sync` 后 `import httpx` 会抛 ModuleNotFoundError，被
    `fetch_avatar_data_uri` 捕获 → 头像**静默降级成占位块**（只一行 warning）。
    """
    import httpx

    async with httpx.AsyncClient(timeout=_AVATAR_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "image/jpeg").split(";")[0]
        return resp.content, ctype.strip() or "image/jpeg"


async def fetch_avatar_data_uri(user_id: int) -> str | None:
    """取头像并转 data URI；**任何失败返回 None**（调用方渲染占位块）。

    - 成功**写缓存**（同一人多次抽取不必反复下载；对齐前端的
      `cache-control: public, max-age=86400` 思路）。
    - 失败**不写负缓存** —— 否则一次抖动会让该头像永久缺失。

    `_download` 约定返回 `(bytes, content_type)`；但也容忍**裸 bytes**
    （老式替身/调用方只给 body），此时 mime 用默认 `image/jpeg`。
    """
    url = avatar_url(user_id)
    key = _avatar_cache_key(url)
    if key in _AVATAR_CACHE:
        return _AVATAR_CACHE[key]
    try:
        got = await _download(url)
        data, ctype = got if isinstance(got, tuple) else (got, "image/jpeg")
    except Exception as e:
        logger.warning("取头像失败 user=%s: %s: %s", user_id, type(e).__name__, e)
        return None
    if not data:
        logger.warning("取头像得到空 body user=%s（当失败处理）", user_id)
        return None
    uri = to_data_uri(data, mime=ctype)
    _AVATAR_CACHE[key] = uri
    return uri


# ---------------------------------------------------------------------------
# 回执卡（Notice）：所有面向群里的回复都用它 —— 与抽取卡片同款外观
# ---------------------------------------------------------------------------

#: 语气 → （主色，徽标文字）
_NOTICE_TONES: Final[dict[str, tuple[str, str]]] = {
    "info": ("#db2777", "提示"),
    "ok": ("#16a34a", "完成"),
    "warn": ("#f59e0b", "注意"),
    "deny": ("#dc2626", "未生效"),
}


@dataclass(frozen=True)
class NoticeItem:
    """回执里「按人头」的一行（娶老婆可一次 @ 多人 / 名单类信息）。"""

    user_id: int
    line: str


@dataclass(frozen=True)
class NoticeContext:
    """一条回执的全部内容（**信息密集**：标题 + 当事人 + 字段 + 名单 + 下一步）。"""

    title: str
    tone: str = "info"  # info | ok | warn | deny
    headline: str = ""
    avatar_user_id: int | None = None
    fields: tuple[tuple[str, str], ...] = ()
    items: tuple[NoticeItem, ...] = ()
    note: str = ""


def notice_user_ids(ctx: NoticeContext) -> tuple[int, ...]:
    """要取头像的人（主图 + 名单），保序去重。"""
    ids: list[int] = []
    if ctx.avatar_user_id is not None:
        ids.append(int(ctx.avatar_user_id))
    ids.extend(int(it.user_id) for it in ctx.items)
    return tuple(dict.fromkeys(ids))


_NOTICE_TEMPLATE: Final[str] = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body><div class="card">
  <div class="title" data-tone="{tone}">{title}<span class="badge">{badge}</span></div>
  <div class="head">{head}</div>
  {avatar}
  <div class="rows">{rows}</div>
  {items}
  <div class="footer">{note}</div>
</div></body></html>"""


def _mini_avatar(uid: int, pool: dict[int, str | None]) -> str:
    uri = pool.get(uid)
    if uri:
        return f'<img class="mini" src="{html_mod.escape(uri)}">'
    return f'<span class="ph-mini">{html_mod.escape(str(uid)[:1])}</span>'


def build_notice_html(
    ctx: NoticeContext, *, avatars: dict[int, str | None] | None = None
) -> str:
    """拼回执卡 HTML（**纯函数**，转义都在这里做）。"""
    pool = avatars or {}
    color, badge = _NOTICE_TONES.get(ctx.tone, _NOTICE_TONES["info"])
    avatar = ""
    if ctx.avatar_user_id is not None:
        uid = int(ctx.avatar_user_id)
        uri = pool.get(uid)
        avatar = (
            f'<img class="avatar" src="{html_mod.escape(uri)}">'
            if uri
            else f'<span class="ph">{html_mod.escape(str(uid)[:1])}</span>'
        )
    rows = "".join(
        f'<div class="line"><span class="key">{html_mod.escape(str(k))}：</span>'
        f'<span class="val">{html_mod.escape(str(v))}</span></div>'
        for k, v in ctx.fields
    )
    items = "".join(
        f'<div class="item">{_mini_avatar(int(it.user_id), pool)}'
        f'<span class="val">{html_mod.escape(it.line)}</span></div>'
        for it in ctx.items
    )
    # `_NOTICE_CSS` 已是成品 CSS（花括号全是单个的）：语气色用 replace 注入，
    # **不能**用 `.format()` —— 它会把 CSS 自身的花括号当占位符直接报错。
    return _NOTICE_TEMPLATE.format(
        css=_NOTICE_CSS.replace("{color}", color),
        tone=html_mod.escape(ctx.tone),
        badge=html_mod.escape(badge),
        title=html_mod.escape(ctx.title),
        head=html_mod.escape(ctx.headline),
        avatar=avatar,
        rows=rows,
        items=items,
        note=html_mod.escape(ctx.note),
    )


def build_notice_fallback_text(ctx: NoticeContext) -> str:
    """回执的纯文本等价物（**字段与图一一对应**，无 `@`）。"""
    lines = [ctx.title]
    if ctx.headline:
        lines.append(ctx.headline)
    lines.extend(f"{k}：{v}" for k, v in ctx.fields)
    lines.extend(f"{it.user_id}：{it.line}" for it in ctx.items)
    if ctx.note:
        lines.append(ctx.note)
    return "\n".join(lines)


async def render_notice(
    ctx: NoticeContext, *, avatars: dict[int, str | None] | None = None
) -> bytes | None:
    """渲染回执卡 PNG。**任何失败都只 warning 并返回 None**（调用方回退等价文本）。"""
    try:
        html_to_pic = _load_htmlkit()
        return await html_to_pic(
            build_notice_html(ctx, avatars=avatars), max_width=MAX_WIDTH
        )
    except Exception as e:
        logger.warning("回执卡渲染失败，回退纯文本: %s: %s", type(e).__name__, e)
        return None


async def fetch_avatars(ids: Sequence[int], *, limit: int = 4) -> dict[int, str | None]:
    """并发取多人头像（复用 `fetch_avatar_data_uri` 的进程内缓存；失败 → None）。"""
    sem = asyncio.Semaphore(max(1, int(limit)))

    async def one(uid: int) -> tuple[int, str | None]:
        async with sem:
            return uid, await fetch_avatar_data_uri(uid)

    return dict(await asyncio.gather(*(one(u) for u in dict.fromkeys(ids))))


#: 回执卡私有部分（公共段见 `_BASE_CSS`）；`{color}` 由语气色注入（build_notice_html）。
_NOTICE_CSS: Final[str] = (
    _BASE_CSS
    + """
  .title .badge { font-size: 15px; color: #ffffff; background: {color};
                  border-radius: 10px; padding: 2px 10px; margin-left: 10px; }
  .head { font-size: 21px; color: #0f172a; line-height: 30px; margin-bottom: 10px; }
  .avatar { width: 96px; height: 96px; border-radius: 48px; border: 3px solid #fbcfe8;
            margin-bottom: 10px; }
  .ph { display: inline-block; width: 96px; height: 96px; border-radius: 48px;
        background: #fce7f3; color: #9d174d; font-size: 40px; text-align: center;
        line-height: 96px; margin-bottom: 10px; }
  .rows { display: block; }
  .item { margin-top: 8px; }
  .mini { width: 36px; height: 36px; border-radius: 18px; border: 2px solid #fbcfe8;
          margin-right: 8px; }
  .ph-mini { display: inline-block; width: 36px; height: 36px; border-radius: 18px;
             background: #fce7f3; color: #9d174d; font-size: 18px; text-align: center;
             line-height: 36px; margin-right: 8px; }
"""
)


__all__ = [
    "CARD_WIDTH",
    "MAX_WIDTH",
    "CardContext",
    "NoticeContext",
    "NoticeItem",
    "avatar_url",
    "build_fallback_text",
    "build_html",
    "build_notice_fallback_text",
    "build_notice_html",
    "fetch_avatar_data_uri",
    "fetch_avatars",
    "format_last_seen",
    "notice_user_ids",
    "render_card",
    "render_notice",
    "sex_label",
    "to_data_uri",
]
