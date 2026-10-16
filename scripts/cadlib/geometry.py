"""墙体与门窗洞口的几何生成。

墙体按**中心线 + 厚度**表达：在中心线两侧各偏移 t/2 得到墙线。

绘制方式（重要）
----------------
墙面线**不是**一面墙一面墙各画各的，而是交给 `outline.build_wall_polylines`：
把所有墙矩形做多边形并集、减去门窗洞口、追踪边界，产出**连续的多段线**。
这样才能得到人工画图的效果：

    - 外墙外边线是一条闭合多段线，转角自然成角；
    - 房间内轮廓是连续多段线，T 形接头处自动断开而不是互相穿插；
    - 洞口处的封口线由并集边界自然产生，不需要事后硬补；
    - 转角不再出现两条墙互相重叠的多余线。

图例部分（门扇、开启弧线、窗玻璃线）单独补画，因为它们不是墙体边界。

已知限制（须在交付说明中向用户讲清）：
    - 门扇开启方向目前由 swing 决定铰接侧，且固定朝墙段**左法向**一侧开启。
      真实图纸需要「门开向哪个房间」的信息，本骨架版本未纳入，属已知待扩展点。
"""

from __future__ import annotations

import math

from .outline import build_wall_polylines


def _unit(p, q):
    dx, dy = float(q[0]) - float(p[0]), float(q[1]) - float(p[1])
    length = math.hypot(dx, dy)
    if length <= 0:
        raise ValueError(f"墙段长度为零: {p} -> {q}")
    return dx / length, dy / length, length


def _resolve_openings(walls, openings, ctx):
    """把 openings[].wall（下标或 id 字符串）统一解析为墙下标。"""
    id_to_index = {}
    for i, w in enumerate(walls):
        if w.get("id"):
            id_to_index[w["id"]] = i

    by_wall = {}
    for op in openings:
        ref = op["wall"]
        if isinstance(ref, str):
            if ref not in id_to_index:
                ctx.warn(f"洞口引用了不存在的墙 id: {ref}，已跳过")
                continue
            idx = id_to_index[ref]
        else:
            idx = int(ref)
        if idx < 0 or idx >= len(walls):
            ctx.warn(f"洞口引用了越界的墙下标 {idx}，已跳过")
            continue
        by_wall.setdefault(idx, []).append(op)
    return by_wall


def build_wall_plan(walls, openings, ctx):
    """纯计算：返回每面墙的实墙子段与洞口区间，供绘制与校验共用。

    返回 list[dict]，元素含：
        wall, layer, p, q, u, n, thickness, length,
        solids: [(a, b), ...]            实墙段（沿中心线的参数区间）
        voids:  [(a, b, opening), ...]   洞口区间
    单位：模型空间 mm。

    solids 不直接用于出图（墙面线由 outline 的并集边界给出），保留供校验与调试。
    """
    by_wall = _resolve_openings(walls, openings, ctx)
    plan = []

    for wi, w in enumerate(walls):
        p, q = w["start"], w["end"]
        t = float(w["thickness"])
        ux, uy, L = _unit(p, q)
        nx, ny = -uy, ux  # 左法向
        layer = "WALL-STR" if w.get("type", "partition") == "structural" else "WALL-PAR"

        voids = []
        for op in by_wall.get(wi, []):
            c, wd = float(op["offset"]), float(op["width"])
            a, b = c - wd / 2.0, c + wd / 2.0
            if a < -1e-6 or b > L + 1e-6:
                ctx.warn(
                    f"墙 {wi} 上的洞口 [{op.get('label', op['kind'])}] 超出墙段范围"
                    f"（洞 {a:.0f}~{b:.0f}，墙长 {L:.0f}）；已裁剪到墙段内"
                )
                a, b = max(0.0, a), min(L, b)
            if b - a <= 1e-6:
                ctx.warn(f"墙 {wi} 上的洞口宽度被裁剪为 0，已忽略")
                continue
            voids.append((a, b, op))

        voids.sort(key=lambda v: v[0])
        for i in range(1, len(voids)):
            if voids[i][0] < voids[i - 1][1] - 1e-6:
                ctx.warn(
                    f"墙 {wi} 上洞口重叠："
                    f"[{voids[i-1][0]:.0f},{voids[i-1][1]:.0f}] 与 "
                    f"[{voids[i][0]:.0f},{voids[i][1]:.0f}]"
                )

        solids, cur = [], 0.0
        for a, b, _ in voids:
            if a > cur + 1e-6:
                solids.append((cur, a))
            cur = max(cur, b)
        if cur < L - 1e-6:
            solids.append((cur, L))

        plan.append({
            "wall": wi, "layer": layer, "p": p, "q": q,
            "u": (ux, uy), "n": (nx, ny), "thickness": t, "length": L,
            "solids": solids, "voids": voids,
        })
    return plan


