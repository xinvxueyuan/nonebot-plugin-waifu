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
import math
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .layout import ClusterLayout, EdgeLine, NodeBox
    from .relations import RelationGraph

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


# ---------------------------------------------------------------------------
# 关系图：节点 = 头像 + 昵称，边 = `linear-gradient` 硬色带画出的斜线
#
# ⚠️ litehtml **没有 `transform`**：斜线不能靠旋转盒子画。用渐变硬色带的那条缝当线
# （现场探针实测：线上像素数与理论值一致）—— 盒 = 线段的 bbox，色带穿过盒中心，
# 于是色带**恰好**是这条线段。
# ---------------------------------------------------------------------------

#: 关系图：边只有两种配色（夫妻 = 主题粉实线 / 倾心 = 琥珀虚线）
_NODE_EDGE_COLOR: Final[dict[str, str]] = {
    "marriage": "#db2777",  # 夫妻：主题粉、实线粗
    "crush": "#f59e0b",  # 倾心：琥珀、虚线细
}

#: 边的主线段宽度（px）：夫妻最粗（4）> 倾心（3）—— 用户实拍：
#: 「连线几乎看不见」（旧值统一 3px 且虚线被切成 5 段，每段只有几个像素）。
_NODE_EDGE_PX: Final[dict[str, int]] = {"marriage": 4, "crush": 3}

#: 虚线切片数：`dashes=2` → 3 切片、**只画首尾两段**，中间那段正好留给关系名 chip
#: （旧值 5 → 9 切片只画 5 段，段太短看不见）。
_NODE_EDGE_DASHES: Final[dict[str, int]] = {"marriage": 1, "crush": 2}

#: 关系图卡片宽：**比其它卡片宽** —— 2 成员簇要两列并排，而每簇是
#: `layout.PAIR_SPAN(260) + 节点盒(96) + 两侧 PAD(24*2) = 404px`，两簇 808px；
#: 普通卡片（640）的内容宽只有 590px，放不下两列。
GRAPH_CARD_WIDTH: Final[int] = 880
#: 关系图的渲染上限宽：必须 ≥ 卡片宽 + body padding（16*2），否则内容被压扁。
GRAPH_MAX_WIDTH: Final[int] = 920

#: 边上直接写的关系名；`倾心→` 让「谁倾心谁」在图上可读（用户口径：能看出方向）
_EDGE_LABEL: Final[dict[str, str]] = {"marriage": "夫妻", "crush": "倾心→"}

