#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest/raster_to_dxf.py — 位图(JPG/PNG)参考图 → DXF（描线复刻）

适用
    只拿到 JPG/PNG 位图参考图时使用。**保真度低于矢量 PDF 路线**，属于"描线"：
    先按线宽把图纸分成"墙带/细线"两类，再矢量化为多段线，最后按 --scale 换算到模型毫米。

原理（OpenCV）
    1) 灰度 + 自适应阈值 → 墨迹二值图
    2) 形态学开运算（核≈墙厚像素）→ 取出**粗线/墙带**；原图减去它 → **细线**
    3) 对两类分别 findContours / HoughLinesP → 轮廓点
    4) 按 --mm-per-px 换算到模型毫米后写 DXF

标定
    量出图上某道已知尺寸（如 3600）在图中的像素长度 L_px，
    mm_per_px = 3600 / L_px。用 --mm-per-px 传入。

文字
    本模块不做 OCR（避免引入重依赖）。文字请在 CAD 里用原图对照补录，
    或另装开源 OCR（RapidOCR/PaddleOCR）后自行扩展。

用法
    python raster_to_dxf.py scan.png out.dxf --mm-per-px 12.5 --wall-px 15
"""
from __future__ import annotations

import argparse
import sys

import cv2
import ezdxf
import numpy as np


def build(img_path: str, out_path: str, mm_per_px: float,
          wall_px: int = 15, approx_eps: float = 1.5) -> dict:
    img = cv2.imread(img_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise SystemExit(f"cannot read image: {img_path}")
    h, w = img.shape
    ink = cv2.adaptiveThreshold(255 - img, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                cv2.THRESH_BINARY, 21, 10)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (wall_px, wall_px))
    thick = cv2.morphologyEx(ink, cv2.MORPH_OPEN, k)      # 墙带 / 粗线
    thin = cv2.subtract(ink, thick)                        # 细线

    doc = ezdxf.new("R2010", setup=True)
    for n, c in [("REF-THICK", 7), ("REF-THIN", 8)]:
        doc.layers.add(name=n, color=c)
    msp = doc.modelspace()

    def emit(mask, layer, eps):
        cnts, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        n = 0
        for c in cnts:
            if cv2.contourArea(c) < 4 and len(c) < 4:
                continue
            ap = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
            if len(ap) < 2:
                continue
            pts = [(x * mm_per_px, (h - y) * mm_per_px) for x, y in ap]
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
            n += 1
        return n

    nt = emit(thick, "REF-THICK", approx_eps * 2)
    nn = emit(thin, "REF-THIN", approx_eps)
    doc.saveas(out_path)
    return {"image": f"{w}x{h}", "thick_polys": nt, "thin_polys": nn, "entities": len(msp)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="位图参考图 -> DXF（描线）")
    ap.add_argument("image")
    ap.add_argument("dxf")
    ap.add_argument("--mm-per-px", type=float, required=True,
                    help="每个像素代表多少模型毫米（用已知尺寸标定）")
    ap.add_argument("--wall-px", type=int, default=15,
                    help="墙带宽度像素（≈墙厚/每像素毫米）")
    ap.add_argument("--approx-eps", type=float, default=1.5)
    a = ap.parse_args(argv)
    stat = build(a.image, a.dxf, a.mm_per_px, a.wall_px, a.approx_eps)
    print(f"OK -> {a.dxf}  {stat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
