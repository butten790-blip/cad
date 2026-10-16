"""室外构件：散水、台阶、坡道。

GB/T 50104 一层平面图应表示室外的散水、入口台阶、坡道。
这些是建筑外围的构配件，与室内平面不同一张图的比例表达，用细实线画轮廓。

散水：环绕建筑外墙一圈的排水坡带，通常宽 600~1000mm，与外墙之间留缝。
台阶：入口处的踏步，由若干级矩形沿出挑方向叠放。
"""

from __future__ import annotations

from .layers import LAYER_SITE


def draw_apron(ctx, apron, bbox) -> None:
    """散水：围绕建筑外包框的一圈带。

    apron: { "width": 800, "gap": 30 }
        width 散水宽度(mm)，gap 散水与外墙之间的缝隙(mm，坡向用)。
    """
    if not apron:
        return
    msp = ctx.msp
    width = float(apron.get("width", 800) or 0)
    if width <= 0:
        return
    gap = float(apron.get("gap", 0) or 0)

    xmin, ymin, xmax, ymax = bbox
    for r in (gap, gap + width):
        pts = [(xmin - r, ymin - r), (xmax + r, ymin - r),
               (xmax + r, ymax + r), (xmin - r, ymax + r)]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": LAYER_SITE})


def draw_steps(ctx, steps) -> None:
    """入口台阶。

    steps: [ { "at": [x, y], "width": 1200, "depth": 300, "n": 2,
               "direction": "down" } ]
        at 为第一级踏步贴墙边中点；n 为踏步数；direction 为出挑方向。
    """
    msp = ctx.msp
    dirs = {"down": (0.0, -1.0), "up": (0.0, 1.0),
            "left": (-1.0, 0.0), "right": (1.0, 0.0)}
    for i, st in enumerate(steps or []):
        at = st.get("at")
        if not at or len(at) < 2:
            continue
        px, py = float(at[0]), float(at[1])
        w = float(st.get("width", 1200) or 0)
        depth = float(st.get("depth", 300) or 0)
        n = int(st.get("n", 1) or 1)
        vx, vy = dirs.get(str(st.get("direction", "down")).lower(), (0.0, -1.0))
        ux, uy = -vy, vx                     # 与出挑方向垂直 → 台阶宽向

        for k in range(1, n + 1):
            x0 = px + vx * depth * (k - 1)
            y0 = py + vy * depth * (k - 1)
            pts = [
                (x0 - ux * w / 2, y0 - uy * w / 2),
                (x0 + ux * w / 2, y0 + uy * w / 2),
                (x0 + ux * w / 2 + vx * depth, y0 + uy * w / 2 + vy * depth),
                (x0 - ux * w / 2 + vx * depth, y0 - uy * w / 2 + vy * depth),
            ]
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": LAYER_SITE})
