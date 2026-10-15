"""真渲染验收：**真 Chromium** 出一条关系图，逐像素验收。

为什么非要真出图：G6 是**异步**渲染的（还要等图片解码），`window.__ready` 没等对就会
截到一张空卡片 —— 这类 bug 纯字符串断言一个都盖不住。这里钉四件事：

1. 整张卡片不是空白（非白像素 > 5000）；
2. 沿每条边采样命中自己的边色（夫妻粉 4px / 倾心琥珀 3px / 喜欢蓝 3px）；
3. 头像圆心那片区域是头像的颜色（图片真的贴上了）；没头像的是粉底占位；
4. **胶囊底真的是白的**：把卡片底色换成非白再出一次图 —— 白卡上「白像素」没有证明力，
   改底之后纯白像素的唯一来源就是胶囊，于是「胶囊内部纯白 + 胶囊外部是底色」能直接钉住
   G6 静默忽略 `labelCfg.style.background` 那个坑（所以要自绘）；
5. 端点圆：实心圆心 = 边色、空心圆心 = 白、环 = 边色（外径一致靠 `size` 缩 3px 实现）。

像素取法：**簇容器单独截图**（`locator('#g6-N').screenshot()`）—— 出图左上角就是簇局部
(0,0)，于是 layout 的坐标可以直接当像素坐标用，不必猜卡片/行/簇的页内偏移。
PNG 解码用标准库（zlib + 过滤器还原），零新增依赖。
"""

from __future__ import annotations

import base64
import math
import pathlib
import struct
import zlib

import pytest

pytest.importorskip(
    "nonebot_plugin_htmlrender", reason="出图是可选依赖（htmlrender + playwright）"
)
pytest.importorskip("playwright", reason="出图是可选依赖（htmlrender + playwright）")

from playwright.async_api import async_playwright

from nonebot_plugin_waifu import graph_g6, layout, relations, render

#: 卡片底色的替换（非白底：白像素只可能来自胶囊）
_COLORED = (
    "background: #ffffff; border-radius: 14px;",
    "background: #dbeafe; border-radius: 14px;",
)
CARD_BG = (0xDB, 0xEA, 0xFE)
WHITE = (255, 255, 255)

AV_COLOR = {1: "#16a34a", 2: "#7c3aed", 4: "#f97316", 5: "#e11d48"}
NAMES = {1: "小樱", 2: "知世", 3: "后藤一里（烟雨版）", 4: "甲", 5: "乙"}


def _avatar(color: str) -> str:
    """纯色圆头像（**不带文字**：圆心就是纯色，采样才没有歧义）。"""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
        f'<circle cx="32" cy="32" r="32" fill="{color}"/></svg>'
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


AVATARS: dict[int, str | None] = {uid: _avatar(c) for uid, c in AV_COLOR.items()}
AVATARS[3] = None  # 没头像 → 占位（顺带盖住占位那条路径）

GRAPH = relations.build_relations(
    marriages=[(1, 2, 1.0)],
    crushes={3: 1},
    likes={4: 5},
    max_edges=20,
)
CLUSTERS = layout.layout_graph(GRAPH, focus=None)


