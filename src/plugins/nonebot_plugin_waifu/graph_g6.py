r"""关系图的 **G6 v4** 绘制（纯函数：布局产物 → HTML 片段；不碰网络）。

为什么是 G6（2026-10-15 用户拍板「去找相关的组件库工具来实现」）：
旧实现用 HTML div + `linear-gradient` 色带在轴对齐盒子里「假装」斜线，端点和粗细都要
反算像素；现在整张图交给组件库：头像节点（`type:'image'`）、真矢量边、白底胶囊关系名、
端点圆。**组件库只负责画**，坐标仍全部来自 `layout.py`（~40 条几何断言钉着）。

落地细节都是本机 Chromium 实测出来的结论，别再猜：

1. **`graph.render()` 在 v4.8.25 不返回 Promise**（`undefined`）—— 别 `.then()`。
   就绪信号由本模块的脚本自己置位：G6 的图片节点是异步解码的，先 `new Image()`
   预热全部 data URI，再 `render()`，最后双 `requestAnimationFrame` 置
   `window.__ready = true`；否则会截到「头像还没贴上」的空图。
2. **边的端点必须钉在 layout 的坐标上**：`startPoint/endPoint`（放 `style` 里或边模型
   顶层）实测**无效** —— 线被裁到节点 bbox（64px 方框）边缘，而 layout 的端点在离
   节点心 46px 处（`AVATAR/2 + GAP`，留 14px 空隙给端口圆）。改用「1px 隐藏锚点节点」：
   每条边两端各放一个 `size:1, opacity:0` 的圆节点在 layout 的精确端点上，边连锚点 ——
   实测 140→380 的线画在 141..378（亚像素差）。
3. **胶囊必须自绘**：G6 的 `labelCfg.style.background`（白底/圆角/内边距）实测被
   **静默忽略**（整块区域白像素 0）。改用「image 节点 + 内联 SVG」：白底圆角矩形 +
   1px 同色描边（白卡上没描边就看不见）+ 同色关系名，旋转烘进 SVG 自己的
   `<g transform="rotate(...)">`（锚点 = 底边中点，与旧 `.elab` 的
   `transform-origin:50% 100%` 同口径）。
4. **端点圆用 12px 小节点**：实心 = `size:12`、空心 = `size:9 + lineWidth:3`
   （canvas 的描边居中在半径上，所以空心要缩 3px 外径才与实心一致）。
5. **注入防护**：昵称进 JSON 数据（canvas 画字不需要 HTML 转义），但整包 JSON 会内联
   进 `<script>` —— 所以把 `<` 全部写成 `\u003c`，昵称里带 `</script>` 也炸不了页面。

Provenance：`@antv/g6` **v4.8.25** 的 `dist/g6.min.js`（MIT · 1,570,257B ·
sha256 `cb6283ad…`）—— 与官方 CDN 现拉的字节完全一致（实测复核过）。
bundle 源码里那句 `version="4.8.23"` 是上游包内没更新的常量，npm 包版本是 4.8.25。
"""

from __future__ import annotations

import base64
from functools import lru_cache
import html as html_mod
import json
import math
import pathlib
from typing import TYPE_CHECKING, Any, Final

