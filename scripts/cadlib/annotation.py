"""轴线、轴号、尺寸标注与标高。

全部实现依据 references/annotation.md（GB/T 50001 第7章、第11章）。
"""

from __future__ import annotations

import math

from ezdxf.enums import TextEntityAlignment

from . import standards as S
from .layers import LAYER_SYMBOL
from .symbols import block_for, define_symbols, elevation_text_offset

FORBIDDEN_AXIS_LETTERS = set("IOZ")


def _finalize_dim(msp, dim, layer="DIM"):
    """把尺寸的几何块内容从图层 0 改到 DIM 图层。

    尺寸实体的几何（尺寸线、尺寸界线、起止符号）由 AutoCAD/ezdxf 渲染进一个
    匿名块，块内实体默认落在图层 0、颜色与线宽都为 BYLAYER，
    于是**既拿不到 DIM 图层该有的颜色（红），也拿不到该有的线宽**；
    部分预览渲染器甚至会把图层 0 的内容当作背景色而完全看不见。
    规则要求这些线属于尺寸标注图层，这里显式归位。
    """
    try:
        blk = dim.get_geometry_block()
    except Exception:  # noqa: BLE001
        return
    if blk is None:
        return
    for e in blk:
        try:
            if e.dxf.layer == "0":
                e.dxf.layer = layer
        except AttributeError:
            continue


def _add_dim(ctx, msp, base, p1, p2, angle=0):
    """统一的线性尺寸入口：建 → 渲染 → 几何归层。"""
    dim = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle,
                             dimstyle="GB_DIM",
                             dxfattribs={"layer": "DIM"})
    dim.render()
    _finalize_dim(msp, dim)
    return dim


# ------------------------------------------------------------------ 基础工具
def add_text(ctx, at, content, height_mm, layer="TEXT",
             align=TextEntityAlignment.MIDDLE_CENTER, rotation=0.0,
             style=None):
    """在模型空间写文字。height_mm / 位置按 paper mm 语义传入时请自行 ps()。"""
    msp = ctx.msp
    attribs = {"layer": layer, "style": style or S.STYLE_NAME,
               "rotation": rotation}
    text = msp.add_text(content, height=height_mm, dxfattribs=attribs)
    text.set_placement((float(at[0]), float(at[1])), align=align)
    return text


def _check_height(ctx, height_mm, where):
    if height_mm < S.MIN_TEXT_HEIGHT - 1e-9:
        ctx.warn(f"{where}: 字高 {height_mm}mm 小于国标下限 2.5mm")
    elif abs(height_mm - _nearest(height_mm)) > 1e-6:
        ctx.warn(f"{where}: 字高 {height_mm}mm 不在国标字高系列 "
                 f"{S.TEXT_HEIGHTS} 内")


def _nearest(v):
    return min(S.TEXT_HEIGHTS, key=lambda x: abs(x - v))


