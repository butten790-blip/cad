#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest/pdf_to_dxf.py — 矢量 PDF → DXF（保真复刻工具）

为什么需要它
    建筑/室内施工图的**矢量 PDF**（CAD 图纸空间导出的 PDF）里，每一条线都是精确坐标。
    直接读矢量做复刻 = 几何级 1:1，远胜"截图目测"。

坐标系换算
    paper_mm = pt * 25.4 / 72
    model_mm = (paper_mm - origin_paper_mm) / k
      k    = 纸面毫米 / 模型毫米，由图上**尺寸标注**标定（如总尺寸 17200）
      origin = 模型原点(0,0) 落在纸面的位置（一般取最左下轴线交点）

标定步骤（做一次即可）
    1) 找图上最大总尺寸文字（如 "17200"），量其两端界线在纸面的 x 差 Δx；
       k = Δx / 17200。
    2) origin_x = 最左轴线纸面 x；origin_y = 最下轴线纸面 y。
    用 --k / --x0 / --y0 传入；不传则 k=1/scale、origin=0，即直接输出纸面毫米。

用法
    python pdf_to_dxf.py in.pdf out.dxf --k 0.0066067 --x0 49.6 --y0 122.0
    python pdf_to_dxf.py in.pdf out.dxf --scale 100          # 已知 1:100，且原点在 (0,0)
"""
from __future__ import annotations

import argparse
import sys

import ezdxf
import fitz
from ezdxf.enums import TextEntityAlignment

PT2MM = 25.4 / 72.0


def build(pdf_path: str, out_path: str, k: float, x0: float, y0: float,
          style_font: str = "simfang.ttf") -> dict:
    def mx(pt: float) -> float:
        return (pt * PT2MM - x0) / k

    def my(pt: float) -> float:
        return (y0 - pt * PT2MM) / k

    page = fitz.open(pdf_path)[0]
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4          # 毫米
    doc.header["$MEASUREMENT"] = 1       # 公制
    for name, color in [("REF-INK", 7), ("REF-FILL", 250), ("REF-TEXT", 7)]:
        doc.layers.add(name=name, color=color)
    try:
        doc.styles.add("CJK", font=style_font)
        style = "CJK"
    except Exception:
        style = "Standard"
    msp = doc.modelspace()

    def subpaths(items):
        subs, cur = [], []
        for it in items:
            op = it[0]
            if op == "l":
                a = (mx(it[1].x), my(it[1].y))
                b = (mx(it[2].x), my(it[2].y))
                if not cur:
                    cur.append(a)
                elif abs(cur[-1][0] - a[0]) > 1e-6 or abs(cur[-1][1] - a[1]) > 1e-6:
                    subs.append(cur)
                    cur = [a]
                cur.append(b)
            elif op == "c":  # cubic bezier -> 8-seg polyline
                p0, p1, p2, p3 = it[1], it[2], it[3], it[4]
                if not cur:
                    cur.append((mx(p0.x), my(p0.y)))
                for i in range(1, 9):
                    t = i / 8
                    m = 1 - t
                    x = m**3 * p0.x + 3 * m**2 * t * p1.x + 3 * m * t**2 * p2.x + t**3 * p3.x
                    y = m**3 * p0.y + 3 * m**2 * t * p1.y + 3 * m * t**2 * p2.y + t**3 * p3.y
                    cur.append((mx(x), my(y)))
            elif op == "re":
                r = it[1]
                subs.append([(mx(r.x0), my(r.y1)), (mx(r.x1), my(r.y1)),
                             (mx(r.x1), my(r.y0)), (mx(r.x0), my(r.y0)),
                             (mx(r.x0), my(r.y1))])
            elif op == "qu":
                q = it[1]
                subs.append([(mx(q.ul.x), my(q.ul.y)), (mx(q.ur.x), my(q.ur.y)),
                             (mx(q.lr.x), my(q.lr.y)), (mx(q.ll.x), my(q.ll.y)),
                             (mx(q.ul.x), my(q.ul.y))])
        if len(cur) >= 2:
            subs.append(cur)
        return subs

    n_poly = n_text = 0
    for d in page.get_drawings():
        is_fill = d.get("fill") is not None
        for pts in subpaths(d["items"]):
            if len(pts) < 2:
                continue
            msp.add_lwpolyline(pts, close=is_fill,
                               dxfattribs={"layer": "REF-FILL" if is_fill else "REF-INK"})
            n_poly += 1

    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b["lines"]:
            for s in line["spans"]:
                if not s["text"].strip():
                    continue
                bb = s["bbox"]
                msp.add_text(s["text"],
                             height=round(s["size"] * PT2MM / k, 1),
                             dxfattribs={"layer": "REF-TEXT", "style": style},
                             ).set_placement((mx(bb[0]), my(bb[3])),
                                             align=TextEntityAlignment.LEFT)
                n_text += 1

    doc.saveas(out_path)
    return {"polylines": n_poly, "texts": n_text, "entities": len(msp)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="矢量 PDF -> DXF（保真复刻）")
    ap.add_argument("pdf")
    ap.add_argument("dxf")
    ap.add_argument("--k", type=float, default=None,
                    help="纸面毫米/模型毫米；不给则用 1/scale")
    ap.add_argument("--x0", type=float, default=0.0, help="模型原点的纸面 x(mm)")
    ap.add_argument("--y0", type=float, default=0.0, help="模型原点的纸面 y(mm)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="出图比例分母（1:100 传 100）；仅当未给 --k 时生效")
    ap.add_argument("--font", default="simfang.ttf", help="文字样式字体")
    a = ap.parse_args(argv)
    k = a.k if a.k else 1.0 / a.scale
    stat = build(a.pdf, a.dxf, k, a.x0, a.y0, a.font)
    print(f"OK -> {a.dxf}  {stat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