from .layout import CHIP_H, CHIP_W, PORT_D, ClusterLayout, EdgeLine, label_rotation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: 头像节点直径（px）：`size:64` 的 image 节点，圆形 data URI 贴上去就是头像。
NODE_SIZE: Final[int] = 64
#: 头像外环：`style.stroke` + `lineWidth`（与旧 CSS
#: `.avatar{border:3px solid #fbcfe8}` 同色同宽）。
RING_COLOR: Final[str] = "#fbcfe8"
RING_WIDTH: Final[int] = 3
#: 没头像时的占位：粉底圆 + 首字（照旧 `.ph-av`）。
PH_BG: Final[str] = "#fce7f3"
PH_FG: Final[str] = "#9d174d"
PH_FONT: Final[int] = 26
#: 昵称：16px `#0f172a`，最多 `NAME_MAX` 字 + `…`（与旧 `_node_html` 同口径）。
NAME_FG: Final[str] = "#0f172a"
NAME_FONT: Final[int] = 16
NAME_MAX: Final[int] = 6
#: 关系名胶囊：14px、圆角 8（照旧 `.elab`）。
CHIP_FONT: Final[int] = 14
CHIP_RADIUS: Final[int] = 8
#: 胶囊位图的留边（px）：给 1px 描边与抗锯齿留位置。**`capsule_uri` 与节点 size
#: 必须用同一个值** —— G6 的 image 节点会把位图拉满节点，尺寸不一致就会缩放变形。
CAPSULE_PAD: Final[float] = 2.0
#: 空心端口圆的描边宽度（px）：外径与实心圆一致（见模块 docstring 第 4 条）。
PORT_STROKE: Final[int] = 3
#: 隐藏锚点节点的直径（px）：只用来把线的端点钉在 layout 坐标上，不画东西。
ANCHOR_SIZE: Final[int] = 1
#: canvas 画字要显式给 CJK 字体栈（宿主默认 sans-serif 不含中文字形时整张图是方块）；
#: 与 `render._BASE_CSS` 的字体栈**同一套**（Windows 走 YaHei、Linux 走 WenQuanYi）。
FONT_STACK: Final[str] = (
    '"WenQuanYi Zen Hei","Noto Sans CJK SC","Microsoft YaHei","PingFang SC",sans-serif'
)
#: 就绪旗：`render._shot` 的 `wait_for` 用它（G6 异步渲染，不等就会截到空图）。
READY_EXPR: Final[str] = "window.__ready === true"
#: 静态资源（v4.8.25 · MIT · 1,570,257B · sha256 见 `G6_SHA256`）。
G6_ASSET: Final[str] = "assets/g6.min.js"
G6_SHA256: Final[str] = (
    "cb6283adac42c7980b7912e5ad58d9af770a5dc1550718cea35ff30a5b576784"
)
G6_BYTES: Final[int] = 1_570_257

_LIB_TOKEN: Final[str] = "__G6_LIB__"
_CALLS_TOKEN: Final[str] = "__G6_CALLS__"
#: SVG 是 **XML**：属性里的引号必须转义。字体栈自带双引号，直接塞进
#: `font-family="..."` 会让 XML 解析失败、整张位图静默不显示（实测踩过：
#: 头像/胶囊全空白，`Image.onload` 直接 error）。
_FONT_ATTR: Final[str] = html_mod.escape(FONT_STACK, quote=True)
_MISSING_ASSET: Final[str] = (
    f"找不到 {G6_ASSET}（G6 静态资源未随包分发）—— 关系图回退纯文本"
)


def truncate_name(raw: str) -> str:
    """名字截断到 `NAME_MAX` 字 + `…`（与旧 `_node_html` 同口径）。"""
    text = (raw or "").strip()
    return f"{text[:NAME_MAX]}…" if len(text) > NAME_MAX else text


def line_endpoints(line: EdgeLine) -> tuple[float, float, float, float]:
    """线段两端坐标（bbox 中心 + 角度 + `hypot(w, h)` 反算）。

    layout 给的是 bbox `(x, y, w, h)` + `angle`，且**轴对齐线的另一个维度是伪造的
    1px**；这里从中心沿真方向量长度，轴对齐线于是得到真正水平/垂直的线。
    """
    mx, my = line.x + line.w / 2, line.y + line.h / 2
    length = math.hypot(line.w, line.h)
    rad = math.radians(line.angle)
    hx, hy = length / 2 * math.cos(rad), length / 2 * math.sin(rad)
    return mx - hx, my - hy, mx + hx, my + hy


def chip_aabb(
    anchor_x: float, anchor_y: float, angle: float
) -> tuple[float, float, float, float]:
    """关系名 chip **旋转后**的 AABB（锚点 = 底边中点）。

    与 `layout._rotated_chip_aabb` 同口径：自绘胶囊要用它定位 image 节点
    （节点的中心 = AABB 中心），所以必须与 layout 的空间判定逐像素同源 ——
    否则「chip 不压节点盒」这条保证会在渲染层被破坏。
    """
    rot = math.radians(label_rotation(angle))
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    xs: list[float] = []
    ys: list[float] = []
    corners = (
        (-CHIP_W / 2, 0.0),
        (CHIP_W / 2, 0.0),
        (CHIP_W / 2, -CHIP_H),
        (-CHIP_W / 2, -CHIP_H),
    )
    for ox, oy in corners:
        xs.append(anchor_x + ox * cos_r - oy * sin_r)
        ys.append(anchor_y + ox * sin_r + oy * cos_r)
    return min(xs), min(ys), max(xs), max(ys)