# ------------------------------------------------------------------ 轴网
def draw_axes(ctx, grid, bbox) -> None:
    """绘制轴线（细点划线）与轴号圆圈（直径 8mm，细实线）。"""
    msp = ctx.msp
    xs = [float(v) for v in (grid.get("x") or [])]
    ys = [float(v) for v in (grid.get("y") or [])]
    if not xs and not ys:
        return

    ext_mm = float(grid.get("extend", S.AXIS_EXTEND))
    if ext_mm < S.AXIS_EXTEND_MIN - 1e-6:
        ctx.warn(f"grid.extend = {ext_mm}mm 小于安全值 {S.AXIS_EXTEND_MIN}mm，"
                 f"轴号会与最外侧尺寸链相撞，已自动提升")
        ext_mm = S.AXIS_EXTEND_MIN
    ext = ctx.ps(ext_mm)  # extend 为图纸毫米
    radius = ctx.ps(float(grid.get("bubble_radius", S.AXIS_BUBBLE_DIA / 2.0)))
    text_h = ctx.ps(S.AXIS_BUBBLE_TEXT)
    font_h = text_h  # 轴号用同样字高，宽度因子由文字样式统一控制

    xmin, ymin, xmax, ymax = bbox
    x0, x1 = xmin - ext, xmax + ext
    y0, y1 = ymin - ext, ymax + ext

    x_labels = grid.get("x_labels") or [str(i + 1) for i in range(len(xs))]
    if grid.get("y_labels"):
        y_labels = list(grid["y_labels"])
    else:
        if len(ys) > len(S.LETTERS):
            ctx.warn(f"纵向轴线 {len(ys)} 条超出可用字母数 {len(S.LETTERS)}，"
                     f"请显式提供 y_labels")
        y_labels = [S.LETTERS[i] if i < len(S.LETTERS) else f"?{i}"
                    for i in range(len(ys))]

    for lab in x_labels + y_labels:
        if any(ch in FORBIDDEN_AXIS_LETTERS for ch in str(lab).upper()):
            ctx.warn(f"轴号「{lab}」含 I / O / Z，国标禁止使用（易与 1/0/2 混淆）")

    # 横向轴线：数字，从左到右
    for x, lab in zip(xs, x_labels):
        msp.add_line((x, y0 + radius), (x, y1 - radius),
                     dxfattribs={"layer": "AXIS"})
        for cy in (y0, y1):
            # 轴号圆圈必须是**细实线圆**，不能继承 AXIS 图层的 CENTER 点划线型，
            # 否则圆圈会被画成点划圆（审图意见 #6）。
            msp.add_circle((x, cy), radius,
                           dxfattribs={"layer": "AXIS",
                                       "linetype": "CONTINUOUS"})
            add_text(ctx, (x, cy), str(lab), font_h, layer="AXIS")

    # 纵向轴线：字母，从下到上
    for y, lab in zip(ys, y_labels):
        msp.add_line((x0 + radius, y), (x1 - radius, y),
                     dxfattribs={"layer": "AXIS"})
        for cx in (x0, x1):
            msp.add_circle((cx, y), radius,
                           dxfattribs={"layer": "AXIS",
                                       "linetype": "CONTINUOUS"})
            add_text(ctx, (cx, y), str(lab), font_h, layer="AXIS")


# ------------------------------------------------------------------ 剖切符号
_SECTION_DIRS = {
    "down":  (0.0, -1.0),
    "up":    (0.0, 1.0),
    "left":  (-1.0, 0.0),
    "right": (1.0, 0.0),
}


def draw_section_marks(ctx, sections) -> None:
    """剖切符号（GB/T 50001 第7.1节 符号 · 剖切符号）。

    剖切符号由**剖切位置线**与**剖视方向线**组成，两者垂直：
        - 剖切位置线：粗实线，长度 6~10mm；
        - 剖视方向线：粗实线，长度 4~6mm，由位置线指向剖视方向；
        - 编号（阿拉伯数字）注写在剖视方向线的端部。

    一个完整的剖面需要**两个剖切符号**（剖切线的两端各一个，编号相同），
    spec 的 `sections` 里各写成一条即可。

    结构：
        { "at": [x, y], "direction": "down|up|left|right", "label": "1" }
    其中 direction 表示**剖视方向**（方向线指向的一侧）。
    剖切符号是图纸要素、不属于建筑本体，用实际大小绘制，不随出图比例缩放。
    """
    msp = ctx.msp
    pos_len = ctx.ps(S.SECTION_POS_LEN)      # 位置线长，图纸毫米
    dir_len = ctx.ps(S.SECTION_DIR_LEN)      # 方向线长，图纸毫米
    lw = ctx.lw(1.0)                         # 粗实线 b

    for i, sec in enumerate(sections or []):
        at = sec.get("at")
        d = str(sec.get("direction", "down")).lower()
        if not at or len(at) < 2:
            ctx.warn(f"第 {i+1} 个剖切符号缺少 at 坐标，已跳过")
            continue
        if d not in _SECTION_DIRS:
            ctx.warn(f"剖切符号方向 {d!r} 非法（只支持 down/up/left/right），已跳过")
            continue
        px, py = float(at[0]), float(at[1])
        vx, vy = _SECTION_DIRS[d]
        ux, uy = -vy, vx                    # 与剖视方向垂直 → 位置线方向

        msp.add_line((px - ux * pos_len / 2.0, py - uy * pos_len / 2.0),
                     (px + ux * pos_len / 2.0, py + uy * pos_len / 2.0),
                     dxfattribs={"layer": LAYER_SYMBOL, "lineweight": lw})
        msp.add_line((px, py), (px + vx * dir_len, py + vy * dir_len),
                     dxfattribs={"layer": LAYER_SYMBOL, "lineweight": lw})

        add_text(ctx, (px + vx * (dir_len + ctx.ps(3.5)),
                       py + vy * (dir_len + ctx.ps(3.5))),
                 str(sec.get("label", i + 1)),
                 ctx.ps(S.HEIGHT_NOTE), layer="TEXT")


