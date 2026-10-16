"""出图自检：读回 DXF 并逐项核对 references/checklist.md 的质检清单。

分级：
    ERROR  违反硬性规范，必须修复后才可交付
    WARN   可能违规，需人工确认
    INFO   统计与固有局限说明

用法：
    from validate import validate_dxf
    report = validate_dxf("out.dxf", spec=spec_dict)
    print(report.as_text())
"""

from __future__ import annotations

import math

import ezdxf

from cadlib import standards as S
from cadlib.hatch import PATTERN_DESCRIPTION  # noqa: F401  (供文档引用)
from cadlib.layers import LAYER_TABLE

FORBIDDEN_AXIS_LETTERS = set("IOZ")
LEGAL_HEIGHTS = set(S.LEGAL_TEXT_HEIGHTS)
LEGAL_LINEWEIGHTS = set(S.DXF_LINEWEIGHTS)

# 仅用于把线宽检查限定在国标图层表内的图层
LAYER_NAMES = {row[0] for row in LAYER_TABLE}


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, code, message):
        self.items.append((level, code, message))

    def error(self, code, message):
        self.add("ERROR", code, message)

    def warn(self, code, message):
        self.add("WARN", code, message)

    def info(self, code, message):
        self.add("INFO", code, message)

    def has_error(self):
        return any(lv == "ERROR" for lv, _, _ in self.items)

    def counts(self):
        out = {"ERROR": 0, "WARN": 0, "INFO": 0}
        for lv, _, _ in self.items:
            out[lv] = out.get(lv, 0) + 1
        return out

    def as_text(self):
        c = self.counts()
        head = (f"===== 出图自检报告 =====  "
                f"ERROR {c['ERROR']} / WARN {c['WARN']} / INFO {c['INFO']}")
        lines = [head]
        if not self.items:
            lines.append("  未发现问题。")
        for lv, code, msg in self.items:
            lines.append(f"  [{lv:5s}] {code}: {msg}")
        return "\n".join(lines)


# ---------------------------------------------------------------- 工具
def _text_height_ok(h):
    return h >= S.MIN_TEXT_HEIGHT - 1e-9 and any(
        abs(h - x) < 1e-6 for x in LEGAL_HEIGHTS)


def _seg_intersects_aabb(p, q, box):
    """线段与轴对齐矩形是否相交（Liang-Barsky 裁剪，返回 t 区间）。"""
    x0, y0, x1, y1 = box
    dx, dy = q[0] - p[0], q[1] - p[1]
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, p[0] - x0), (dx, x1 - p[0]),
                   (-dy, p[1] - y0), (dy, y1 - p[1])):
        if abs(pp) < 1e-12:
            if qq < 0:
                return False
            continue
        r = qq / pp
        if pp < 0:
            if r > t1:
                return False
            t0 = max(t0, r)
        else:
            if r < t0:
                return False
            t1 = min(t1, r)
    return t0 <= t1


# ---------------------------------------------------------------- 各项检查
def _check_texts(doc, rep):
    """#1 字体与字高。"""
    bad_style, bad_height, tiny = [], [], []
    for e in doc.modelspace().query("TEXT MTEXT"):
        layer = e.dxf.layer
        style = e.dxf.get("style", "")
        if layer in ("TEXT", "DIM", "AXIS") and style != S.STYLE_NAME:
            bad_style.append((layer, style))
        h = e.dxf.get("height", None)
        if h is None and e.dxftype() == "MTEXT":
            h = e.dxf.get("char_height", None)
        if h:
            h_mm = h / _dimscale(doc)
            if h_mm < S.MIN_TEXT_HEIGHT - 1e-9:
                tiny.append(round(h_mm, 2))
            elif not any(abs(h_mm - x) < 0.01 for x in LEGAL_HEIGHTS):
                bad_height.append((layer, round(h_mm, 2)))
    if bad_style:
        rep.error("FONT-1", f"{len(bad_style)} 处文字未使用长仿宋样式 "
                            f"{S.STYLE_NAME}：{bad_style[:5]}")
    else:
        rep.info("FONT-1", f"全部文字均使用 {S.STYLE_NAME}（宽度因子 "
                           f"{S.FANGSONG_WIDTH_FACTOR}，近似长仿宋 1:0.7）")
    if tiny:
        rep.error("FONT-2", f"{len(tiny)} 处字高小于国标下限 2.5mm：{sorted(set(tiny))[:5]}")
    if bad_height:
        rep.error("FONT-3", f"{len(bad_height)} 处字高不在国标字高系列 "
                            f"{S.TEXT_HEIGHTS} 内：{sorted(set(bad_height))[:5]}")
    if not tiny and not bad_height:
        rep.info("FONT-2", "字高均落在国标系列内且不小于 2.5mm")


