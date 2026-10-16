"""门窗编号与门窗统计表。

依据 GB/T 50104：
    - 第4.1.5条 平面图中门窗应标注编号（门用 M、窗用 C，同一类型编号相同）；
    - 门窗应汇总成门窗表，列出编号、洞口尺寸、数量、备注，供加工订货使用。

编号习惯：M0921 = 门洞宽 900、高 2100；C1515 = 窗洞宽 1500、高 1500。
spec 未给 label 时，若给了 height 就按此规则自动生成编号。
"""

from __future__ import annotations

from . import standards as S
from .annotation import add_text
from .layers import LAYER_FRAME

SCHEDULE_TITLE = "门窗表"
COL_W = (22.0, 26.0, 14.0, 30.0)      # 图号 / 洞口尺寸 / 数量 / 备注（图纸毫米）
ROW_H = 6.5
HEAD_H = 7.5


def _default_label(kind: str, width: float, height) -> str:
    """按 M/C + 宽 + 高 生成编号（宽高各取厘米位，如 900×2100 → M0921）。"""
    prefix = {"door": "M", "window": "C"}.get(kind, "D")
    if not height:
        return f"{prefix}{int(round(width))}"
    return "%s%02d%02d" % (prefix, int(round(width / 10.0)),
                           int(round(float(height) / 10.0)))


def collect_openings(openings) -> list:
    """汇总门窗：同编号合并计数。返回按编号排序的行。"""
    table = {}
    for op in openings or []:
        kind = op.get("kind", "opening")
        if kind not in ("door", "window"):
            continue
        width = float(op.get("width", 0) or 0)
        height = op.get("height")
        label = str(op.get("label") or "").strip() or \
            _default_label(kind, width, height)
        size = f"{int(round(width))}×{int(round(height))}" if height \
            else f"{int(round(width))}×-"
        key = (label, size, kind)
        table[key] = table.get(key, 0) + 1

    rows = [(k[0], k[1], v, "门" if k[2] == "door" else "窗")
            for k, v in table.items()]
    rows.sort(key=lambda r: (r[3], r[0]))
    return rows


def schedule_size(n_rows: int) -> tuple:
    """门窗表外形尺寸（图纸毫米）：宽、高（高已含表名占位）。"""
    return sum(COL_W), HEAD_H + ROW_H * n_rows + 6.0


def draw_schedule(ctx, rows, x0, y0) -> tuple:
    """在指定左下角 (x0, y0) 绘制门窗统计表，返回占位矩形。

    位置由调用方选定 —— 表格要与图样争版面，必须先定位图样再挑空位，
    不能写死在一处，否则小建筑会与图样撞上。
    """
    if not rows:
        return None
    msp = ctx.msp
    w, h = schedule_size(len(rows))

    x1, y1 = x0 + w, y0 + h
    y_body = y1 - 6.0                       # 表名占位 6mm

    lw = ctx.lw(0.13)
    msp.add_lwpolyline(
        [(ctx.ps(x0), ctx.ps(y0)), (ctx.ps(x1), ctx.ps(y0)),
         (ctx.ps(x1), ctx.ps(y_body)), (ctx.ps(x0), ctx.ps(y_body))],
        close=True, dxfattribs={"layer": LAYER_FRAME, "lineweight": lw})
    msp.add_line((ctx.ps(x0), ctx.ps(y_body - HEAD_H)),
                 (ctx.ps(x1), ctx.ps(y_body - HEAD_H)),
                 dxfattribs={"layer": LAYER_FRAME, "lineweight": lw})

    xs = [x0]
    for cw in COL_W:
        xs.append(xs[-1] + cw)
    for x in xs[1:-1]:
        msp.add_line((ctx.ps(x), ctx.ps(y0)), (ctx.ps(x), ctx.ps(y_body)),
                     dxfattribs={"layer": LAYER_FRAME, "lineweight": lw})

    hh = ctx.ps(S.HEIGHT_DIM)
    for i, head in enumerate(("编号", "洞口尺寸", "数量", "备注")):
        add_text(ctx, (ctx.ps((xs[i] + xs[i + 1]) / 2.0),
                       ctx.ps(y_body - HEAD_H / 2.0)),
                 head, hh, layer="TEXT")

    for r, row in enumerate(rows):
        yc = y_body - HEAD_H - ROW_H * (r + 0.5)
        if r:
            msp.add_line((ctx.ps(x0), ctx.ps(y_body - HEAD_H - ROW_H * r)),
                         (ctx.ps(x1), ctx.ps(y_body - HEAD_H - ROW_H * r)),
                         dxfattribs={"layer": LAYER_FRAME, "lineweight": lw})
        for i, val in enumerate((row[0], row[1], row[2], row[3])):
            add_text(ctx, (ctx.ps((xs[i] + xs[i + 1]) / 2.0), ctx.ps(yc)),
                     str(val), hh, layer="TEXT")

    add_text(ctx, (ctx.ps((x0 + x1) / 2.0), ctx.ps(y_body + 3.0)),
             SCHEDULE_TITLE, ctx.ps(S.HEIGHT_NOTE), layer=LAYER_FRAME)

    return (x0, y0, x1, y1)


def draw_opening_labels(ctx, plan) -> None:
    """在图上逐个标注门窗编号（GB/T 50104 第4.1.5条）。

    编号写在洞口的 **-n 侧**（与门扇开启方向相反的一侧），水平书写，
    避免压在门扇与开启弧线上。
    """
    for entry in plan:
        nx, ny = entry["n"]
        t = float(entry["thickness"])
        for a, b, op in entry["voids"]:
            kind = op.get("kind", "opening")
            if kind not in ("door", "window"):
                continue
            label = op.get("label") or _default_label(
                kind, float(op["width"]), op.get("height"))
            mid = (a + b) / 2.0
            px = float(entry["p"][0]) + entry["u"][0] * mid - nx * (t / 2.0 + ctx.ps(2.0))
            py = float(entry["p"][1]) + entry["u"][1] * mid - ny * (t / 2.0 + ctx.ps(2.0))
            add_text(ctx, (px, py), str(label),
                     ctx.ps(S.HEIGHT_DIM), layer="TEXT")