# ------------------------------------------------------------------ 尺寸标注
def _wall_perimeter_sides(entry, bbox, tol=1e-3):
    """判断一面墙贴住了建筑外包框的哪几条边。

    外围尺寸链只能标注**该侧外墙上的洞口**。早期实现按「墙在中线左侧还是右侧」
    粗略归属，结果内墙洞口被同时算进两侧的外围尺寸链（重复标注），
    还会切出 30mm、200mm 这种连尺寸数字都放不下的碎段。
    """
    xmin, ymin, xmax, ymax = bbox
    ux, uy = entry["u"]
    nx, ny = entry["n"]
    t = float(entry["thickness"]) / 2.0
    L = float(entry["length"])
    px, py = float(entry["p"][0]), float(entry["p"][1])

    ys = (py + ny * t, py - ny * t)
    xs = (px + nx * t, px - nx * t)
    if abs(ux) > 1e-6:                       # 位置随长度变化的那个坐标
        xs = (px + ux * L + nx * t, px + ux * L - nx * t)
    if abs(uy) > 1e-6:
        ys = (py + uy * L + ny * t, py + uy * L - ny * t)

    sides = set()
    if abs(uy) < 1e-6:                       # 水平墙
        if min(ys) <= ymin + tol:
            sides.add("bottom")
        if max(ys) >= ymax - tol:
            sides.add("top")
    if abs(ux) < 1e-6:                       # 竖直墙
        if min(xs) <= xmin + tol:
            sides.add("left")
        if max(xs) >= xmax - tol:
            sides.add("right")
    return sides


def _opening_edges(plan, want_horizontal, bbox, sides, keep_low=True, tol=1e-3):
    """取洞口边界坐标。

    want_horizontal=True  取水平墙上的洞口，返回其 X 边界
    want_horizontal=False 取竖直墙上的洞口，返回其 Y 边界
    只有贴在 sides 指定边上的墙才参与（即外墙），内墙洞口交给内尺寸链。
    """
    pts = set()
    for entry in plan:
        ux, uy = entry["u"]
        horizontal = abs(uy) < 1e-6
        if horizontal != want_horizontal:
            continue
        if not (_wall_perimeter_sides(entry, bbox, tol) & sides):
            continue
        for a, b, _ in entry["voids"]:
            for s in (a, b):
                if want_horizontal:
                    pts.add(round(float(entry["p"][0]) + ux * s, 3))
                else:
                    pts.add(round(float(entry["p"][1]) + uy * s, 3))
    return sorted(pts)


def _chain_segments(points):
    points = sorted(set(points))
    return [(points[i], points[i + 1]) for i in range(len(points) - 1)]


def _dedup(points):
    return sorted(set(round(float(p), 3) for p in points))


def draw_dimensions(ctx, spec, plan, bbox) -> None:
    """按 GB/T 50001 第11章绘制尺寸链。

    三道尺寸线的排布顺序（自轮廓向外）：细部 → 轴线 → 总尺寸，
    偏移量依次为 10mm、17mm、24mm（第一道 10mm，其后每道加 7mm）。
    """
    grid = spec.get("grid") or {}
    xs = _dedup(grid.get("x") or [])
    ys = _dedup(grid.get("y") or [])

    dims_cfg = spec.get("dimensions") or {}
    if dims_cfg.get("auto", True):
        side = dims_cfg.get("side", "bottom_left")
        sides = {"bottom", "top", "left", "right"} if side == "all" \
            else set(side.split("_"))

        if "bottom" in sides:
            _horizontal_chains(ctx, xs, plan, bbox, {"bottom"},
                               outward=-1.0, grid_positions=xs)
        if "top" in sides:
            _horizontal_chains(ctx, xs, plan, bbox, {"top"},
                               outward=+1.0, grid_positions=xs)
        if "left" in sides:
            _vertical_chains(ctx, ys, plan, bbox, {"left"},
                             outward=-1.0, grid_positions=ys)
        if "right" in sides:
            _vertical_chains(ctx, ys, plan, bbox, {"right"},
                             outward=+1.0, grid_positions=ys)

    if dims_cfg.get("interior_openings", True):
        _interior_chains(ctx, plan, bbox)
    if dims_cfg.get("wall_thickness", True):
        # 墙厚用引出线标注（审图意见 #9）。不开内部跨墙尺寸链 —— 那会让
        # 每一侧多出一条线，加重审图意见 #14 的「尺寸道数过多」观感。
        _wall_thickness_notes(ctx, plan, bbox)

    _draw_explicit(ctx, dims_cfg.get("explicit") or [])