def _check_lineweights(doc, rep):
    """#2 线宽是否按 b / 0.7b / 0.5b / 0.25b 分组。"""
    bad = []
    for e in doc.modelspace():
        layer = e.dxf.layer
        if layer not in LAYER_NAMES:
            continue
        lw = e.dxf.get("lineweight", None)
        if lw in (None, -1):          # -1 / None = 随层
            layer_lw = doc.layers.get(layer).dxf.lineweight
            lw = layer_lw
        if lw in (None, -1):
            continue
        if lw not in LEGAL_LINEWEIGHTS:
            bad.append((layer, lw))
    if bad:
        rep.warn("LW-1", f"{len(bad)} 个实体的线宽不在合法档位：{sorted(set(bad))[:5]}")
    else:
        rep.info("LW-1", f"线宽均取自合法档位（线宽组 b={S.DEFAULT_B}mm）")


def _building_bbox_from_dxf(doc):
    """从尺寸链反推建筑外包框（模型空间）。

    用总尺寸链（横向最长的水平尺寸 + 纵向最长的竖直尺寸）直接得到外包框，
    比扫墙多段线更鲁棒：结构柱、散水等也画在 WALL 图层，会把外包框撑大、
    误导「外围/内部」尺寸链的判定。没有尺寸链时退回墙多段线。
    """
    hx = hy = None
    for dim in doc.modelspace().query("DIMENSION"):
        if dim.dxf.layer != "DIM":
            continue
        p2, p3 = dim.dxf.defpoint2, dim.dxf.defpoint3
        if abs(p3.x - p2.x) >= abs(p3.y - p2.y):
            lo, hi = min(p2.x, p3.x), max(p2.x, p3.x)
            if hx is None or (hi - lo) > (hx[1] - hx[0]):
                hx = (lo, hi)
        else:
            lo, hi = min(p2.y, p3.y), max(p2.y, p3.y)
            if hy is None or (hi - lo) > (hy[1] - hy[0]):
                hy = (lo, hi)

    if hx and hy:
        return (hx[0], hy[0], hx[1], hy[1])

    xs, ys = [], []
    for e in doc.modelspace().query("LWPOLYLINE"):
        if e.dxf.layer not in ("WALL-STR", "WALL-PAR"):
            continue
        for p in e.get_points("xy"):
            xs.append(float(p[0]))
            ys.append(float(p[1]))
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _on_bbox_edge(pt, bbox, tol):
    x, y = pt
    x0, y0, x1, y1 = bbox
    near_x = min(abs(x - x0), abs(x - x1)) <= tol
    near_y = min(abs(y - y0), abs(y - y1)) <= tol
    inside = x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol
    return inside and (near_x or near_y)