# --------------------------------------------------------------------------- #
# 纯标准库 PNG 读取（8-bit RGB/RGBA；Chromium 截图就是这个规格）
# --------------------------------------------------------------------------- #
class Img:
    def __init__(self, path: pathlib.Path) -> None:
        data = path.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        pos, idat = 8, bytearray()
        w = h = depth = ctype = 0
        while pos < len(data):
            (length,) = struct.unpack(">I", data[pos : pos + 4])
            typ = data[pos + 4 : pos + 8]
            chunk = data[pos + 8 : pos + 8 + length]
            pos += 12 + length
            if typ == b"IHDR":
                w, h, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", chunk)
                assert depth == 8 and interlace == 0
            elif typ == b"IDAT":
                idat += chunk
            elif typ == b"IEND":
                break
        raw = zlib.decompress(bytes(idat))
        ch = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
        stride = w * ch
        out = bytearray(h * stride)
        prev = bytearray(stride)
        p = 0
        for y in range(h):
            filt = raw[p]
            p += 1
            line = bytearray(raw[p : p + stride])
            p += stride
            if filt == 1:
                for i in range(ch, stride):
                    line[i] = (line[i] + line[i - ch]) & 0xFF
            elif filt == 2:
                for i in range(stride):
                    line[i] = (line[i] + prev[i]) & 0xFF
            elif filt == 3:
                for i in range(stride):
                    a = line[i - ch] if i >= ch else 0
                    line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
            elif filt == 4:
                for i in range(stride):
                    a = line[i - ch] if i >= ch else 0
                    b = prev[i]
                    c = prev[i - ch] if i >= ch else 0
                    pp = a + b - c
                    pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                    line[i] = (
                        line[i]
                        + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))
                    ) & 0xFF
            else:
                assert filt == 0
            out[y * stride : (y + 1) * stride] = line
            prev = line
        self.w, self.h, self.ch, self.px = w, h, ch, out

    def at(self, x: int, y: int) -> tuple[int, int, int]:
        x, y = max(0, min(x, self.w - 1)), max(0, min(y, self.h - 1))
        i = (y * self.w + x) * self.ch
        return self.px[i], self.px[i + 1], self.px[i + 2]

    def hex(self, x: int, y: int) -> str:
        r, g, b = self.at(x, y)
        return f"#{r:02x}{g:02x}{b:02x}"

    def count_near(self, x: int, y: int, w: int, h: int, rgb, tol: int = 30) -> int:
        return sum(
            1
            for yy in range(y, y + h)
            for xx in range(x, x + w)
            if sum(abs(a - b) for a, b in zip(self.at(xx, yy), rgb, strict=True)) <= tol
        )


def _rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _diff(a, b) -> int:
    return sum(abs(x - y) for x, y in zip(a, b, strict=True))