def _offsets(ctx, count):
    """返回 count 道尺寸线相对轮廓的距离（模型空间单位）。"""
    return [ctx.ps(S.DIM_OFFSET_FIRST + S.DIM_OFFSET_STEP * i)
            for i in range(count)]


def _horizontal_chains(ctx, xs, plan, bbox, sides, outward, grid_positions):
    msp = ctx.msp
    ymin, ymax = bbox[1], bbox[3]
    edge = ymin if outward < 0 else ymax
    detail = _opening_edges(plan, True, bbox, sides)

    chains = []
    if detail and grid_positions:
        # 细部尺寸链在最外两道轴线之间细分洞口位置。
        # 不把建筑外包面（轴线 ± 墙厚/2）并入锚点 —— 那会切出 120mm 这类碎段，
        # 尺寸文字在 1:50 下放不进去，会与旁边的尺寸界线互相压线。
        lo, hi = min(grid_positions), max(grid_positions)
        inner = [p for p in detail if lo + 1e-6 < p < hi - 1e-6]
        if inner:
            chains.append(_chain_segments([lo] + inner + [hi]))
    if grid_positions:
        chains.append(_chain_segments(grid_positions))
    chains.append([(bbox[0], bbox[2])])          # 总尺寸取外包尺寸

    offs = _offsets(ctx, len(chains))
    for segs, off in zip(chains, offs):
        y_base = edge + outward * off
        for a, b in segs:
            if b - a < 1e-6:
                continue
            _add_dim(ctx, msp, ((a + b) / 2.0, y_base), (a, edge), (b, edge))


def _vertical_chains(ctx, ys, plan, bbox, sides, outward, grid_positions):
    msp = ctx.msp
    xmin, xmax = bbox[0], bbox[2]
    edge = xmin if outward < 0 else xmax
    detail = _opening_edges(plan, False, bbox, sides)

    chains = []
    if detail and grid_positions:
        lo, hi = min(grid_positions), max(grid_positions)
        inner = [p for p in detail if lo + 1e-6 < p < hi - 1e-6]
        if inner:
            chains.append(_chain_segments([lo] + inner + [hi]))
    if grid_positions:
        chains.append(_chain_segments(grid_positions))
    chains.append([(bbox[1], bbox[3])])

    offs = _offsets(ctx, len(chains))
    for segs, off in zip(chains, offs):
        x_base = edge + outward * off
        for a, b in segs:
            if b - a < 1e-6:
                continue
            _add_dim(ctx, msp, (x_base, (a + b) / 2.0), (edge, a), (edge, b),
                     angle=90)


def _interior_chains(ctx, plan, bbox):
    """内墙洞口的内尺寸链。

    外围三道尺寸只标注外墙上的洞口；内墙（隔墙）上的门洞必须另有尺寸依据。
    这里贴着每面有洞口的**内墙**画一道紧凑尺寸链，标注洞口相对墙端的定位。

    尺寸界线的起点取在**墙面**上、链轴距墙面 INTERIOR_CHAIN_OFFSET mm，
    与国标「第一道尺寸线距轮廓 10mm」用同一口径，自检才能按同一规则校验。
    """
    msp = ctx.msp
    off = ctx.ps(S.INTERIOR_CHAIN_OFFSET)

    for entry in plan:
        voids = entry["voids"]
        if not voids:
            continue
        if _wall_perimeter_sides(entry, bbox):   # 外墙洞口已在外围链上，不重复
            continue
        ux, uy = entry["u"]
        nx, ny = entry["n"]
        t = float(entry["thickness"])
        L = float(entry["length"])
        px, py = float(entry["p"][0]), float(entry["p"][1])
        face = t / 2.0                            # 尺寸界线起点：墙面
        axis = face + off                         # 链轴距中心线

        pts = _dedup([0.0, L]
                     + [a for a, _, _ in voids]
                     + [b for _, b, _ in voids])
        for a, b in zip(pts, pts[1:]):
            if b - a < 1e-6:
                continue
            if abs(uy) < 1e-6:            # 水平内墙
                x1, x2 = px + ux * a, px + ux * b
                y_face = py + ny * face
                y_base = py + ny * axis
                _add_dim(ctx, msp, ((x1 + x2) / 2.0, y_base),
                         (x1, y_face), (x2, y_face))
            elif abs(ux) < 1e-6:          # 竖直内墙
                y1, y2 = py + uy * a, py + uy * b
                x_face = px + nx * face
                x_base = px + nx * axis
                _add_dim(ctx, msp, (x_base, (y1 + y2) / 2.0),
                         (x_face, y1), (x_face, y2), angle=90)
            else:
                ctx.warn("斜向内墙的洞口内尺寸链尚未实现，请用 dimensions.explicit 手工补")



