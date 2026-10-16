"""结构构件：结构柱。

平面图应表示被剖切到的墙、柱（GB/T 50104）。墙体已由 `outline` 生成，
本模块补结构柱：矩形轮廓 + 材料填充。

柱在图上的表达：
    - 轮廓用粗实线（落在 WALL-STR 图层，与承重墙同层级）；
    - 钢筋混凝土柱按 GB/T 50001 第9章材料图例填充，或涂黑。小比例图上
      涂黑最清晰，因此默认用 SOLID 实心填充。
"""

from __future__ import annotations

import math


def _unit(v):
    n = math.hypot(v[0], v[1])
    if n <= 0:
        return (1.0, 0.0)
    return (v[0] / n, v[1] / n)


def draw_columns(ctx, columns) -> None:
    """绘制结构柱。

    spec 结构：
        { "at": [x, y], "width": 400, "depth": 400,
          "rotation": 0, "filled": true, "label": "KZ1" }
    width 沿 X、depth 沿 Y，rotation 为绕柱心的转角（度）。
    单位：模型空间 mm。
    """
    msp = ctx.msp
    for i, col in enumerate(columns or []):
        at = col.get("at")
        if not at or len(at) < 2:
            ctx.warn(f"第 {i+1} 根柱缺少 at 坐标，已跳过")
            continue
        cx, cy = float(at[0]), float(at[1])
        w = float(col.get("width", 400))
        d = float(col.get("depth", w))
        if w <= 0 or d <= 0:
            ctx.warn(f"第 {i+1} 根柱的截面尺寸非法（{w}×{d}），已跳过")
            continue
        rot = math.radians(float(col.get("rotation", 0) or 0))
        ca, sa = math.cos(rot), math.sin(rot)

        hw, hd = w / 2.0, d / 2.0
        local = ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))
        pts = [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in local]

        msp.add_lwpolyline(pts, close=True,
                           dxfattribs={"layer": "WALL-STR"})

        # 混凝土柱涂黑（GB/T 50104 平面图材料图例的简化画法）
        if col.get("filled", True):
            hatch = msp.add_hatch(color=7, dxfattribs={"layer": "WALL-STR"})
            hatch.dxf.solid_fill = 1
            hatch.paths.add_polyline_path(pts, is_closed=True)

        if col.get("label"):
            from . import standards as S
            from .annotation import add_text
            add_text(ctx, (cx, cy - hd - ctx.ps(S.HEIGHT_DIM * 1.4)),
                     str(col["label"]), ctx.ps(S.HEIGHT_DIM), layer="TEXT")


def column_bbox(columns):
    """所有柱的包围盒 (x0, y0, x1, y1)；无柱返回 None。"""
    xs, ys = [], []
    for col in columns or []:
        at = col.get("at")
        if not at or len(at) < 2:
            continue
        w = float(col.get("width", 400)) / 2.0
        d = float(col.get("depth", col.get("width", 400))) / 2.0
        xs.extend([float(at[0]) - w, float(at[0]) + w])
        ys.extend([float(at[1]) - d, float(at[1]) + d])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))