async def test_g6_graph_renders_with_avatars_edges_capsules_and_ports(tmp_path):
    """一条 ≥1 簇的关系图：非空白 + 边色 + 头像 + 白底胶囊 + 端点圆，逐像素验收。"""
    png = await render.render_graph(CLUSTERS, nicknames=NAMES, avatars=AVATARS)
    assert png is not None, "render_graph 返回 None（渲染失败会静默回退文本）"
    (tmp_path / "card.png").write_bytes(png)
    card = Img(tmp_path / "card.png")
    assert card.w == render.GRAPH_MAX_WIDTH
    assert card.h > 200
    non_white = sum(
        1 for y in range(card.h) for x in range(card.w) if card.at(x, y) != WHITE
    )
    assert non_white > 5000, f"卡片几乎空白（非白像素 {non_white}）"

    html = render.build_graph_html(CLUSTERS, nicknames=NAMES, avatars=AVATARS)
    color, plain = _COLORED
    colored = html.replace(color, plain)
    assert colored != html, "卡片底色没换掉，白像素证明不了胶囊"

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as e:  # 本机没装 chromium → 跳过（不是本仓的问题）
            if "Executable doesn't exist" in str(e):
                pytest.skip("playwright 浏览器未安装")
            raise
        page = await browser.new_page(
            viewport={"width": render.GRAPH_MAX_WIDTH, "height": 100},
            device_scale_factor=1,
        )
        shots: dict[str, list[Img]] = {}
        for tag, source in (("plain", html), ("bg", colored)):
            await page.goto(pathlib.Path.cwd().as_uri())
            await page.set_content(source, wait_until="networkidle")
            await page.wait_for_function(graph_g6.READY_EXPR, timeout=15_000)
            shots[tag] = []
            for i in range(1, len(CLUSTERS) + 1):
                path = tmp_path / f"{tag}-{i}.png"
                await page.locator(f"#g6-{i}").screenshot(path=str(path))
                shots[tag].append(Img(path))
        await browser.close()

    assert len(CLUSTERS) == 2
    for ci, cluster in enumerate(CLUSTERS):
        pim, bim = shots["plain"][ci], shots["bg"][ci]
        assert (pim.w, pim.h) == (cluster.width, cluster.height)
        # ① 头像：圆心 48x48 区域里该色占大头（纯色圆头像，不含文字）
        for node in cluster.nodes:
            color = AV_COLOR.get(node.uid)
            cx, cy = int(node.cx), int(node.cy)
            if color:
                hits = pim.count_near(cx - 24, cy - 24, 48, 48, _rgb(color))
                assert hits > 200, f"簇{ci + 1} uid={node.uid} 头像没贴上（{hits}）"
            else:
                hits = pim.count_near(cx - 24, cy - 24, 48, 48, _rgb(graph_g6.PH_BG))
                assert hits > 200, f"簇{ci + 1} uid={node.uid} 占位圆没画（{hits}）"
        # ② 边：沿线采样命中自己的边色
        for line in cluster.lines:
            want = _rgb(render._EDGE_COLOR[line.kind])
            x1, y1, x2, y2 = graph_g6.line_endpoints(line)
            length = max(1.0, math.hypot(x2 - x1, y2 - y1))
            for t in (6 / length, 0.25, 0.5, 0.75, 1 - 6 / length):
                px, py = int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t)
                if 0.45 < t < 0.55:  # 中点可能被关系名胶囊压住
                    continue
                got = pim.at(px, py)
                assert _diff(got, want) <= 120, (
                    f"簇{ci + 1} {line.kind} 边在 t={t:.2f} 处是 {pim.hex(px, py)}"
                    f"，期望 {render._EDGE_COLOR[line.kind]}"
                )
            # ③ 胶囊：内部纯白（蓝底版）、外部是底色、内部白像素占大头
            rot = math.radians(layout.label_rotation(line.angle))
            cos_r, sin_r = math.cos(rot), math.sin(rot)
            ax = line.label_x + layout.CHIP_W / 2
            ay = line.label_y + layout.CHIP_H

            def chip_pt(
                dx: float,
                dy: float,
                _ax: float = ax,
                _ay: float = ay,
                _cos: float = cos_r,
                _sin: float = sin_r,
            ) -> tuple[int, int]:
                """Chip 局部坐标（原点 = 底边中点锚点，y 向上为负）→ 世界像素坐标。"""
                return (
                    round(_ax + dx * _cos - dy * _sin),
                    round(_ay + dx * _sin + dy * _cos),
                )

            inner = chip_pt(-18.0, -12.0)  # 胶囊内，避开居中的关系名文字
            assert bim.at(*inner) == WHITE, (
                f"簇{ci + 1} {line.kind} 胶囊底不是白的（{bim.hex(*inner)}）—— "
                "G6 会静默忽略 labelCfg.style.background，必须自绘"
            )
            just_out = chip_pt(0.0, 10.0)  # 锚点下方 10px = 胶囊外
            assert _diff(bim.at(*just_out), CARD_BG) <= 30, (
                f"簇{ci + 1} 胶囊外应是底色（{bim.hex(*just_out)}）—— 白区不该漫出去"
            )
            hit = tot = 0
            for dyy in range(-layout.CHIP_H + 2, 0, 2):
                for dxx in range(-layout.CHIP_W // 2 + 2, layout.CHIP_W // 2 - 1, 2):
                    tot += 1
                    hit += bim.at(*chip_pt(float(dxx), float(dyy))) == WHITE
            assert hit > 0.4 * tot, f"簇{ci + 1} 胶囊内白像素只有 {hit}/{tot}"
        # ④ 端点圆：实心圆心 = 边色；空心圆心 = 白、环 = 边色
        for port in cluster.ports:
            want = _rgb(render._EDGE_COLOR[port.kind])
            center = (port.cx, port.cy)
            if port.filled:
                assert _diff(pim.at(*center), want) <= 120, (
                    f"簇{ci + 1} 实心端口 ({center}) 是 {pim.hex(*center)}"
                )
            else:
                assert pim.at(*center) == WHITE, (
                    f"簇{ci + 1} 空心端口 ({center}) 不是白心：{pim.hex(*center)}"
                )
                ring = (port.cx + 4, port.cy)
                assert _diff(pim.at(*ring), want) <= 150, (
                    f"簇{ci + 1} 空心端口的环不是边色：{pim.hex(*ring)}"
                )


async def test_render_graph_falls_back_to_none_when_the_ready_flag_never_lands(
    monkeypatch,
):
    """就绪旗不置位（G6 崩了/资产缺了）→ 等超时 → 返回 None（调用方回退纯文本）。

    直接打 `wait_for` 那条路：把 `page.wait_for_function` 换成永远抛超时，
    断言 `render_graph` **吞掉异常**而不是把「没反应」暴露给用户。
    """
    import nonebot_plugin_waifu.render as render_mod

    class _Page:
        async def goto(self, *_a, **_kw) -> None:
            return None

        async def set_content(self, *_a, **_kw) -> None:
            return None

        async def wait_for_function(self, expr, timeout=None):
            raise TimeoutError(f"{expr} 超时（模拟就绪旗不置位）")

        async def screenshot(self, **_kw) -> bytes:  # pragma: no cover
            raise AssertionError("超时了不该还能截到图")

    class _Ctx:
        async def __aenter__(self):
            return _Page()

        async def __aexit__(self, *_exc) -> bool:
            return False

    monkeypatch.setattr(render_mod, "_load_page_ctx", lambda: lambda *a, **k: _Ctx())
    assert (
        await render_mod.render_graph(CLUSTERS, nicknames=NAMES, avatars=AVATARS)
        is None
    )


# --------------------------------------------------------------------------- #
# 用户实拍 2026-10-15：「重叠太多了」——28 人的簇（50 条边）真出图验收。
# --------------------------------------------------------------------------- #
BIG_N = 28
_FLAT = [
    "#16a34a",
    "#7c3aed",
    "#f97316",
    "#e11d48",
    "#0ea5e9",
    "#65a30d",
    "#c026d3",
    "#0d9488",
    "#ea580c",
    "#4f46e5",
    "#b45309",
    "#be123c",
]
BIG_AV_COLOR = {uid: _FLAT[(uid - 1) % len(_FLAT)] for uid in range(1, BIG_N + 1)}
BIG_AVATARS: dict[int, str | None] = {
    uid: _avatar(color) for uid, color in BIG_AV_COLOR.items()
}
BIG_NAMES = dict.fromkeys(range(1, BIG_N + 1), "猫")


def _big_graph():
    likes = {1: 2}
    likes.update(dict.fromkeys(range(2, BIG_N + 1), 1))
    return relations.build_relations(marriages=[], crushes={}, likes=likes)


BIG_CLUSTERS = layout.layout_graph(_big_graph(), focus=None)


async def test_big_cluster_renders_widened_with_every_avatar_on_its_ring(tmp_path):
    """28 人的簇：分环 → 卡片加宽 → 真渲染里 28 个头像一个不少地坐在各自位置上。

    「重叠」的硬保证在 `test_layout.py` 的
    `test_big_clusters_split_into_rings_and_never_overlap`（盒子两两不重叠）；
    这里钉的是**渲染口径**：卡片真的跟着加宽了、每个头像都画在了 layout
    给的坐标上（换环后位置没歪、没被裁）。

    ⚠️ 这里**不调 `render.render_graph`**：htmlrender 的浏览器是**绑事件循环**的
    模块级单例，而 pytest 每个用例一个循环 —— 本进程里第二次 `render_graph`
    会拿到上一个（已关闭）循环的浏览器而扑空（生产只有一个循环，不受影响；
    本文件第一个用例已经钉过 `render_graph` 那条路了）。
    """
    assert len(BIG_CLUSTERS) == 1
    (cluster,) = BIG_CLUSTERS
    assert len(cluster.nodes) == BIG_N
    viewport = render._graph_viewport_width(BIG_CLUSTERS)
    assert viewport > render.GRAPH_MAX_WIDTH, "大簇的视口没跟着加宽"

    html = render.build_graph_html(
        BIG_CLUSTERS, nicknames=BIG_NAMES, avatars=BIG_AVATARS
    )
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch()
        except Exception as e:  # pragma: no cover
            if "Executable doesn't exist" in str(e):
                pytest.skip("playwright 浏览器未安装")
            raise
        page = await browser.new_page(
            viewport={"width": viewport, "height": 100}, device_scale_factor=1
        )
        await page.goto(pathlib.Path.cwd().as_uri())
        await page.set_content(html, wait_until="networkidle")
        await page.wait_for_function(graph_g6.READY_EXPR, timeout=15_000)
        full = tmp_path / "big-card.png"
        await page.screenshot(path=str(full), full_page=True)
        shot = tmp_path / "big-cluster1.png"
        await page.locator("#g6-1").screenshot(path=str(shot))
        await browser.close()

    card = Img(full)
    assert card.w == viewport, f"整卡宽 {card.w} ≠ 视口 {viewport}（被裁/被压扁）"
    non_white = sum(
        1 for y in range(card.h) for x in range(card.w) if card.at(x, y) != WHITE
    )
    assert non_white > 20_000, f"大簇卡片太素（非白像素 {non_white}）"

    im = Img(shot)
    assert (im.w, im.h) == (cluster.width, cluster.height)
    for node in cluster.nodes:  # 每个头像都在自己的圆心位置（48x48 里占大头）
        color = BIG_AV_COLOR[node.uid]
        hits = im.count_near(int(node.cx) - 24, int(node.cy) - 24, 48, 48, _rgb(color))
        assert hits > 200, f"uid={node.uid} 的头像没画在自己的位置上（{hits} px）"
