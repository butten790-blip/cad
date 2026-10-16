"""出图流水线：把 drawing.spec 渲染成一张完整图纸。

顺序很关键：
    1. 建文档与基础设施（线型 / 图层 / 文字样式 / 标注样式）
    2. 先算建筑外包框，再算定位点，然后**平移数据本身**
       （而不是平移已画好的实体，避免把图框也一起挪走）
    3. 画图框（图纸坐标，不参与平移）
    4. 画正文（已平移的模型坐标）

本模块必须是纯函数式、确定性的：同 spec → 同 DXF。
"""

from __future__ import annotations

import ezdxf
from ezdxf import units as ezdxf_units

from cadlib import standards as S
from cadlib.annotation import (add_text, draw_axes, draw_dimensions,
                               draw_elevations, draw_section_marks)
from cadlib.context import Context
from cadlib.furniture import draw_furniture
from cadlib.geometry import build_wall_plan, draw_walls
from cadlib.layers import create_dimstyle, create_layers, ensure_linetypes
from cadlib.rooms import draw_rooms
from cadlib.schedule import (collect_openings, draw_opening_labels,
                             draw_schedule, schedule_size)
from cadlib.structure import draw_columns
from cadlib.site import draw_apron, draw_steps
from cadlib.textstyle import create_text_styles
from cadlib.titleblock import (draw_drawing_name, draw_frame, draw_north_arrow,
                               frame_rect, text_width_mm)


# ---------------------------------------------------------------- 工具
def _sh(point, d):
    return [float(point[0]) + d[0], float(point[1]) + d[1]]


def _sheet_dims(size: str, orientation: str):
    short, long_ = S.SHEET_SIZES[size]
    return (long_, short) if orientation == "landscape" else (short, long_)


