"""房间：地面填充、房间名与面积标注。"""

from __future__ import annotations

from ezdxf.enums import TextEntityAlignment

from . import standards as S
from .annotation import add_text
from .hatch import add_area_hatch, PATTERN_DESCRIPTION


def _shoelace_area_m2(points) -> float:
    """多边形面积，返回 m²（输入 mm）。"""
    n = len(points)
    if n < 3:
        return 0.0
    acc = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        acc += x1 * y2 - x2 * y1
    return abs(acc) / 2.0 / 1_000_000.0


def _centroid(points):
    """面积质心；退化时回退到顶点算术平均。"""
    n = len(points)
    acc = 0.0
    cx = cy = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        acc += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(acc) < 1e-9:
        return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    return (cx / (3.0 * acc), cy / (3.0 * acc))


def _text_width_mm(text, height_mm):
    """粗估文字宽度（模型单位）。汉字按宽度因子 0.7，拉丁按 0.55。"""
    return sum(height_mm * (0.70 if ord(c) > 0x2E80 else 0.55) for c in str(text))


def _draw_area(ctx, at, area_m2, height):
    """标注面积，单位 m²。

    上标 2 用**单独的小号文字**绘制，而不是直接写 U+00B2「²」——
    仿宋（simfang.ttf）字体里没有这个字形，直接写会在 AutoCAD 与预览里
    都显示成方框。拆成两个图元后不依赖任何字体的上标支持。
    """
    msp = ctx.msp
    main = f"{area_m2:.2f}m"
    # 上标字高不得低于国标下限 2.5mm，否则自检会判为违规
    sup_h = max(height * 0.62, ctx.ps(S.MIN_TEXT_HEIGHT))
    sup = "2"

    total = _text_width_mm(main, height) + _text_width_mm(sup, sup_h)
    x0 = float(at[0]) - total / 2.0
    y0 = float(at[1])

    text = msp.add_text(main, height=height,
                        dxfattribs={"layer": "TEXT", "style": S.STYLE_NAME})
    text.set_placement((x0, y0), align=TextEntityAlignment.BOTTOM_LEFT)

    sup_text = msp.add_text(sup, height=sup_h,
                            dxfattribs={"layer": "TEXT", "style": S.STYLE_NAME})
    sup_text.set_placement((x0 + _text_width_mm(main, height),
                            y0 + height * 0.38),
                           align=TextEntityAlignment.BOTTOM_LEFT)


def draw_rooms(ctx, rooms, draw_floor=True) -> None:
    """绘制房间名与面积，并按需绘制地面材质填充。

    draw_floor=False 时不填充地面 —— 平面布置图按制图惯例不画地面材质，
    材质填充属于「地面铺装图」。开启填充时，材质线会压住房间名与标高文字，
    这是按图别分工应当避免的。
    """
    for room in rooms:
        pts = [(float(p[0]), float(p[1])) for p in room["boundary"]]
        if len(pts) < 3:
            ctx.warn(f"房间「{room.get('name', '?')}」边界顶点少于 3 个，已跳过")
            continue

        material = room.get("floor")
        if material and draw_floor:
            if material not in PATTERN_DESCRIPTION:
                ctx.warn(f"房间「{room['name']}」使用了未知填充材料 {material}，已跳过填充")
            else:
                add_area_hatch(ctx.msp, pts, material, scale=ctx.scale)

        if room.get("label_at"):
            at = (float(room["label_at"][0]), float(room["label_at"][1]))
        else:
            cx, cy = _centroid(pts)
            # 默认略高于形心：内墙局部尺寸链是贴墙画的，房间名居中时会与它相撞
            ys = [p[1] for p in pts]
            at = (cx, cy + 0.18 * (max(ys) - min(ys)))
        name_h = ctx.ps(S.HEIGHT_NOTE)               # 房间名 5mm
        area_h = ctx.ps(S.HEIGHT_DIM)                # 面积 3.5mm

        if room.get("show_area", True):
            add_text(ctx, (at[0], at[1] + name_h * 0.6), room["name"], name_h)
            _draw_area(ctx, (at[0], at[1] - area_h * 2.2),
                       _shoelace_area_m2(pts), area_h)
        else:
            add_text(ctx, at, room["name"], name_h)