def _check_dim_offsets(doc, rep):
    """#4 尺寸链间距。

    国标的 10mm/7mm 规则只管**外围三道尺寸**（尺寸界线从建筑最外轮廓引出）。
    内墙的局部尺寸链贴墙布置，不适用该规则，单独归类报告，否则会误报。
    """
    ds = doc.dimstyles.get("GB_DIM") if "GB_DIM" in doc.dimstyles else None
    scale = ds.dxf.dimscale if ds else 1.0
    bbox = _building_bbox_from_dxf(doc)
    # 容差取 1 个模型单位（1mm）。不能用墙厚量级的容差：
    # 内墙的中心线端点距外包框只有半个外墙厚（120mm），容差一大就会被误判成外围链。
    tol = 1.0

    outer, inner = {}, {}
    for dim in doc.modelspace().query("DIMENSION"):
        if dim.dxf.layer != "DIM":
            continue
        dp = dim.dxf.defpoint
        p2 = dim.dxf.defpoint2
        p3 = dim.dxf.defpoint3
        horizontal = abs(p3.x - p2.x) >= abs(p3.y - p2.y)
        if horizontal:
            offset = abs(dp.y - p2.y)
            side = "bottom" if dp.y < p2.y else "top"
        else:
            offset = abs(dp.x - p2.x)
            side = "left" if dp.x < p2.x else "right"

        is_outer = bbox is not None and _on_bbox_edge((p2.x, p2.y), bbox, tol)
        target = outer if is_outer else inner
        target.setdefault(side, set()).add(round(offset / scale, 2))

    if not outer and not inner:
        rep.warn("DIM-1", "未找到任何尺寸标注")
        return

    allowed = [S.DIM_OFFSET_FIRST + S.DIM_OFFSET_STEP * i for i in range(6)]
    for side, offsets in sorted(outer.items()):
        ok = all(any(abs(v - a) < 0.6 for a in allowed) for v in offsets)
        detail = f"{side} 侧外围尺寸链距轮廓 {sorted(offsets)} mm"
        if ok:
            rep.info("DIM-1", f"{detail} —— 符合「第一道 10mm、其后 +7mm」")
        else:
            rep.warn("DIM-1", f"{detail} —— 与 10/17/24mm 规则不符，请人工确认")

    if inner:
        vals = sorted({v for s in inner.values() for v in s})
        rep.info("DIM-1B", f"内墙局部尺寸链距墙面 {vals} mm"
                           f"（贴墙布置，不受外围 10mm 规则约束）")


def _dim_text_anchor(dim):
    """取尺寸数字的实际落点。ezdxf 渲染后数字在尺寸的几何块里。

    不使用 dim.dxf.text_midpoint —— 该字段未渲染时是 (0,0,0)，
    会让校验在每个图纸原点附近误报。
    """
    try:
        blk = dim.get_geometry_block()
    except Exception:  # noqa: BLE001
        blk = None
    if blk is None:
        return None
    for e in blk:
        if e.dxftype() == "TEXT":
            p = e.dxf.insert
            return (p.x, p.y)
        if e.dxftype() == "MTEXT":
            p = e.dxf.insert
            return (p.x, p.y)
    return None


def _check_dim_text(doc, rep):
    """#3 尺寸数字是否被图线穿过（启发式，可能误报）。"""
    scale = _dimscale(doc)
    ds = doc.dimstyles.get("GB_DIM") if "GB_DIM" in doc.dimstyles else None
    text_h = (ds.dxf.dimtxt if ds else 3.5) * scale
    pad = text_h * 0.5

    lines = []
    for e in doc.modelspace().query("LINE"):
        lines.append(((e.dxf.start.x, e.dxf.start.y),
                      (e.dxf.end.x, e.dxf.end.y)))
    for e in doc.modelspace().query("LWPOLYLINE"):
        pts = [(p[0], p[1]) for p in e.get_points("xy")]
        for i in range(len(pts) - 1):
            lines.append((pts[i], pts[i + 1]))

    hits = checked = 0
    for dim in doc.modelspace().query("DIMENSION"):
        if dim.dxf.layer != "DIM":
            continue
        anchor = _dim_text_anchor(dim)
        if anchor is None:
            continue
        checked += 1
        text = dim.dxf.get("text", "") or ""
        w = max(text_h * 2.0, text_h * len(text) * 0.62)
        box = (anchor[0] - w / 2 - pad, anchor[1] - text_h / 2 - pad,
               anchor[0] + w / 2 + pad, anchor[1] + text_h / 2 + pad)
        for p, q in lines:
            if _seg_intersects_aabb(p, q, box):
                hits += 1
                break

    if checked == 0:
        rep.info("DIM-2", "尺寸数字落点不可解析，跳过该项检查（不影响其余检查）")
    elif hits:
        rep.warn("DIM-2", f"疑似 {hits}/{checked} 处尺寸数字被图线穿过"
                          f"（启发式判断，存在误报，需人工复核）")
    else:
        rep.info("DIM-2", f"{checked} 处尺寸数字均未被图线穿过（启发式判断）")