#: 节点名字最多显示几个字符（超出截断 + `…`）：节点盒只有 96px 宽 ≈ 6 个 16px 汉字，
#: 再长就会折行、压到下面的边和关系名 chip（用户实拍：「后藤一里（烟雨版）」）。
NAME_MAX: Final[int] = 6

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
  .cap { font-size: 16px; color: #9d174d; margin-bottom: 4px; }
  /* 96px = layout.NODE_W / layout.NODE_H（节点盒尺寸）；两处必须一致 */
  .node { position: absolute; width: 96px; height: 96px; text-align: center; }
  .avatar { width: 64px; height: 64px; border-radius: 32px; border: 3px solid #fbcfe8;
            display: block; margin: 0 auto; }
  .ph-av { display: block; width: 64px; height: 64px; border-radius: 32px;
           background: #fce7f3; color: #9d174d; font-size: 26px;
           line-height: 64px; text-align: center; margin: 0 auto; }
  /* `nowrap`：名字已截到 6 字 + …，再换行就会压到下面的图（单行才好看） */
  .nm { font-size: 16px; color: #0f172a; line-height: 24px; white-space: nowrap; }
  .seg { position: absolute; }
  .elab { position: absolute; font-size: 14px; padding: 1px 6px;
          border-radius: 8px; background: #ffffff; }
"""
)

_GRAPH_TEMPLATE: Final[str] = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body><div class="card">
  <div class="title">{title}</div>
  <div class="sub">{sub}</div>
  {clusters}
  <div class="footer">{footer}</div>
</div></body></html>"""

_SEG_TEMPLATE: Final[str] = (
    '<div class="seg" style="left:{x}px;top:{y}px;width:{w}px;height:{h}px;'
    'background:{bg}"></div>'
)
_NODE_TEMPLATE: Final[str] = (
    '<div class="node" style="left:{x}px;top:{y}px">{avatar}'
    '<div class="nm">{name}</div></div>'
)
_LABEL_TEMPLATE: Final[str] = (
    '<div class="elab" style="left:{x}px;top:{y}px;color:{color};'
    'border:1px solid {color}">{text}</div>'
)


def _band_background(w: int, h: int, angle: float, color: str, px: int = 3) -> str:
    """用 `linear-gradient` 的硬色带画出穿过盒中心的斜线段（litehtml 无 transform）。

    渐变轴取**垂直线段**方向（`angle = atan2(dy, dx)` 的度数），色带放在 50%：
    色带与线段平行、且穿过盒中心 —— 而盒就是线段的 bbox，所以色带**恰好**是这条线段。
    色带宽度按渐变线长度换算成百分比：`L = |w·sinθ| + |h·cosθ|`。
    """
    rad = math.radians(angle)
    length = abs(w * math.sin(rad)) + abs(h * math.cos(rad)) or 1.0
    half = (px / 2) / length * 100.0
    lo, hi = 50.0 - half, 50.0 + half
    return (
        f"linear-gradient({angle:.2f}deg, transparent {lo:.2f}%, {color} {lo:.2f}%,"
        f" {color} {hi:.2f}%, transparent {hi:.2f}%)"
    )


def _padded_box(
    x: int, y: int, w: int, h: int, *, px: int
) -> tuple[int, int, int, int]:
    """把线段盒在**法线方向**撑到至少 `px`（保持中心不动）。

    ⚠️ 实测坑：水平/垂直线的 bbox 在法线方向只有 `max(1, ...)` **伪造出的 1px** ——
    色带的粗细被盒子卡死在 1px，画出来就是一根发丝（用户实拍抱怨过「看不见线」）。
    斜线本来就有足够余量，这里只对「薄盒子」生效。
    """
    pw, ph = max(w, px), max(h, px)
    return (round(x + (w - pw) / 2), round(y + (h - ph) / 2), pw, ph)


def _segment_html(line: EdgeLine, color: str, *, dashes: int = 1, px: int = 3) -> str:
    """一条边：`dashes=1` 实线；`>1` 拆成虚线段（每段一个小 bbox，共用同一角度）。

    `px` = 主线段粗细（夫妻 4 / 倾心 3）—— 线要真的看得见，别指望「细线也能看出」。

    ⚠️ 备选方案（探针 B FAIL 时）把本函数体换成「沿线段布 3×3px 小方块」即可，
    入参/返回不变，上层模板不用改。
    """
    if dashes <= 1:
        bx, by, bw, bh = _padded_box(line.x, line.y, line.w, line.h, px=px)
        return _SEG_TEMPLATE.format(
            x=bx,
            y=by,
            w=bw,
            h=bh,
            bg=_band_background(bw, bh, line.angle, color, px),
        )
    # 虚线：以**线段中点**为基准向两端量，而不是从 bbox 角上累加 ——
    # 轴对齐线的 bbox 高/宽是伪造的 1px，从角上累加会让每段虚线错开 1px。
    rad = math.radians(line.angle)
    ux, uy = math.cos(rad), math.sin(rad)
    mx, my = line.x + line.w / 2, line.y + line.h / 2
    out: list[str] = []
    slices = dashes * 2 - 1
    for i in range(slices):
        if i % 2:  # 奇数片留白 = 虚线间隙（中间那段正好留给 chip）
            continue
        t0, t1 = i / slices, (i + 1) / slices
        c = (t0 + t1) / 2 - 0.5  # 该片中心相对线段中点的参数
        sw = max(1, round(line.w * (t1 - t0)))
        sh = max(1, round(line.h * (t1 - t0)))
        bx, by, bw, bh = _padded_box(
            round(mx + c * ux * line.w - sw / 2),
            round(my + c * uy * line.h - sh / 2),
            sw,
            sh,
            px=px,
        )
        out.append(
            _SEG_TEMPLATE.format(
                x=bx,
                y=by,
                w=bw,
                h=bh,
                bg=_band_background(bw, bh, line.angle, color, px),
            )
        )
    return "".join(out)


def _node_html(
    node: NodeBox, *, nicknames: dict[int, str], avatars: dict[int, str | None]
) -> str:
    raw = (nicknames.get(node.uid) or "").strip()
    # 名字截断到 `NAME_MAX` 字 + …：节点盒只有 96px 宽，长名字会**折行压到图上**
    # （用户实拍：「后藤一里（烟雨版）」折成两行、跟关系名 chip 交叠）。
    if len(raw) > NAME_MAX:
        raw = f"{raw[:NAME_MAX]}…"
    name = html_mod.escape(raw or str(node.uid))
    uri = avatars.get(node.uid)
    if uri:
        avatar = f'<img class="avatar" src="{html_mod.escape(uri)}">'
    else:
        first = (raw or str(node.uid))[:1]
        avatar = f'<span class="ph-av">{html_mod.escape(first)}</span>'
    return _NODE_TEMPLATE.format(x=node.x, y=node.y, avatar=avatar, name=name)


def _cluster_html(
    cluster: ClusterLayout, *, nicknames: dict[int, str], pool: dict[int, str | None]
) -> str:
    """一个簇的 HTML：线段 + 关系名 chip + 节点（绝对定位，坐标来自 layout）。"""
    segs: list[str] = []
    for line in cluster.lines:
        color = _NODE_EDGE_COLOR.get(line.kind, "#db2777")
        segs.append(
            _segment_html(
                line,
                color,
                dashes=_NODE_EDGE_DASHES.get(line.kind, 1),
                px=_NODE_EDGE_PX.get(line.kind, 3),
            )
        )
        segs.append(
            _LABEL_TEMPLATE.format(
                x=line.label_x,
                y=line.label_y,
                color=color,
                text=html_mod.escape(_EDGE_LABEL.get(line.kind, line.kind)),
            )
        )
    nodes = "".join(
        _node_html(n, nicknames=nicknames, avatars=pool) for n in cluster.nodes
    )
    cap = (
        f'<div class="cap">{html_mod.escape(cluster.caption)}</div>'
        if cluster.caption
        else ""
    )
    return (
        f'<div class="cluster" style="width:{cluster.width}px;'
        f'height:{cluster.height}px">{cap}{"".join(segs)}{nodes}</div>'
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

    for cluster in clusters:
        block = _cluster_html(cluster, nicknames=nicknames, pool=pool)
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
    footer = "粉色实线=夫妻 · 橙色虚线=倾心（箭头方向 = 倾心的一方 → 被倾心的人）"
    return _GRAPH_TEMPLATE.format(
        css=_GRAPH_CSS.replace("{card_width}", str(GRAPH_CARD_WIDTH)),
        title=html_mod.escape(title),
        sub=html_mod.escape(sub),
        clusters="".join(rows),
        footer=html_mod.escape(footer),
    )


async def render_graph(
    clusters: Sequence[ClusterLayout],
    *,
    nicknames: dict[int, str],
    avatars: dict[int, str | None] | None = None,
    focus: int | None = None,
) -> bytes | None:
    """渲染关系图 PNG。**任何失败都只 warning 并返回 None**（调用方回退纯文本）。"""
    try:
        html_to_pic = _load_htmlkit()
        return await html_to_pic(
            build_graph_html(
                clusters, nicknames=nicknames, avatars=avatars, focus=focus
            ),
            max_width=GRAPH_MAX_WIDTH,
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
    """纯文本回退（**无 `@`**）。"""

    def name(uid: int) -> str:
        return (nicknames.get(uid) or "").strip() or str(uid)

    lines = [
        "老婆关系图" + (f"（以「{name(focus)}」为中心）" if focus else "（本群）"),
        f"共 {len(graph.nodes)} 人 · {len(graph.edges)} 条关系",
    ]
    for kind, label in (("marriage", "夫妻"), ("crush", "倾心")):
        got = [
            f"{name(e.src)} {'与' if kind == 'marriage' else '→'} {name(e.dst)}"
            for e in graph.edges
            if e.kind == kind
        ]
        if got:
            lines.append(f"{label}：" + "，".join(got))
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
