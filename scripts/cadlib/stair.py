"""楼梯平面表达（GB/T 50104 图样画法）。

平面图里楼梯按「踏步分格 + 上下行方向线」表达：
    - 踏步：垂直于行进方向的等分细线；
    - 方向线：自起步端画至最后一个踏步，端部带箭头，并注「上」「下」；
    - 剖到的梯段与未剖到的梯段用折断线区分——一层平面只画起步段，
      本模块按「整段可行走踏步」表示，不画折断线（一层楼梯的常见简化）。

局部坐标系：外包矩形 x ∈ [-w/2, w/2]，y ∈ [-d/2, d/2]，行进方向为 +Y；
`at` 为外包中心。rotation 为逆时针角度。
"""

from __future__ import annotations

import math

from .annotation import add_text
from . import standards as S


def _p(cx, cy, c, s, x, y):
    return (cx + x * c - y * s, cy + x * s + y * c)


def draw_stairs(ctx, items) -> None:
    if not items:
        return
    msp = ctx.msp
    for item in items:
        cx, cy = float(item["at"][0]), float(item["at"][1])
        w = float(item.get("width", 1200))
        d = float(item.get("depth", 2400))
        n = max(2, int(item.get("steps", 9)))
        rot = float(item.get("rotation", 0) or 0)
        up = str(item.get("direction", "up")).lower() != "down"
        rad = math.radians(rot)
        c, s = math.cos(rad), math.sin(rad)

        # 梯段外框
        msp.add_lwpolyline(
            [_p(cx, cy, c, s, -w / 2, -d / 2), _p(cx, cy, c, s, w / 2, -d / 2),
             _p(cx, cy, c, s, w / 2, d / 2), _p(cx, cy, c, s, -w / 2, d / 2)],
            close=True, dxfattribs={"layer": "STAIR"},
        )
        # 踏步分格
        for k in range(1, n):
            y = -d / 2 + d * k / n
            msp.add_line(_p(cx, cy, c, s, -w / 2, y),
                         _p(cx, cy, c, s, w / 2, y),
                         dxfattribs={"layer": "STAIR"})

        # 上下行方向线 + 箭头
        y0 = -d / 2 + d * 0.06 if up else d / 2 - d * 0.06
        y1 = d / 2 - d * 0.06 if up else -d / 2 + d * 0.06
        msp.add_line(_p(cx, cy, c, s, 0, y0), _p(cx, cy, c, s, 0, y1),
                     dxfattribs={"layer": "STAIR"})
        ah = max(w * 0.10, 60.0)
        msp.add_line(_p(cx, cy, c, s, 0, y1), _p(cx, cy, c, s, -ah * 0.5, y1 - (ah if up else -ah)),
                     dxfattribs={"layer": "STAIR"})
        msp.add_line(_p(cx, cy, c, s, 0, y1), _p(cx, cy, c, s, ah * 0.5, y1 - (ah if up else -ah)),
                     dxfattribs={"layer": "STAIR"})

        add_text(ctx, _p(cx, cy, c, s, 0, y1 + (ah * 0.9 if up else -ah * 0.9)),
                 "上" if up else "下", ctx.ps(S.HEIGHT_NOTE), layer="STAIR")