def _svg_uri(body: str, width: float, height: float) -> str:
    """内联 SVG → data URI（G6 的 image 节点只吃 URL/data URI，不吃 DOM 片段）。"""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f"{body}</svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode(
        "ascii"
    )


def placeholder_uri(nickname: str, uid: int) -> str:
    """没头像的成员：粉底圆 + 首字（照旧 `.ph-av` 的 26px `#9d174d`）。"""
    first = html_mod.escape(((nickname or "").strip() or str(uid))[:1])
    r = NODE_SIZE / 2
    return _svg_uri(
        f'<circle cx="{r}" cy="{r}" r="{r}" fill="{PH_BG}"/>'
        f'<text x="{r}" y="{r}" dy="0.36em" text-anchor="middle" '
        f'font-family="{_FONT_ATTR}" font-size="{PH_FONT}" '
        f'fill="{PH_FG}">{first}</text>',
        NODE_SIZE,
        NODE_SIZE,
    )


def capsule_uri(
    text: str, color: str, anchor_x: float, anchor_y: float, angle: float
) -> str:
    """白底胶囊关系名（圆角矩形 + 1px 同色描边 + 同色文字），旋转烘进 SVG。

    为什么不直接用 G6 的 `labelCfg.style.background`：实测**静默忽略**（见模块 docstring
    第 3 条）。自绘的几何与旧 `.elab` 完全一致：底边中点贴在自己的线上，跟着线转，
    永远正着读（`label_rotation`）。
    """
    x0, y0, x1, y1 = chip_aabb(anchor_x, anchor_y, angle)
    w, h = x1 - x0 + 2 * CAPSULE_PAD, y1 - y0 + 2 * CAPSULE_PAD
    gx, gy = anchor_x - x0 + CAPSULE_PAD, anchor_y - y0 + CAPSULE_PAD
    deg = round(label_rotation(angle), 1)
    body = (
        f'<g transform="rotate({deg} {gx:.1f} {gy:.1f})">'
        f'<rect x="{gx - CHIP_W / 2:.1f}" y="{gy - CHIP_H:.1f}" width="{CHIP_W}" '
        f'height="{CHIP_H}" rx="{CHIP_RADIUS}" fill="#ffffff" stroke="{color}" '
        f'stroke-width="1"/>'
        f'<text x="{gx:.1f}" y="{gy - CHIP_H / 2:.1f}" text-anchor="middle" '
        f'dominant-baseline="central" font-family="{_FONT_ATTR}" '
        f'font-size="{CHIP_FONT}" fill="{color}">{html_mod.escape(text)}</text>'
        f"</g>"
    )
    return _svg_uri(body, round(w, 1), round(h, 1))


def capsule_node(
    *,
    node_id: str,
    text: str,
    color: str,
    anchor_x: float,
    anchor_y: float,
    angle: float,
) -> dict[str, Any]:
    """关系名胶囊节点：**旋转后的 AABB 中心**放节点，位图与节点尺寸逐像素一致。

    G6 的 image 节点会把位图拉满节点矩形，所以 `size` 必须等于 SVG 自身的宽高
    （同一个 `CAPSULE_PAD`），否则胶囊会被缩放几个百分点、与 layout 的碰撞口径错开。
    """
    x0, y0, x1, y1 = chip_aabb(anchor_x, anchor_y, angle)
    size = [
        round(x1 - x0 + 2 * CAPSULE_PAD, 1),
        round(y1 - y0 + 2 * CAPSULE_PAD, 1),
    ]
    return {
        "id": node_id,
        "x": round((x0 + x1) / 2, 1),
        "y": round((y0 + y1) / 2, 1),
        "type": "image",
        "size": size,
        "img": capsule_uri(text, color, anchor_x, anchor_y, angle),
    }


def avatar_id(uid: int) -> str:
    """头像节点的 id（前缀区分于锚点/端口，免撞）。"""
    return f"n{int(uid)}"


def anchor_id(index: int, slot: int) -> str:
    """隐藏锚点节点的 id（每条边两个：起点/终点）。"""
    return f"a{index}-{slot}"