def _draw_explicit(ctx, items):
    msp = ctx.msp
    for item in items:
        axis = item["axis"]
        at = float(item["at"])
        for seg in item["segments"]:
            p1, p2 = seg["from"], seg["to"]
            if axis == "horizontal":
                p1, p2 = (p1[0], at), (p2[0], at)
                base = ((p1[0] + p2[0]) / 2.0, at)
                ang = 0
            elif axis == "vertical":
                p1, p2 = (at, p1[1]), (at, p2[1])
                base = (at, (p1[1] + p2[1]) / 2.0)
                ang = 90
            else:  # aligned
                base = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
                ang = math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0]))
            _add_dim(ctx, msp, base, p1, p2, angle=ang)


# ------------------------------------------------------------------ 标高
def _wall_thickness_notes(ctx, plan, bbox) -> None:
    """墙体厚度标注（GB/T 50104：平面图应标注墙厚）。

    做法：**每种墙厚取一面代表性墙，用引出线标注一次**，而不是给每面墙都画
    一条跨墙尺寸——240mm 的墙在 1:100 下只有 2.4mm 宽，尺寸数字根本放不进去，
    画一堆挤在一起的短尺寸只会让图面更乱。

    引出线从墙的外皮引向文字「墙厚240」，放在 -n 侧；文字落在建筑内部时
    有可能与房间内容接近，因此同一种墙厚只标一次。
    """
    msp = ctx.msp
    seen = {}
    for e in plan:
        t = round(float(e["thickness"]), 1)
        seen.setdefault(t, e)

    for t, e in sorted(seen.items(), reverse=True):
        # 取该墙无洞口的最长实墙段的中点作引出点
        segs = []
        cur = 0.0
        for a, b, _ in e["voids"]:
            if a > cur + 1e-6:
                segs.append((cur, a))
            cur = max(cur, b)
        L = float(e["length"])
        if cur < L - 1e-6:
            segs.append((cur, L))
        if not segs:
            segs = [(0.0, L)]
        a, b = max(segs, key=lambda s: s[1] - s[0])
        mid = (a + b) / 2.0

        nx, ny = e["n"]
        px = float(e["p"][0]) + e["u"][0] * mid + nx * (t / 2.0)
        py = float(e["p"][1]) + e["u"][1] * mid + ny * (t / 2.0)
        tx = px + nx * ctx.ps(18.0)
        ty = py + ny * ctx.ps(18.0)

        msp.add_line((px, py), (tx, ty),
                     dxfattribs={"layer": "DIM", "lineweight": ctx.lw(0.13)})
        add_text(ctx, (tx, ty), f"墙厚{int(round(t))}",
                 ctx.ps(S.HEIGHT_DIM), layer="TEXT")


def draw_elevations(ctx, elevations, bbox) -> None:
    """绘制标高符号。

    数字单位米，格式 ±0.000 / 3.000 / -0.450。
    `type: "outdoor"` 的标高点用**涂黑**等腰直角三角形，与室内楼地面标高区分
    —— 一层平面图需要表达室内外高差（GB/T 50104）。
    """
    if not elevations:
        return
    define_symbols(ctx)
    msp = ctx.msp
    # 引出线默认向右；只有靠近右边界时才翻到左侧，避免文字压到图外或邻室
    flip_x = bbox[0] + 0.75 * (bbox[2] - bbox[0])

    for item in elevations:
        at = (float(item["at"][0]), float(item["at"][1]))
        value = float(item["value"])
        outdoor = str(item.get("type", "")).lower() in ("outdoor", "outdoor_ground")
        block = block_for(outdoor, at[0] < flip_x)
        msp.add_blockref(block, at)
        dx, dy = elevation_text_offset(ctx, block)
        add_text(ctx, (at[0] + dx, at[1] + dy),
                 S.format_elevation(value),
                 ctx.ps(S.ELEV_TEXT_HEIGHT), layer="TEXT")