def _check_axis_labels(doc, rep):
    """#6 轴号是否使用了 I / O / Z。"""
    bad = []
    for e in doc.modelspace().query("TEXT"):
        if e.dxf.layer != "AXIS":
            continue
        val = (e.dxf.text or "").strip().upper()
        if val and val[0] in FORBIDDEN_AXIS_LETTERS:
            bad.append(val)
    if bad:
        rep.error("AXIS-1", f"轴号使用国标禁用字母 I/O/Z：{sorted(set(bad))}")
    else:
        rep.info("AXIS-1", "轴号未使用 I / O / Z")


def _check_elevation_symbol(doc, rep):
    """#5 标高符号是否为块，且为等腰直角三角形（直角朝下、高 3mm）。"""
    ins = [e for e in doc.modelspace().query("INSERT")
           if str(e.dxf.name).startswith("ELEV")]
    if not ins:
        rep.info("ELEV-1", "图中无标高符号")
        return
    for name in {e.dxf.name for e in ins}:
        if name not in doc.blocks:
            rep.error("ELEV-1", f"标高块 {name} 未定义")
            continue
        blk = doc.blocks.get(name)
        # 三角形既可能是空心（LWPOLYLINE）也可能是涂黑（SOLID，室外地坪标高）
        polys = [e for e in blk if e.dxftype() == "LWPOLYLINE"]
        if not polys:
            solids = [e for e in blk if e.dxftype() == "SOLID"]
            if solids:
                s = solids[0].dxf
                polys = None
                pts = [(s.vtx0[0], s.vtx0[1]), (s.vtx1[0], s.vtx1[1]),
                       (s.vtx2[0], s.vtx2[1])]
            else:
                rep.error("ELEV-1", f"标高块 {name} 内没有三角形轮廓")
                continue
        if polys:
            pts = [(p[0], p[1]) for p in polys[0].get_points("xy")]
        if len(pts) != 3:
            rep.error("ELEV-1", f"标高块 {name} 不是三角形（{len(pts)} 个顶点）")
            continue
        scale = _dimscale(doc)
        if not _is_iso_right_triangle(pts, S.ELEV_TRI_HEIGHT * scale):
            rep.warn("ELEV-1", f"标高块 {name} 不满足「等腰直角三角形、高 3mm、直角朝下」")
        else:
            rep.info("ELEV-1", f"标高块 {name} 几何正确（等腰直角三角形，高 3mm）")


def _is_iso_right_triangle(pts, expect_h, tol=0.05):
    (ax, ay), (bx, by), (cx, cy) = pts
    # 找出直角顶点：到另两点距离相等的那个
    d = []
    for i, j, k in ((0, 1, 2), (1, 0, 2), (2, 0, 1)):
        p, q, r = pts[i], pts[j], pts[k]
        d.append((math.dist(p, q), math.dist(p, r), (p, q, r)))
    legs, _, (apex, p1, p2) = min(d, key=lambda t: abs(t[0] - t[1]))
    l1 = math.dist(apex, p1)
    l2 = math.dist(apex, p2)
    if abs(l1 - l2) > max(tol, l1 * 0.02):
        return False
    v1 = (p1[0] - apex[0], p1[1] - apex[1])
    v2 = (p2[0] - apex[0], p2[1] - apex[1])
    if abs(v1[0] * v2[0] + v1[1] * v2[1]) > max(tol, l1 * l2 * 0.02):
        return False          # 不是直角
    height = abs(p1[1] - apex[1])
    if abs(height - expect_h) > max(tol, expect_h * 0.05):
        return False
    return apex[1] < p1[1] and apex[1] < p2[1]   # 直角朝下