def port_id(index: int, slot: int) -> str:
    """端点圆节点的 id。"""
    return f"p{index}-{slot}"


def capsule_id(index: int, slot: int) -> str:
    """关系名胶囊节点的 id。"""
    return f"c{index}-{slot}"


def container_id(index: int) -> str:
    """簇的 G6 容器 `div` 的 id（一簇一个 Graph 实例）。"""
    return f"g6-{index}"


def container_html(index: int, cluster: ClusterLayout) -> str:
    """簇的 G6 容器（尺寸 = 簇宽高；坐标 1:1，`fitView:false` 下无缩放无裁切）。"""
    return (
        f'<div class="gk" id="{container_id(index)}" '
        f'style="width:{cluster.width}px;height:{cluster.height}px"></div>'
    )


def cluster_data(
    cluster: ClusterLayout,
    *,
    index: int,
    nicknames: Mapping[int, str],
    avatars: Mapping[int, str | None],
    colors: Mapping[str, str],
    widths: Mapping[str, int],
    labels: Mapping[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """一个簇 → G6 的 `{nodes, edges}`（**纯函数**，可直接断言）。

    节点四类：头像（image）/ 隐藏锚点（边端点）/ 端点圆（有向边）/ 关系名胶囊。
    边一律连**锚点**：G6 会自己在锚点圆半径处收笔，于是线的坐标就是 layout 的坐标。
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for node in cluster.nodes:
        raw = nicknames.get(node.uid) or ""
        uri = avatars.get(node.uid) or placeholder_uri(raw, node.uid)
        nodes.append({
            "id": avatar_id(node.uid),
            "x": node.cx,
            "y": node.cy,
            "type": "image",
            "size": NODE_SIZE,
            "img": uri,
            "label": truncate_name(raw) or str(node.uid),
            "style": {"stroke": RING_COLOR, "lineWidth": RING_WIDTH},
        })
    for slot, line in enumerate(cluster.lines):
        color = colors.get(line.kind, "#db2777")
        x1, y1, x2, y2 = line_endpoints(line)
        for uid, (px, py) in enumerate(((x1, y1), (x2, y2))):
            nodes.append({
                "id": anchor_id(index, 2 * slot + uid),
                "x": round(px, 1),
                "y": round(py, 1),
                "type": "circle",
                "size": ANCHOR_SIZE,
                "style": {"opacity": 0},
            })
        edges.append({
            "source": anchor_id(index, 2 * slot),
            "target": anchor_id(index, 2 * slot + 1),
            "style": {
                "stroke": color,
                "lineWidth": widths.get(line.kind, 3),
                "endArrow": False,
            },
        })
        nodes.append(
            capsule_node(
                node_id=capsule_id(index, slot),
                text=labels.get(line.kind, line.kind),
                color=color,
                anchor_x=line.label_x + CHIP_W / 2,
                anchor_y=line.label_y + CHIP_H,
                angle=line.angle,
            )
        )
    for slot, port in enumerate(cluster.ports):
        color = colors.get(port.kind, "#db2777")
        if port.filled:
            size, style = PORT_D, {"fill": color, "stroke": color, "lineWidth": 0}
        else:
            size = PORT_D - PORT_STROKE
            style = {"fill": "#ffffff", "stroke": color, "lineWidth": PORT_STROKE}
        nodes.append({
            "id": port_id(index, slot),
            "x": port.cx,
            "y": port.cy,
            "type": "circle",
            "size": size,
            "style": style,
        })
    return {"nodes": nodes, "edges": edges}


def _payload(data: Mapping[str, Any]) -> str:
    r"""数据 → 内联 JSON（`<` 全写成 `\u003c`：昵称里带 `</script>` 也炸不了页面）。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


_HELPERS: Final[str] = """
  function render(id, data, width, height) {
    var el = document.getElementById(id);
    if (!el) { return; }
    var urls = [];
    data.nodes.forEach(function (n) {
      if (n.img && urls.indexOf(n.img) < 0) { urls.push(n.img); }
    });
    pending.push(Promise.all(urls.map(function (u) {
      return new Promise(function (done) {
        var img = new Image();
        img.onload = img.onerror = done;   // 解码失败也要放行，别卡死就绪旗
        img.src = u;
      });
    })).then(function () {
      var graph = new G6.Graph({
        container: el, width: width, height: height,
        // 坐标是 layout 算好的：不许自适应、不许居中、不许动画
        fitView: false, fitCenter: false, animate: false,
        modes: { default: [] },
        defaultNode: {
          type: 'image', size: 64,
          labelCfg: { position: 'bottom', style: { fontSize: 16, fill: '#0f172a',
            fontFamily: __G6_FONT__ } },
        },
        defaultEdge: { type: 'line', style: { lineWidth: 3, stroke: '#db2777',
          endArrow: false } },
      });
      graph.data(data);
      graph.render();
    }));
  }
"""


_SCRIPT: Final[str] = (
    "<script>"
    + _LIB_TOKEN
    + "</script>\n<script>\n(function () {\n"
    + "  window.__ready = false;\n"
    + "  var pending = [];\n"
    + _HELPERS.replace("__G6_FONT__", json.dumps(FONT_STACK))
    + _CALLS_TOKEN
    + "\n"
    + "  Promise.all(pending).then(function () {\n"
    + "    requestAnimationFrame(function () {\n"
    + "      requestAnimationFrame(function () { window.__ready = true; });\n"
    + "    });\n"
    + "  });\n"
    + "})();\n</script>"
)


def _render_call(index: int, cluster: ClusterLayout, data: Mapping[str, Any]) -> str:
    """一个簇的初始化调用（宽高 = 簇宽高，坐标 1:1）。"""
    return (
        f"  render({json.dumps(container_id(index))}, {_payload(data)}, "
        f"{cluster.width}, {cluster.height});"
    )


@lru_cache(maxsize=1)
def read_g6_js() -> str | None:
    """读入 `assets/g6.min.js`（进程内只读一次）；找不到返回 `None`。

    htmlrender 走 `set_content`，**相对路径的 `<script src>` 不生效** —— 必须内联。
    候选路径覆盖「源码树里跑」（`src/plugins/nonebot_plugin_waifu/graph_g6.py` →
    仓库根）与「以仓库根为工作目录跑」两种情形。
    """
    here = pathlib.Path(__file__).resolve()
    candidates = [*(p / G6_ASSET for p in here.parents[:4]), pathlib.Path(G6_ASSET)]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


def build_scripts(
    clusters: Sequence[ClusterLayout],
    *,
    nicknames: Mapping[int, str],
    avatars: Mapping[int, str | None],
    colors: Mapping[str, str],
    widths: Mapping[str, int],
    labels: Mapping[str, str],
    g6_js: str | None = None,
) -> str:
    """全部簇的 `<script>`（g6.min.js 内联**一次** + 每簇一段初始化）。

    脚本自己置就绪旗（`window.__ready`）：G6 是异步渲染的，不等就截图会得到空图。
    资产缺失 / JS 抛错时旗子不会置位 → `render._shot` 等超时 → 回退纯文本。
    """
    lib = g6_js if g6_js is not None else read_g6_js()
    if lib is None:
        raise RuntimeError(_MISSING_ASSET)
    calls = "\n".join(
        _render_call(
            index,
            cluster,
            cluster_data(
                cluster,
                index=index,
                nicknames=nicknames,
                avatars=avatars,
                colors=colors,
                widths=widths,
                labels=labels,
            ),
        )
        for index, cluster in enumerate(clusters, start=1)
    )
    return _SCRIPT.replace(_CALLS_TOKEN, calls).replace(_LIB_TOKEN, lib)


__all__ = [
    "ANCHOR_SIZE",
    "CAPSULE_PAD",
    "CHIP_FONT",
    "CHIP_RADIUS",
    "FONT_STACK",
    "G6_ASSET",
    "G6_BYTES",
    "G6_SHA256",
    "NAME_FG",
    "NAME_FONT",
    "NAME_MAX",
    "NODE_SIZE",
    "PH_BG",
    "PH_FG",
    "PORT_STROKE",
    "READY_EXPR",
    "RING_COLOR",
    "RING_WIDTH",
    "anchor_id",
    "avatar_id",
    "build_scripts",
    "capsule_id",
    "capsule_node",
    "capsule_uri",
    "chip_aabb",
    "cluster_data",
    "container_html",
    "container_id",
    "line_endpoints",
    "placeholder_uri",
    "port_id",
    "read_g6_js",
    "truncate_name",
]