def _pt(entry, s, off):
    """墙上参数 s、法向偏移 off 处的点。"""
    px, py = float(entry["p"][0]), float(entry["p"][1])
    ux, uy = entry["u"]
    nx, ny = entry["n"]
    return (px + ux * s + nx * off, py + uy * s + ny * off)


# ---------------------------------------------------------------- 绘制
def draw_walls(ctx, plan) -> None:
    """绘制墙体轮廓（连续多段线）与门窗图例。"""
    msp = ctx.msp

    # 1) 墙体轮廓：一笔成型的连续多段线，含洞口封口线
    for layer, pts, closed in build_wall_polylines(plan, ctx.warn):
        msp.add_lwpolyline(
            [(float(p[0]), float(p[1])) for p in pts],
            close=bool(closed),
            dxfattribs={"layer": layer},
        )

    # 2) 门窗图例：不参与墙体布尔运算，单独补画
    for entry in plan:
        t = entry["thickness"]
        for a, b, op in entry["voids"]:
            kind = op.get("kind", "opening")
            if kind == "window":
                _draw_window_glass(ctx, entry, a, b, t)
            elif kind == "door":
                _draw_door(ctx, entry, a, b, t, op.get("swing", "left"))
            # kind == "opening"：墙体已断开，不补图例


def _draw_window_glass(ctx, entry, a, b, t):
    """窗图例：洞口内画四条等距细实线。

    依据 GB/T 50104 窗图例与通行画法（「四条等距细实线绘制窗轮廓，
    贴合墙体两侧边线居中布置」）：
        两条与墙面平齐（窗台线，线宽改细），
        两条在墙厚三分点处（玻璃线）。
    墙的粗线在洞口处已由墙体轮廓断开，这里补齐窗自身的四条细线。

    注：门窗轮廓线按 GB/T 50104 取 0.7b（用户 2026-10-16 确认，见 references/layers.md）。
    """
    msp = ctx.msp
    for off in (t / 2.0, t / 6.0, -t / 6.0, -t / 2.0):
        msp.add_line(_pt(entry, a, off), _pt(entry, b, off),
                     dxfattribs={"layer": "WIN-DOOR"})


def _draw_door(ctx, entry, a, b, t, swing):
    """门图例：门扇线 + 90° 开启弧线。洞口两侧的封口线由墙体轮廓给出。"""
    if swing in ("none", "sliding"):
        return

    if swing == "double":
        half = (b - a) / 2.0
        for hinge_s, other_s in ((a, a + half), (b, b - half)):
            _door_leaf_and_arc(ctx, entry, hinge_s, other_s, t)
        return

    # left  -> 铰接在墙段起点侧；right -> 铰接在终点侧
    hinge_s, other_s = (a, b) if swing != "right" else (b, a)
    _door_leaf_and_arc(ctx, entry, hinge_s, other_s, t)


def _door_leaf_and_arc(ctx, entry, hinge_s, other_s, t):
    msp = ctx.msp
    nx, ny = entry["n"]
    width = abs(other_s - hinge_s)

    hinge = _pt(entry, hinge_s, t / 2.0)                 # 门轴点（墙的 +n 侧）
    leaf_end = (hinge[0] + nx * width, hinge[1] + ny * width)
    other = _pt(entry, other_s, t / 2.0)

    msp.add_line(hinge, leaf_end, dxfattribs={"layer": "WIN-DOOR"})

    a1 = math.degrees(math.atan2(ny, nx))                # 门扇方向角
    a2 = math.degrees(math.atan2(other[1] - hinge[1],
                                 other[0] - hinge[0]))   # 另一侧门樘方向角
    delta = (a2 - a1) % 360.0
    start, end = (a1, a2) if delta <= 180.0 else (a2, a1)
    msp.add_arc(center=hinge, radius=width, start_angle=start, end_angle=end,
                dxfattribs={"layer": "WIN-DOOR"})