def _parse_scale(label) -> float:
    try:
        return float(str(label).split(":")[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"无法解析比例「{label}」，应形如 1:50") from exc


def _building_bbox(spec, plan):
    """建筑外包框（模型空间 mm），计及墙厚、轴网与房间边界。"""
    xs, ys = [], []

    for entry in plan:
        t = entry["thickness"]
        px, py = entry["p"]
        ux, uy = entry["u"]
        nx, ny = entry["n"]
        for s in (0.0, entry["length"]):
            for off in (t / 2.0, -t / 2.0):
                xs.append(px + ux * s + nx * off)
                ys.append(py + uy * s + ny * off)
    grid = spec.get("grid") or {}
    xs += [float(v) for v in (grid.get("x") or [])]
    ys += [float(v) for v in (grid.get("y") or [])]

    for room in spec.get("rooms") or []:
        for p in room.get("boundary") or []:
            xs.append(float(p[0]))
            ys.append(float(p[1]))

    # 注意：结构柱**不计入**尺寸用的外包框。尺寸体系基于「墙与轴线」，
    # 外包尺寸取墙体外皮；柱视为嵌墙构件（突出墙外时由用户单独标注），
    # 否则柱子会把外包框撑大，导致总尺寸链与轴线链基准不一致。

    if not xs or not ys:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _auto_anchor(ctx, bbox, frame, notes_count=0):
    """把建筑放进图框的可用区，并自动避让右下角标题栏。

    四边需要的让位量按出图要素倒推（图纸毫米）：
        轴号 34 + 2 边距；底部再加图名（7mm 字 + 4mm 间隙 + 下划线）约 14mm。
    可用区不是整个图框：右下角 180×40 的标题栏是硬占用，必须避开。
    按优先级依次尝试「标题栏上方」与「标题栏左侧」两块矩形。
    放不下时贴边摆放并报警，由版面校验给出结论与建议。
    """
    ext = S.AXIS_EXTEND_MIN
    name_zone = 14.0
    bw = ctx.ms(bbox[2] - bbox[0])
    bh = ctx.ms(bbox[3] - bbox[1])
    need_w = bw + 2.0 * ext
    need_h = bh + 2.0 * ext + name_zone

    fx0, fy0, fx1, fy1 = frame["x0"], frame["y0"], frame["x1"], frame["y1"]
    regions = [
        (fx0, frame["title_y1"], fx1, fy1),    # 标题栏上方（常规布图）
        (fx0, fy0, frame["title_x0"], fy1),    # 标题栏左侧
    ]
    pad = 2.0
    target = None
    for region in regions:
        rx0, ry0, rx1, ry1 = region
        if (rx1 - rx0) - 2 * pad >= need_w and (ry1 - ry0) - 2 * pad >= need_h:
            target = region
            break
    if target is None:
        target = regions[0]
        ctx.warn(
            "图样（含轴号与图名）需要 %.0f×%.0fmm，图框内除标题栏外没有足够的"
            "完整矩形可用区；已按标题栏上方摆放，详见版面校验结论。"
            % (need_w, need_h)
        )
    rx0, ry0, rx1, ry1 = target
    bx0 = rx0 + ((rx1 - rx0) - need_w) / 2.0 + ext
    by0 = ry0 + ((ry1 - ry0) - need_h) / 2.0 + ext + name_zone
    return ctx.ps(bx0), ctx.ps(by0)


def _rect_overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _figure_band(ctx, bbox):
    """图样占位矩形（图纸毫米）：建筑外包框 + 轴号带 + 图名带。"""
    ext = ctx.ps(S.AXIS_EXTEND_MIN)
    name_h = ctx.ps(S.HEIGHT_DRAWING_NAME + 8.0)
    return (ctx.ms(bbox[0] - ext), ctx.ms(bbox[1] - ext - name_h),
            ctx.ms(bbox[2] + ext), ctx.ms(bbox[3] + ext))


def _pick_schedule_pos(ctx, frame, band, n_rows):
    """给门窗表挑一个不与图样、标题栏相撞的空位。找不到返回 None。"""
    if not n_rows:
        return None
    w, h = schedule_size(n_rows)
    title = (frame["title_x0"], frame["title_y0"],
             frame["title_x1"], frame["title_y1"])
    pad = 3.0
    candidates = (
        (frame["x1"] - pad - w, frame["title_y1"] + 4.0),      # 标题栏正上方
        (frame["x0"] + pad, frame["y1"] - S.SIGNBOX_H - 4.0 - h),  # 会签栏下方
        (frame["x0"] + 6.0, frame["y0"] + 34.0),               # 设计说明上方
    )
    for x0, y0 in candidates:
        rect = (x0, y0, x0 + w, y0 + h)
        if (x0 < frame["x0"] + 2.0 or y0 < frame["y0"] + 2.0
                or rect[2] > frame["x1"] - 2.0 or rect[3] > frame["y1"] - 2.0):
            continue
        if _rect_overlap(rect, band) or _rect_overlap(rect, title):
            continue
        return x0, y0
    return None


def _check_layout(ctx, frame, bbox, name_box, sched_box=None):
    """版面校验：图样连同轴号、图名、门窗表必须在图框内，且不与标题栏相撞。

    放不下时必须报错并给出可执行的建议（降比例或换图幅），
    不能悄悄把图贴边摆出去了事 —— 那样的图纸是不合格的。
    """
    errors = []
    band = _figure_band(ctx, bbox)

    fx0, fy0, fx1, fy1 = frame["x0"], frame["y0"], frame["x1"], frame["y1"]
    if (band[0] < fx0 - 1e-6 or band[1] < fy0 - 1e-6
            or band[2] > fx1 + 1e-6 or band[3] > fy1 + 1e-6):
        errors.append(
            "图样连同轴号与图名超出图框：需要 %.0f×%.0fmm，"
            "图框内净空仅 %.0f×%.0fmm"
            % (band[2] - band[0], band[3] - band[1], fx1 - fx0, fy1 - fy0)
        )

    tb = (frame["title_x0"], frame["title_y0"],
          frame["title_x1"], frame["title_y1"])
    if _rect_overlap(band, tb):
        errors.append("图样的尺寸链/轴号带与右下角标题栏矩形重叠")

    if name_box and _rect_overlap(name_box, tb):
        errors.append("图名与右下角标题栏重叠")

    if sched_box and _rect_overlap(sched_box, band):
        errors.append("门窗表与图样占位矩形重叠")

    if errors:
        need_w = (band[2] - band[0]) / (fx1 - fx0)
        need_h = (band[3] - band[1]) / (fy1 - fy0)
        factor = max(need_w, need_h)
        suggestion = [s for s in S.SCALES if s >= ctx.scale * factor]
        nxt = suggestion[0] if suggestion else None
        errors.append(
            "建议：比例改为 1:%s 或换用更大图幅（当前 1:%d，%s）"
            % (nxt, int(ctx.scale), ctx.sheet_size)
            if nxt else
            "建议：换用更大图幅（当前 1:%d，%s）" % (int(ctx.scale), ctx.sheet_size)
        )
    return errors


def _draw_notes(ctx, meta, frame):
    """把 meta.notes 作为设计说明写在图框左下角（标题栏上方）。

    说明条目自上而下排（1、2、3…），所以第 1 条落在最上面：
    先算出整块说明的高度，再从顶部往下排。
    """
    notes = meta.get("notes") or []
    if not notes:
        return
    from ezdxf.enums import TextEntityAlignment
    x = ctx.ps(frame["x0"] + 6.0)
    y0 = ctx.ps(frame["y0"] + 4.0)
    step = ctx.ps(S.HEIGHT_NOTE * 1.8)
    top = y0 + (len(notes) - 1) * step
    for i, line in enumerate(notes):
        add_text(ctx, (x, top - i * step), str(line),
                 ctx.ps(S.HEIGHT_DIM), layer="TEXT",
                 align=TextEntityAlignment.BOTTOM_LEFT)


def _shift_grid(grid, d):
    out = dict(grid)
    if grid.get("x"):
        out["x"] = [float(v) + d[0] for v in grid["x"]]
    if grid.get("y"):
        out["y"] = [float(v) + d[1] for v in grid["y"]]
    return out


def _shift_explicit_dimensions(items, d):
    out = []
    for item in items:
        axis = item["axis"]
        new = dict(item)
        if axis == "horizontal":
            new["at"] = float(item["at"]) + d[1]
        elif axis == "vertical":
            new["at"] = float(item["at"]) + d[0]
        else:
            new["at"] = float(item["at"])
        new["segments"] = [
            dict(seg, **{"from": _sh(seg["from"], d), "to": _sh(seg["to"], d)})
            for seg in item["segments"]
        ]
        out.append(new)
    return out


# ---------------------------------------------------------------- 主流程
def render_spec(spec: dict, dxf_version: str = "R2018"):
    """执行完整出图。返回 (doc, ctx, info)。"""
    meta = spec.get("meta") or {}
    sheet = spec.get("sheet") or {}
    size = sheet.get("size", "A3")
    if size not in S.SHEET_SIZES:
        raise ValueError(f"未知图幅 {size}，可选 {tuple(S.SHEET_SIZES)}")
    orientation = sheet.get("orientation", "landscape")
    scale = _parse_scale(meta.get("scale", "1:50"))
    b = float(sheet.get("lineweight_b", S.DEFAULT_B))
    if b not in S.BASIC_LINEWEIGHTS:
        raise ValueError(f"基本线宽 b={b} 非法，可选 {S.BASIC_LINEWEIGHTS}")

    sheet_w, sheet_h = _sheet_dims(size, orientation)

    # 冻结 ezdxf 写入的元数据（$TDCREATE/$TDUPDATE/$VERSIONGUID/$FINGERPRINTGUID
    # 以及 "ezdxf x.y.z @ 时间" 标记）。这些字段默认取系统当前时间与随机 GUID，
    # 会让同一份 spec 每次产出的 DXF 哈希都不同，版本比对与变更审阅就失去意义。
    # 图签上的日期信息由 meta.date 写入标题栏，不受此影响。
    ezdxf.options.write_fixed_meta_data_for_testing = True

    doc = ezdxf.new(dxf_version, setup=True)
    doc.units = ezdxf_units.MM
    msp = doc.modelspace()

    # 模型空间出图的两组全局变量，必须在建实体之前设好：
    #   $LTSCALE   线型比例。CENTER/DASHED 的划线长度以模型单位计，若保持 1，
    #              在 1:50 的模型尺寸下点划线会退化成实线，CAD 里看不出轴线的点划特征。
    #   $MEASUREMENT 1 = 公制，让 CAD 选用公制线型与填充图案库。
    doc.header["$LTSCALE"] = float(scale)
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LUNITS"] = 2

    ctx = Context(doc=doc, msp=msp, scale=scale, b=b,
                  sheet_size=size, sheet_w=sheet_w, sheet_h=sheet_h)

    ensure_linetypes(doc)
    create_layers(ctx)
    create_text_styles(ctx)
    create_dimstyle(ctx)

    # ---- 定位：先算外包框，再决定平移量
    walls = spec.get("walls") or []
    openings = spec.get("openings") or []
    plan0 = build_wall_plan(walls, openings, ctx)
    bbox0 = _building_bbox(spec, plan0)

    fr = frame_rect(ctx)
    origin = spec.get("origin")
    if origin:
        ax = ctx.ps(float(origin[0])) + ctx.ps(fr["x0"])
        ay = ctx.ps(float(origin[1])) + ctx.ps(fr["y0"])
        if ax < ctx.ps(fr["x0"]) or ay < ctx.ps(fr["y0"]):
            ctx.warn("origin 使图形落到图框外，请检查 spec.origin 取值")
    else:
        ax, ay = _auto_anchor(ctx, bbox0, fr, len(meta.get("notes") or []))
    d = (ax - bbox0[0], ay - bbox0[1])

    # ---- 平移模型侧数据（图框不在其中）
    walls_s = [dict(w, start=_sh(w["start"], d), end=_sh(w["end"], d))
               for w in walls]
    plan = build_wall_plan(walls_s, openings, ctx)
    bbox = (bbox0[0] + d[0], bbox0[1] + d[1],
            bbox0[2] + d[0], bbox0[3] + d[1])

    grid_s = _shift_grid(spec.get("grid") or {}, d)
    rooms_s = [dict(r,
                    boundary=[_sh(p, d) for p in r["boundary"]],
                    label_at=(_sh(r["label_at"], d) if r.get("label_at") else None))
               for r in (spec.get("rooms") or [])]
    elevations_s = [dict(e, at=_sh(e["at"], d))
                    for e in (spec.get("elevations") or [])]
    furniture_s = [dict(f, at=_sh(f["at"], d))
                   for f in (spec.get("furniture") or [])]
    texts_s = [dict(t, at=_sh(t["at"], d)) for t in (spec.get("texts") or [])]
    columns_s = [dict(c, at=_sh(c["at"], d)) for c in (spec.get("columns") or [])]
    sections_s = [dict(c, at=_sh(c["at"], d)) for c in (spec.get("sections") or [])]
    site_s = spec.get("site") or {}
    steps_s = [dict(c, at=_sh(c["at"], d)) for c in (site_s.get("steps") or [])]

    dims_cfg = dict(spec.get("dimensions") or {})
    dims_cfg["explicit"] = _shift_explicit_dimensions(
        dims_cfg.get("explicit") or [], d)
    spec_s = dict(spec, grid=grid_s, dimensions=dims_cfg)

    # ---- 绘制
    kind = spec.get("drawing_kind", "plan")
    draw_frame(ctx, meta)
    draw_north_arrow(ctx, fr)
    draw_axes(ctx, grid_s, bbox)
    draw_walls(ctx, plan)
    draw_opening_labels(ctx, plan)
    draw_columns(ctx, columns_s)
    draw_apron(ctx, site_s.get("apron"), bbox)
    draw_steps(ctx, steps_s)
    # 地面材质填充只属于「地面铺装图」；平面布置图/天花图按惯例不填充，
    # 否则材质线会压住房间名与标高。
    draw_rooms(ctx, rooms_s, draw_floor=(kind == "floor_finish"))
    draw_dimensions(ctx, spec_s, plan, bbox)
    draw_section_marks(ctx, sections_s)
    draw_elevations(ctx, elevations_s, bbox)
    draw_furniture(ctx, furniture_s)

    for t in texts_s:
        add_text(ctx, t["at"], t["content"],
                 ctx.ps(float(t.get("height", S.HEIGHT_NOTE))),
                 layer="TEXT", rotation=float(t.get("rotation", 0) or 0))

    _draw_notes(ctx, meta, fr)
    name_box = draw_drawing_name(
        ctx, meta.get("drawing_name"), bbox, fr,
        scale_label=meta.get("scale") or "1:%d" % int(ctx.scale))

    # 门窗表：位置要从「图样 + 轴号 + 图名」占位之外的空位里挑
    band = _figure_band(ctx, bbox)
    sched_box = None
    if spec.get("window_door_schedule", True):
        rows = collect_openings(spec_s.get("openings") or [])
        pos = _pick_schedule_pos(ctx, fr, band, len(rows))
        if pos:
            sched_box = draw_schedule(ctx, rows, pos[0], pos[1])
        elif rows:
            ctx.warn("图框内没有足够空位放门窗表，已跳过；"
                     "可增大图幅/降低比例，或设 window_door_schedule=false 关闭")

    layout_errors = _check_layout(ctx, fr, bbox, name_box, sched_box)
    for msg in layout_errors:
        ctx.warn(f"[版面] {msg}")

    info = {
        "entities": len(msp),
        "layers": len(doc.layers),
        "bbox": bbox,
        "anchor": (ax, ay),
        "warnings": list(ctx.warnings),
        "layout_errors": layout_errors,
        # 整张图纸的模型空间范围，供预览渲染锁定视图
        "sheet_extents": (0.0, 0.0,
                          ctx.ps(ctx.sheet_w), ctx.ps(ctx.sheet_h)),
    }
    return doc, ctx, info
