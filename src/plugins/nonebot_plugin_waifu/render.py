"""结果图渲染（**Chromium**：nonebot-plugin-htmlrender，可选依赖）。

## 三条必须遵守的约定

1. **失败一律回退文本，绝不抛**：渲染是锦上添花，渲染不通不能表现成「命令没反应」。
   `render_card()` 返回 `None` 就由调用方发纯文本。
2. **所有用户输入必须 `html.escape`**（昵称是用户随手写的）。
3. **渲染器是 Chromium**（用户 2026-10-14：从 litehtml/htmlkit **改用 Chromium**）——
   `transform` / flex / gap 这些浏览器语义**可以用了**：标签沿线条旋转就是靠
   `transform:rotate()`（litehtml 实测不支持，会静默不转）。
   ⚠️ 视口宽度靠 `viewport={"width": ...}` 写死，`full_page=True` 截整页。

## 头像为什么要转 data URI

渲染在 Chromium 里跑，图片本可以直接给 URL；但**头像仍走 data URI**：插件自己下载
（带 Referer 等头）再转 base64，渲染就不会因为网络抖动而缺图，也不依赖宿主的 HTTP 驱动。
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import html as html_mod
import logging
import pathlib
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .layout import ClusterLayout
    from .relations import RelationGraph

from . import graph_g6

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
  /* 头像与右侧「昵称」行**垂直居中**：inline 的 img 默认按基线对齐，
     文字会掉到头像下缘（实测偏 9px）。 */
  .body { display: flex; align-items: center; }
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


def _load_renderer() -> Any:
    """惰性取渲染器 `html_to_pic`（**Chromium**：`nonebot-plugin-htmlrender`）。

    单独抽出来是为了测试能 monkeypatch，不必真装 playwright + 浏览器。
    """
    from nonebot_plugin_htmlrender import html_to_pic  # pyright: ignore[reportMissingImports]

    return html_to_pic


def _load_page_ctx() -> Any:
    """惰性取 htmlrender 的页面上下文管理器 `get_new_page`（**等待条件**要用它）。

    `html_to_pic` 没有「等页面条件成立再截图」的钩子（只有固定 `wait` 秒数），
    而 G6 是异步渲染 —— 不等就会截到空图，所以带 `wait_for` 的出图走自己拿 page 的路。
    """
    from nonebot_plugin_htmlrender.browser import (  # pyright: ignore[reportMissingImports]
        get_new_page,
    )

    return get_new_page


#: `wait_for` 的超时（毫秒）：到点就抛 → `render_graph` 回退纯文本（绝不发空图）。
READY_TIMEOUT_MS: Final[int] = 15_000


async def _shot_after(html: str, *, width: int, height: int, wait_for: str) -> bytes:
    """等 `wait_for` 条件成立后再截图（G6 异步渲染的防「空图」关键）。

    与 `html_to_pic` 同一条管线（`goto` 一个 file:// 起点 → `set_content` →
    整页截图、`device_scale_factor=1`），只多一步 `page.wait_for_function`。
    """
    get_new_page = _load_page_ctx()
    async with get_new_page(1, viewport={"width": width, "height": height}) as page:
        await page.goto(pathlib.Path.cwd().as_uri())
        await page.set_content(html, wait_until="networkidle")
        await page.wait_for_function(wait_for, timeout=READY_TIMEOUT_MS)
        return await page.screenshot(full_page=True, type="png")


async def _shot(
    html: str, *, width: int, height: int = 100, wait_for: str | None = None
) -> bytes:
    """HTML → PNG：视口宽度写死，高度按整页长（`full_page=True`）。

    `device_scale_factor=1`：与旧 litehtml 的 1:1 像素输出对齐（不把图放大一倍）。
    `wait_for`：给「内容不是同步画好」的图用（关系图 = G6 在 JS 里异步渲染）——
    传了就走 `page.wait_for_function` 的那条路，不传就是原来的 `html_to_pic`。
    """
    if wait_for is not None:
        return await _shot_after(html, width=width, height=height, wait_for=wait_for)
    html_to_pic = _load_renderer()
    return await html_to_pic(
        html,
        viewport={"width": width, "height": height},
        full_page=True,
        device_scale_factor=1,
    )


async def render_card(ctx: CardContext, *, footer: str | None = None) -> bytes | None:
    """渲染成 PNG。**任何失败都只 warning 并返回 None**（调用方回退纯文本）。"""
    try:
        return await _shot(build_html(ctx, footer=footer), width=MAX_WIDTH)
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
        return await _shot(build_notice_html(ctx, avatars=avatars), width=MAX_WIDTH)
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
  /* 状态胶囊：**与标题文字垂直居中**。inline span 默认按基线对齐，实测胶囊中心
     比标题文字中心低 4.5px；标题行改 flex + `align-items:center` 后偏移为 0。 */
  .title { display: flex; align-items: center; gap: 10px; }
  .title .badge { font-size: 15px; line-height: 1; color: #ffffff;
                  background: {color}; border-radius: 10px; padding: 5px 10px; }
  .head { font-size: 21px; color: #0f172a; line-height: 30px; margin-bottom: 10px; }
  .avatar { width: 96px; height: 96px; border-radius: 48px; border: 3px solid #fbcfe8;
            margin-bottom: 10px; }
  .ph { display: inline-block; width: 96px; height: 96px; border-radius: 48px;
        background: #fce7f3; color: #9d174d; font-size: 40px; text-align: center;
        line-height: 96px; margin-bottom: 10px; }
  .rows { display: block; }
  /* 名单行：小人头 + 文字同样居中（否则小人头骑在文字基线上，偏 1.5px）。 */
  .item { display: flex; align-items: center; margin-top: 8px; }
  .mini { width: 36px; height: 36px; border-radius: 18px; border: 2px solid #fbcfe8;
          margin-right: 8px; }
  .ph-mini { display: inline-block; width: 36px; height: 36px; border-radius: 18px;
             background: #fce7f3; color: #9d174d; font-size: 18px; text-align: center;
             line-height: 36px; margin-right: 8px; }
"""
)


