"""墙体轮廓的连续化生成。

问题背景
--------
人工画 CAD 是「用直线命令从一点连到一点，一笔画出连贯的外轮廓」，转角自然成角，
一段墙的墙面线通常就是一整条多段线。早期实现是「每面墙各自画自己那两条面线」，
后果是：

    - 转角处两条墙的线互相交叉、重叠，图上出现多余的线；
    - 一段墙面被切成很多短 LINE，CAD 里点一下只选中一小段，改不动；
    - 洞口处的封口线是硬补上去的，和墙面线接不上。

本模块的解法
------------
把所有墙体矩形做 **多边形并集**，再对并集区域做 **边界追踪**，得到的每条边
都是一条连续多段线。等价于人工做法：

    外墙矩形 ∪ 内墙矩形 − 门窗洞口矩形  的边界
    = 外墙外边线（一条闭合多段线）+ 房间内轮廓（若干条连续多段线）

算法（不依赖任何第三方几何库）
------------------------------
1. 收集所有多边形的边，并在所有交点处打断，得到「原子线段」；
2. 对每条原子线段取中点、沿法向两侧各偏移 ε，判定两侧是否在墙体实体内
   （在任一墙矩形内，且不在任何洞口矩形内）；
3. 两侧不同 → 该线段是并集边界；两侧相同 → 内部线或外部线，丢弃；
4. 把边界线段接成闭环；
5. 合并共线且同图层的相邻线段，按图层切成若干条多段线输出。

单位：全部为模型空间毫米。本模块是纯函数，不含任何随机或时间依赖。
"""

from __future__ import annotations

import math
from collections import defaultdict

# 图层优先级：一条边界线段可能同时来自多个多边形，取优先级最高的图层
LAYER_PRIORITY = ("WIN-DOOR", "WALL-STR", "WALL-PAR")

# 判定的法向偏移量（mm）。远大于浮点误差，又远小于任何真实构造尺寸。
EPS = 0.05

# 坐标吸附精度：小数点后 3 位 = 1 微米。
# 打断线段时用参数插值算交点，会把 4950 算成 4949.999998 这种带浮点尾巴的值；
# 若不吸附，同一个几何点会被当成两个图论节点，接环必然断链。
# 1 微米远低于任何施工尺寸精度（最小构造尺寸也是毫米级），吸附无副作用。
SNAP = 3


def _snap(p):
    """把坐标吸附到 0.001mm 网格。所有进入图论运算的点都必须先过这一步。"""
    return (round(float(p[0]), SNAP), round(float(p[1]), SNAP))


# ---------------------------------------------------------------- 基础运算
def _k(p):
    """坐标转可哈希的键。与 _snap 用同一精度，保证同一几何点得到同一个键。"""
    return _snap(p)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _mul(a, s):
    return (a[0] * s, a[1] * s)


