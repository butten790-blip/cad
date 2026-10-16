#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest/probe.py — 参考图体检（拿到任何参考图，先跑它）

为什么需要
    参考图**不一定遵守本技能的约定**（可能是别的标准、别的墙体表示法、没有尺寸、
    甚至不是平面图）。所以不能先假设计划，必须先"体检"：摸清输入类型、约定、可标定线索，
    再决定走哪条路线、哪些参数需要问用户。

输出一份报告，包含：
    · 输入类型（矢量 PDF / 位图）
    · 页面尺寸、内容范围
    · 线宽分布、填充比例、填充件厚度分布  → 推断**墙体表示法**（实心 poché / 斜线填充 / 双线）
    · 文字高度、疑似比例文字（1:N）、疑似总尺寸数字 → **标定线索**
    · 图纸类型线索（平面/立面/剖面/大样/天花/铺装/门窗表/轴号…）
    · 推荐路线 + **需要用户确认的不确定项**

用法
    python ingest/probe.py 参考图.pdf
    python ingest/probe.py 参考图.png
"""
from __future__ import annotations

import re
import sys
from collections import Counter

PT2MM = 25.4 / 72.0

TYPE_WORDS = {
    "平面": ["平面", "平面图", "布置图", "PLAN"],
    "天花": ["天花", "吊顶", "顶棚", "CEILING"],
    "铺装": ["铺装", "地面", "地材"],
    "立面": ["立面", "ELEVATION"],
    "剖面": ["剖面", "SECTION"],
    "大样": ["大样", "详图", "节点", "DETAIL"],
    "门窗表": ["门窗表", "门窗统计"],
    "总平面": ["总平面", "总图", "SITE"],
    "轴号": ["轴"],
}


def probe_pdf(path):
    import fitz
    doc = fitz.open(path)
    page = doc[0]
    r = page.rect
    pw, ph = r.width * PT2MM, r.height * PT2MM
    drawings = page.get_drawings()
    n_fill = sum(1 for d in drawings if d.get("fill") is not None)
    n_stroke = sum(1 for d in drawings if d.get("color") is not None)
    n_img = len(page.get_images(full=True))

    widths = Counter(round(d.get("width") or 0, 3) for d in drawings)
    fills_th = Counter()
    for d in drawings:
        if d.get("fill") is None:
            continue
        rr = d["rect"]
        w = rr.width * PT2MM
        h = rr.height * PT2MM
        fills_th[round(min(w, h) / 0.2) * 0.2] += 1

    texts, sizes = [], Counter()
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            for s in ln["spans"]:
                if s["text"].strip():
                    texts.append(s["text"].strip())
                    sizes[round(s["size"], 1)] += 1

    joined = " ".join(texts)
    scales = re.findall(r"1\s*[:：]\s*(\d{2,4})", joined)
    nums = sorted((int(m) for m in re.findall(r"\b(\d{3,5})\b", joined)), reverse=True)[:10]
    types = [k for k, ws in TYPE_WORDS.items() if any(w in joined for w in ws)]

    print("=" * 64)
    print("参考图体检报告")
    print("=" * 64)
    print(f"输入类型    : 矢量 PDF" if (len(drawings) > 30 and n_img == 0)
          else f"含位图的 PDF（疑似扫描）")
    print(f"页数/页幅   : {doc.page_count} 页 / {pw:.0f} x {ph:.0f} mm")
    print(f"矢量路径    : {len(drawings)}  (描边 {n_stroke} / 填充 {n_fill})")
    print(f"嵌入位图    : {n_img}")
    print(f"线宽分布    : {dict(widths.most_common(6))} (pt)")
    if n_fill:
        print(f"填充件厚度  : {dict(sorted(fills_th.items())[:10])} 纸面mm"
              "   -> 记下这些值，按比例换算后若落在 100~300mm 且在墙位置，多为剪力墙 poché")
    print(f"文字条目    : {len(texts)}；字号分布 {dict(sizes.most_common(6))} (pt)")
    print(f"疑似比例    : {scales[:5] if scales else '未找到（需人工量尺寸标定）'}")
    print(f"疑似尺寸数字: {nums[:8]}")
    print(f"图纸类型线索: {types if types else '未识别（可能是纯图形/图片）'}")
    print("-" * 64)
    print("推荐路线：矢量 → pdf_to_dxf.py（保真转绘，不依赖约定）")
    print("需用户确认：出图比例 / 墙体表示法（实心?斜线?双线?）/ 是否需要语义重建")
    return 0


def probe_image(path):
    import cv2
    import numpy as np
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        print("无法读取图片")
        return 1
    h, w = img.shape
    ink = (img < 128).mean()
    print("=" * 64)
    print("参考图体检报告（位图）")
    print("=" * 64)
    print(f"输入类型    : 位图（JPG/PNG）")
    print(f"分辨率      : {w} x {h}")
    print(f"墨迹占比    : {ink*100:.2f}%")
    print("-" * 64)
    print("推荐路线：raster_to_dxf.py（描线，保真度中）")
    print("需用户确认：量图上已知尺寸的像素长 → mm_per_px；墙厚像素；文字是否需 OCR")
    return 0


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    p = argv[0]
    if p.lower().endswith(".pdf"):
        return probe_pdf(p)
    return probe_image(p)


if __name__ == "__main__":
    sys.exit(main())