# ---------------------------------------------------------------------------
# 关系图：**G6 v4 组件库**（AntV）× Chromium 出图
#
# 2026-10-15 用户拍板：关系图改用组件库画（「去找相关的组件库工具来实现」——
# 王者荣耀英雄关系页那种成熟形态），旧 HTML div + `linear-gradient` 色带绘制退役。
# 绘制细节（头像节点 / 端点圆 / 白底胶囊关系名 / 隐藏锚点钉坐标）全在
# `graph_g6.py` 里，连同「为什么这么做」的实测结论。
# 本文件只负责：卡片外框 + 簇的排布（一簇一个 G6 容器）+ 图例 + 纯文本回退。
# ---------------------------------------------------------------------------

#: 关系图：边的配色（夫妻 = 主题粉实线 / 倾心 = 琥珀实线 / 喜欢 = 蓝色实线）
_EDGE_COLOR: Final[dict[str, str]] = {
    "marriage": "#db2777",  # 夫妻：主题粉、粗、无向（没有端口圆）
    "crush": "#f59e0b",  # 倾心：琥珀、细、指向被倾心的人（端口圆表方向）
    "like": "#0ea5e9",  # 喜欢：蓝色、细、指向被喜欢的人（与倾心不撞色）
}

#: 边的主线段宽度（px）：夫妻最粗（4）> 倾心/喜欢（3）—— 用户实拍：
#: 「连线几乎看不见」（旧值统一 3px 且虚线被切成 5 段，每段只有几个像素）。
_EDGE_PX: Final[dict[str, int]] = {"marriage": 4, "crush": 3, "like": 3}

#: 关系图卡片宽：**比其它卡片宽** —— 2 成员簇要两列并排，而每簇是
#: `layout.PAIR_SPAN(260) + 节点盒(96) + 两侧 PAD(24*2) = 404px`，两簇 808px；
#: 普通卡片（640）的内容宽只有 590px，放不下两列。
GRAPH_CARD_WIDTH: Final[int] = 880
#: 卡片宽度上限：人多的大簇会自动分多环（`layout._ring_plan`，最外环半径 616），
#: 卡片按「最宽的簇 + 卡片内边距」跟着加宽，但不超过这个值
#: （再宽手机上就缩到看不清了）。
GRAPH_CARD_WIDTH_MAX: Final[int] = 1440
#: 卡片左右内边距（见 `_BASE_CSS` 的 `.card{padding: 22px 24px}`）——算卡片宽要用。
_CARD_PAD_X: Final[int] = 24
#: 关系图的渲染上限宽（**基准**）：必须 ≥ 卡片宽 + body padding（16*2），
#: 否则内容被压扁。
#: 大簇时的实际视口宽看 `_graph_viewport_width`（基准 880 + 40 = 920 就是它）。
GRAPH_MAX_WIDTH: Final[int] = 920