def _lerp(p, q, t):
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _dist(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _point_in_convex(p, poly):
    """凸多边形内外判定（射线法的叉积版）。所有多边形都是矩形，必然凸。"""
    sign = 0
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        if abs(cross) < 1e-12:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


# ---------------------------------------------------------------- 矩形构造
def _corner(p, u, n, along, offset):
    """沿 u 走 along、沿法向 n 偏 offset 的点。"""
    return (p[0] + u[0] * along + n[0] * offset,
            p[1] + u[1] * along + n[1] * offset)


def _point_on_centerline(pt, other, tol=1e-3):
    """点是否落在另一面墙的中心线线段上（含端点）。"""
    p = (float(other["p"][0]), float(other["p"][1]))
    u = other["u"]
    L = float(other["length"])
    q = (p[0] + u[0] * L, p[1] + u[1] * L)
    d = _sub(q, p)
    length2 = d[0] * d[0] + d[1] * d[1]
    if length2 < 1e-12:
        return _dist(pt, p) <= tol
    t = ((pt[0] - p[0]) * d[0] + (pt[1] - p[1]) * d[1]) / length2
    if t < -1e-9 or t > 1.0 + 1e-9:
        return False
    foot = (p[0] + d[0] * t, p[1] + d[1] * t)
    return _dist(foot, pt) <= tol


def _end_extensions(entry, plan):
    """某一端与别的墙相接时，需要沿轴向再延伸多少。

    如果只按中心线长度做矩形，两条墙在转角处各差半个墙厚，会留下一个
    t/2 × t/2 的方形缺口，边界追踪就会绕着这个缺口走，轮廓自然不连贯。
    人工画图会把墙延伸到位；这里等价地：端点落在另一面墙中心线上（含端点）时，
    向外延伸那面墙的半厚，交由并集运算把多余部分吃掉。
    """
    p = (float(entry["p"][0]), float(entry["p"][1]))
    u = entry["u"]
    L = float(entry["length"])
    ends = [p, (p[0] + u[0] * L, p[1] + u[1] * L)]

    out = []
    for pt in ends:
        ext = 0.0
        for other in plan:
            if other is entry:
                continue
            if _point_on_centerline(pt, other):
                ext = max(ext, float(other["thickness"]) / 2.0)
        out.append(ext)
    return out[0], out[1]


def wall_rect(entry, plan=None):
    """整面墙的矩形（含洞口位置在内），逆时针四点。

    传入 plan 时，两端与别的墙相接处会自动延伸半厚，把转角填实。
    """
    p = (float(entry["p"][0]), float(entry["p"][1]))
    u = entry["u"]
    n = entry["n"]
    t = float(entry["thickness"]) / 2.0
    L = float(entry["length"])

    a0, a1 = 0.0, 0.0
    if plan is not None:
        a0, a1 = _end_extensions(entry, plan)

    return [_corner(p, u, n, -a0, t), _corner(p, u, n, -a0, -t),
            _corner(p, u, n, L + a1, -t), _corner(p, u, n, L + a1, t)]


def void_rect(entry, a, b):
    """洞口矩形，沿墙向覆盖 [a, b]，横向贯通整个墙厚。"""
    p = (float(entry["p"][0]), float(entry["p"][1]))
    u = entry["u"]
    n = entry["n"]
    t = float(entry["thickness"]) / 2.0
    return [_corner(p, u, n, a, t), _corner(p, u, n, a, -t),
            _corner(p, u, n, b, -t), _corner(p, u, n, b, t)]


# ---------------------------------------------------------------- 第一步：打断
def _collect_edges(polys):
    """把每个多边形拆成带图层标签的有向边，端点统一吸附到网格。"""
    segs = []
    for pts, layer, role in polys:
        n = len(pts)
        for i in range(n):
            a, b = _snap(pts[i]), _snap(pts[(i + 1) % n])
            if a != b:
                segs.append((a, b, layer, role))
    return segs


def _seg_intersection_params(p, q, r, s):
    """线段 pq 与 rs 的交点参数 t（在 pq 上）；平行或不相交返回 []。"""
    d1 = _sub(q, p)
    d2 = _sub(s, r)
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denom) < 1e-12:
        return []
    rp = _sub(r, p)
    t = (rp[0] * d2[1] - rp[1] * d2[0]) / denom
    u = (rp[0] * d1[1] - rp[1] * d1[0]) / denom
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return [min(max(t, 0.0), 1.0)]
    return []


def _param_of_point_on_seg(p, q, pt):
    """点 pt 落在线段 pq 上时返回其参数，否则 None。"""
    d = _sub(q, p)
    length2 = d[0] * d[0] + d[1] * d[1]
    if length2 < 1e-18:
        return None
    t = ((pt[0] - p[0]) * d[0] + (pt[1] - p[1]) * d[1]) / length2
    if t < -1e-9 or t > 1.0 + 1e-9:
        return None
    foot = _lerp(p, q, t)
    if _dist(foot, pt) > 1e-7:
        return None
    return min(max(t, 0.0), 1.0)