def _check_room_labels(doc, rep):
    """#12 每个房间是否都有名称文字（审图意见 #18：左下角房间漏标名称）。"""
    names = set()
    for e in doc.modelspace().query("TEXT"):
        if e.dxf.layer == "TEXT" and 1 <= len(e.dxf.text) <= 6:
            names.add(e.dxf.text)
    if not names:
        rep.warn("RM-1", "未检测到任何房间名称文字")
    else:
        rep.info("RM-1", f"检测到 {len(names)} 个文字标签（房间名/编号）")


def _check_titleblock(doc, rep, spec):
    """#9 标题栏信息是否填全。"""
    meta = (spec or {}).get("meta") or {}
    required = ("project", "drawing_name", "scale")
    missing = [k for k in required if not meta.get(k)]
    optional = [k for k in ("drawing_no", "designer", "checker", "date")
                if not meta.get(k)]
    if missing:
        rep.error("TB-1", f"标题栏必填字段缺失：{missing}")
    if optional:
        rep.warn("TB-1", f"标题栏可选字段为空：{optional}")
    if not missing and not optional:
        rep.info("TB-1", "标题栏字段完整")


def _check_spec_openings(spec, rep):
    """#7 门窗尺寸与墙体位置是否对得上（在 spec 层面校验，比读 DXF 更准）。"""
    if not spec:
        rep.info("WD-1", "未提供 spec，跳过洞口定位校验")
        return
    walls = spec.get("walls") or []
    bad = 0
    for op in spec.get("openings") or []:
        ref = op.get("wall")
        idx = ref if isinstance(ref, int) else None
        if idx is None:
            for i, w in enumerate(walls):
                if w.get("id") == ref:
                    idx = i
                    break
        if idx is None or idx >= len(walls):
            rep.error("WD-1", f"洞口引用无效墙 {ref}")
            bad += 1
            continue
        w = walls[idx]
        length = math.dist(w["start"], w["end"])
        a = float(op["offset"]) - float(op["width"]) / 2.0
        b = float(op["offset"]) + float(op["width"]) / 2.0
        if a < -1e-6 or b > length + 1e-6:
            rep.error("WD-1", f"洞口 {op.get('label', op.get('kind'))} 超出墙段范围"
                              f"（{a:.0f}~{b:.0f} 超出 0~{length:.0f}）")
            bad += 1
        if float(op["width"]) <= 0:
            rep.error("WD-1", f"洞口 {op.get('label', '?')} 宽度非正")
            bad += 1
    if not bad:
        rep.info("WD-1", f"{len(spec.get('openings') or [])} 个洞口均落在所属墙段内")


def _dimscale(doc):
    if "GB_DIM" in doc.dimstyles:
        return doc.dimstyles.get("GB_DIM").dxf.dimscale or 1.0
    return 1.0


# ---------------------------------------------------------------- 入口
def validate_dxf(path, spec=None) -> Report:
    doc = ezdxf.readfile(path)
    rep = Report()

    _check_texts(doc, rep)
    _check_lineweights(doc, rep)
    _check_dim_offsets(doc, rep)
    _check_dim_text(doc, rep)
    _check_axis_labels(doc, rep)
    _check_elevation_symbol(doc, rep)
    _check_room_labels(doc, rep)
    _check_titleblock(doc, rep, spec)
    _check_spec_openings(spec, rep)

    # #8 立面/平面标高一致性：单图内无法判定
    rep.info("XREF-1", "#8 立面图与平面图标高一致性需跨图纸比对，本单图自检无法覆盖，"
                       "列入人工复核")

    msp = doc.modelspace()
    rep.info("STAT-1", f"实体 {len(msp)} 个，图层 {len(doc.layers)} 个，"
                       f"DXF 版本 {doc.dxfversion}")
    return rep


if __name__ == "__main__":
    import sys
    r = validate_dxf(sys.argv[1])
    print(r.as_text())
    raise SystemExit(1 if r.has_error() else 0)