def _graph_card_width(clusters: Sequence[ClusterLayout]) -> int:
    """关系图卡片宽：默认 `GRAPH_CARD_WIDTH`，**有大簇就跟着加宽**（封顶 MAX）。

    用户实拍 2026-10-15：28 人的簇分两环后宽 848px，超过卡片内容宽（880 − 24*2 = 832）
    —— 不跟着加宽就会被裁掉一圈。
    """
    widest = max((c.width for c in clusters), default=0)
    return min(GRAPH_CARD_WIDTH_MAX, max(GRAPH_CARD_WIDTH, widest + 2 * _CARD_PAD_X))


def _graph_viewport_width(clusters: Sequence[ClusterLayout]) -> int:
    """截图视口宽 = 卡片宽 + body 左右 padding（16*2）+ 8px 余量。"""
    return _graph_card_width(clusters) + 40


#: 边上直接写的关系名。**不再靠「→」字符表方向**（用户 2026-10-14：「倾心→」不够明确）
#: —— 方向由 dst 端的**真箭头**承担（`layout._arrow_wings`）。夫妻无向，没有箭头。
_EDGE_LABEL: Final[dict[str, str]] = {
    "marriage": "夫妻",
    "crush": "倾心",
    "like": "喜欢",
}

#: 图例（图 footer 与纯文本回退**共用同一条**，别写两遍）：三种关系 + 端口圆含义。
#: 连线**一律实线**（2026-10-14 用户口径），所以这里只按颜色区分，不再提线型。
_GRAPH_LEGEND: Final[str] = (
    "粉色=夫妻（双向） · 琥珀=倾心 · 蓝色=喜欢 · "
    "有向边两端：实心圆=发起方，空心圆=被指向的一方"
)

#: 节点名字最多显示几个字符（超出截断 + `…`）：节点盒只有 96px 宽 ≈ 6 个 16px 汉字，
#: 再长就会跟旁边的边/关系名胶囊挤在一起（用户实拍：「后藤一里（烟雨版）」）。
#: 真正干活的是 `graph_g6.truncate_name`（名字现在画在 canvas 上）——这里只留个别名。
NAME_MAX: Final[int] = graph_g6.NAME_MAX

#: 关系图私有部分（公共段见 `_BASE_CSS`：字体栈/卡片框/标题条**只写一处**）；
#: `{card_width}` 由 `GRAPH_CARD_WIDTH` 注入（**不能用 `.format()`**，见 _NOTICE_CSS）。
_GRAPH_CSS: Final[str] = (
    _BASE_CSS
    + """
  /* 关系图卡片更宽：2 成员簇两列并排（两簇 808px > 普通卡片内容宽 590px） */
  .card { width: {card_width}px; }
  .sub { font-size: 17px; color: #9d174d; margin-bottom: 14px; }
  /* 一行放一个/两个簇：`.cluster` 是 inline-block，一行放不下会自动换行 */
  .row { display: block; text-align: center; }
  .cluster { position: relative; margin-bottom: 10px; display: inline-block;
             vertical-align: top; }
  /* 簇标题与 G6 容器**都绝对定位到簇左上角**：layout 在顶部留了 PAD(24) 给标题，
     容器尺寸 = 簇宽高、坐标 1:1，所以标题浮在容器上方那片留白里。cap 在 DOM 里
     排在容器**之后** → 画在画布之上（别调换顺序）。 */
  .cap { position: absolute; left: 0; top: 0; width: 100%; text-align: center;
         font-size: 16px; color: #9d174d; }
  .gk { position: absolute; left: 0; top: 0; }
"""
)

_GRAPH_TEMPLATE: Final[str] = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body><div class="card">
  <div class="title">{title}</div>
  <div class="sub">{sub}</div>
  {clusters}
  <div class="footer">{footer}</div>