def split_all(segs):
    """在所有交点处打断线段，返回原子线段列表。"""
    out = []
    for i, (p, q, layer, role) in enumerate(segs):
        ts = [0.0, 1.0]
        for j, (r, s, _, _) in enumerate(segs):
            if i == j:
                continue
            ts.extend(_seg_intersection_params(p, q, r, s))
            for pt in (r, s):
                t = _param_of_point_on_seg(p, q, pt)
                if t is not None:
                    ts.append(t)
        ts = sorted({round(t, 9) for t in ts})
        for t0, t1 in zip(ts, ts[1:]):
            if t1 - t0 < 1e-9:
                continue
            pa, pb = _snap(_lerp(p, q, t0)), _snap(_lerp(p, q, t1))
            if pa == pb:
                continue
            out.append((pa, pb, layer, role))
    return out


def dedupe(segs):
    """合并重合线段：同一对端点只保留一条，图层标签取优先级最高的。"""
    table = {}
    for p, q, layer, role in segs:
        kp, kq = _k(p), _k(q)
        key = (kp, kq) if kp <= kq else (kq, kp)
        entry = table.get(key)
        if entry is None:
            table[key] = [p, q, {layer}, role]
        else:
            entry[2].add(layer)
    return [(v[0], v[1], frozenset(v[2]), v[3]) for v in table.values()]


# ---------------------------------------------------------------- 第二步：判边界
def _inside(p, adds, subs):
    """点是否落在墙体实体内：在任一墙矩形内，且不在任何洞口矩形内。"""
    if any(_point_in_convex(p, poly) for poly, _ in adds):
        return not any(_point_in_convex(p, poly) for poly, _ in subs)
    return False


def pick_boundary(segs, adds, subs):
    """保留两侧内外性不同的原子线段，即并集/差集的边界。"""
    keep = []
    for p, q, layers, role in segs:
        length = _dist(p, q)
        if length < 1e-6:
            continue
        eps = min(EPS, length / 3.0)
        mid = _lerp(p, q, 0.5)
        d = _sub(q, p)
        inv = 1.0 / length
        nx, ny = -d[1] * inv, d[0] * inv
        left = _inside((mid[0] + nx * eps, mid[1] + ny * eps), adds, subs)
        right = _inside((mid[0] - nx * eps, mid[1] - ny * eps), adds, subs)
        if left != right:
            keep.append((p, q, layers, role))
    return keep


# ---------------------------------------------------------------- 第三步：接环
def _angle(v):
    return math.atan2(v[1], v[0])


def trace_loops(segs):
    """把边界线段接成闭环。返回 [(点序列, 每段图层标签), ...]。

    正常情况每个端点度为 2，走向唯一；只有多个构件恰好共点时才需要按转角选择，
    这里用「相对来向取最靠右的转向」，与人工沿内轮廓顺时针描边一致。
    """
    adj = defaultdict(list)
    for idx, (p, q, _, _) in enumerate(segs):
        adj[_k(p)].append((idx, 0))
        adj[_k(q)].append((idx, 1))

    used = [False] * len(segs)
    loops = []

    for start in range(len(segs)):
        if used[start]:
            continue
        path, layers = [], []
        idx, forward = start, 0
        node = _k(segs[start][0])
        start_node = node
        closed = False

        while True:
            used[idx] = True
            p, q, tags, _ = segs[idx]
            a, b = (p, q) if forward == 0 else (q, p)
            path.append(a)
            layers.append(tags)
            nxt = _k(b)
            if nxt == start_node:
                closed = True
                break
            cands = [(i, f) for (i, f) in adj[nxt] if not used[i]]
            if not cands:
                break
            if len(cands) == 1:
                idx, forward = cands[0]
            else:
                idx, forward = _choose_turn(segs, a, b, cands)
        loops.append((path, layers, closed))
    return loops


def _choose_turn(segs, a, b, cands):
    """在多条候选边中选「相对来向最靠右」的一条。"""
    incoming = _sub(b, a)
    back = math.atan2(-incoming[1], -incoming[0])
    best, best_delta = cands[0], None
    for idx, forward in cands:
        p, q, _, _ = segs[idx]
        other = q if forward == 0 else p
        out = _sub(other, b)
        delta = (back - _angle(out)) % (2.0 * math.pi)
        if best_delta is None or delta < best_delta:
            best, best_delta = (idx, forward), delta
    return best