</div>{script}</body></html>"""


def _cluster_html(cluster: ClusterLayout, *, index: int = 1) -> str:
    """一个簇的 HTML：**标题 + 一个 G6 容器**（尺寸 = 簇宽高，坐标 1:1）。

    具体怎么画（节点/边/端口圆/胶囊）全在 `graph_g6.cluster_data`，
    这里只做卡片层面的排布（`.cap` 绝对定位在顶部留白里，容器铺满簇）。
    """
    cap = (
        f'<div class="cap">{html_mod.escape(cluster.caption)}</div>'
        if cluster.caption
        else ""
    )
    return (
        f'<div class="cluster" style="width:{cluster.width}px;'
        f'height:{cluster.height}px">'
        f"{graph_g6.container_html(index, cluster)}{cap}</div>"
    )


def build_graph_html(
    clusters: Sequence[ClusterLayout],
    *,
    nicknames: dict[int, str],
    avatars: dict[int, str | None] | None = None,
    focus: int | None = None,
    title: str = "老婆关系图",
) -> str:
    """拼关系图 HTML（**纯函数**，转义都在这里做）。

    **排布**：2 成员簇两列并排（每行两个 `.cluster`），大簇（≥3 成员）独占一行 ——
    实拍里 4 个 2 成员簇各占一行、整图 2271px 高，用户要求「排紧一点」。
    """
    pool = avatars or {}
    rows: list[str] = []
    pair_row: list[str] = []  # 攒着两列并排的 2 成员簇

    def flush_pairs() -> None:
        if pair_row:
            rows.append(f'<div class="row">{"".join(pair_row)}</div>')
            pair_row.clear()

    for index, cluster in enumerate(clusters, start=1):
        block = _cluster_html(cluster, index=index)
        if len(cluster.nodes) == 2:
            pair_row.append(block)
            if len(pair_row) == 2:
                flush_pairs()
        else:
            flush_pairs()
            rows.append(f'<div class="row">{block}</div>')
    flush_pairs()
    who = (
        f"以「{html_mod.escape((nicknames.get(focus) or str(focus)).strip())}」为中心"
        if focus
        else "本群全部"
    )
    sub = f"{who} · {len(clusters)} 块 · {sum(len(c.lines) for c in clusters)} 条关系"
    return _GRAPH_TEMPLATE.format(
        css=_GRAPH_CSS.replace("{card_width}", str(_graph_card_width(clusters))),
        title=html_mod.escape(title),
        sub=html_mod.escape(sub),
        clusters="".join(rows),
        # g6.min.js 内联**一次** + 每簇一段初始化（都在卡片末尾，脚本自己置就绪旗）
        script=graph_g6.build_scripts(
            clusters,
            nicknames=nicknames,
            avatars=pool,
            colors=_EDGE_COLOR,
            widths=_EDGE_PX,
            labels=_EDGE_LABEL,
        ),
        footer=html_mod.escape(_GRAPH_LEGEND),
    )


async def render_graph(
    clusters: Sequence[ClusterLayout],
    *,
    nicknames: dict[int, str],
    avatars: dict[int, str | None] | None = None,
    focus: int | None = None,
) -> bytes | None:
    """渲染关系图 PNG。**任何失败都只 warning 并返回 None**（调用方回退纯文本）。

    ⚠️ **必须带 `wait_for`**：图是 G6 在 JS 里异步渲染的（还要等头像解码），
    不等 `window.__ready` 就会截到一张空卡片 —— 这是本方案唯一的实现风险点，
    测试用像素抽样钉住（见 `tests/test_graph_g6_render.py`）。
    """
    try:
        return await _shot(
            build_graph_html(
                clusters, nicknames=nicknames, avatars=avatars, focus=focus
            ),
            width=_graph_viewport_width(clusters),
            wait_for=graph_g6.READY_EXPR,
        )
    except Exception as e:
        logger.warning("关系图渲染失败，回退纯文本: %s: %s", type(e).__name__, e)
        return None


def build_graph_fallback_text(
    graph: RelationGraph,
    *,
    nicknames: dict[int, str],
    focus: int | None = None,
) -> str:
    """纯文本回退（**无 `@`**；关系说明与图 footer 用同一条 `_GRAPH_LEGEND`）。"""

    def name(uid: int) -> str:
        return (nicknames.get(uid) or "").strip() or str(uid)

    lines = [
        "老婆关系图" + (f"（以「{name(focus)}」为中心）" if focus else "（本群）"),
        f"共 {len(graph.nodes)} 人 · {len(graph.edges)} 条关系",
    ]
    for kind, label in (("marriage", "夫妻"), ("crush", "倾心"), ("like", "喜欢")):
        got = [
            f"{name(e.src)} {'与' if kind == 'marriage' else '→'} {name(e.dst)}"
            for e in graph.edges
            if e.kind == kind
        ]
        if got:
            lines.append(f"{label}：" + "，".join(got))
    lines.append(_GRAPH_LEGEND)
    if len(graph.edges) < graph.total_edges:
        lines.append(f"（还有 {graph.total_edges - len(graph.edges)} 条未显示）")
    return "\n".join(lines)[:1200]


__all__ = [
    "CARD_WIDTH",
    "MAX_WIDTH",
    "CardContext",
    "NoticeContext",
    "NoticeItem",
    "avatar_url",
    "build_fallback_text",
    "build_graph_fallback_text",
    "build_graph_html",
    "build_html",
    "build_notice_fallback_text",
    "build_notice_html",
    "fetch_avatar_data_uri",
    "fetch_avatars",
    "format_last_seen",
    "notice_user_ids",
    "render_card",
    "render_graph",
    "render_notice",
    "sex_label",
    "to_data_uri",
]