# ---------------------------------------------------------------- 第四步：整理
def _eff_layer(tags):
    for name in LAYER_PRIORITY:
        if name in tags:
            return name
    return sorted(tags)[0] if tags else "WALL-PAR"


def _same_dir(d1, d2, tol=1e-6):
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    dot = d1[0] * d2[0] + d1[1] * d2[1]
    if abs(cross) > tol * max(1.0, math.hypot(*d1) * math.hypot(*d2)):
        return False
    return dot > 0


def _polyline_from(pts):
    """去掉重复点与共线中间点。"""
    seq = []
    for p in pts:
        if not seq or _dist(seq[-1], p) > 1e-7:
            seq.append(p)
    if len(seq) > 2 and _dist(seq[0], seq[-1]) < 1e-7:
        seq.pop()
    if len(seq) < 2:
        return seq
    out = [seq[0]]
    for i in range(1, len(seq) - 1):
        d1 = _sub(seq[i], out[-1])
        d2 = _sub(seq[i + 1], seq[i])
        if _same_dir(d1, d2):
            continue
        out.append(seq[i])
    out.append(seq[-1])
    return out


def merge_and_split_loop(path, layers, closed):
    """把闭环按「共线 + 同图层」合并，再按图层切成若干条多段线。

    返回 [(图层, 点序列, 是否闭合), ...]。
    整环同图层且共线可合并时，输出一条**闭合多段线**。
    """
    n = len(path)
    if n < 2:
        return []

    # 展开成 边=(起点, 终点, 图层)
    edges = []
    for i in range(n):
        a = path[i]
        b = path[(i + 1) % n] if i < n - 1 else path[0]
        edges.append((a, b, _eff_layer(layers[i])))

    if not closed:
        pts = [edges[0][0]] + [e[1] for e in edges]
        return [(edges[0][2], _polyline_from(pts), False)]

    # 闭环：先按图层切成若干段，再在每段内合并共线
    runs = []
    cur = [edges[0]]
    for e in edges[1:]:
        if e[2] == cur[-1][2]:
            cur.append(e)
        else:
            runs.append(cur)
            cur = [e]
    runs.append(cur)
    # 首尾同图层则合并
    if len(runs) > 1 and runs[0][0][2] == runs[-1][-1][2]:
        runs[0] = runs[-1] + runs[0]
        runs.pop()

    out = []
    for run in runs:
        pts = [run[0][0]] + [e[1] for e in run]
        pts = _polyline_from(pts)
        if len(pts) < 2:
            continue
        run_closed = len(run) == len(edges)
        out.append((run[0][2], pts, run_closed))
    return out


# ---------------------------------------------------------------- 对外入口
def build_wall_polylines(plan, warn=None):
    """由墙段计划生成连续的墙体多段线。

    plan 为 geometry.build_wall_plan 的返回值。
    返回 [(图层, 点序列, 是否闭合), ...]，单位为模型空间 mm。
    """
    adds, subs = [], []
    for entry in plan:
        adds.append((wall_rect(entry, plan), entry["layer"]))
        for a, b, op in entry["voids"]:
            # 门、窗、无扇洞口都要在墙上断开：
            #   - 门与无扇洞口：洞口内不画东西，只有封口线与门扇图例
            #   - 窗：洞口内另画窗的四条细线（窗台线 + 玻璃线），
            #         墙的粗线在洞口处必须断开，改用细线表达（GB/T 50104 窗图例）
            subs.append((void_rect(entry, a, b), "WIN-DOOR"))

    if not adds:
        return []

    polys = [(pts, layer, "add") for pts, layer in adds]
    polys += [(pts, layer, "sub") for pts, layer in subs]

    segs = split_all(_collect_edges(polys))
    segs = dedupe(segs)
    boundary = pick_boundary(segs, adds, subs)

    if warn is not None and not boundary:
        warn("墙体轮廓追踪结果为空，请检查墙段厚度与洞口位置")

    result = []
    for path, layers, closed in trace_loops(boundary):
        result.extend(merge_and_split_loop(path, layers, closed))
    return result
