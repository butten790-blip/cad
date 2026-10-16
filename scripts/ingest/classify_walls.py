#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ingest/classify_walls.py — 从矢量 PDF 提取墙体，并区分「剪力墙 / 隔墙」

墙体表示法（GB/T 50104）
    · 钢筋混凝土剪力墙（承重/抗震墙）：**墙体轮廓线 + 实心黑色填充（poché）**
        → 几何取「轮廓」，填充只是剖切图例 → 本工具归 WALL-STR（structural）
    · 砌体隔墙 / 填充墙：**双线轮廓，无填充**
        → 本工具归 WALL-PAR（partition）

关键：**不要拿填充当真墙**。填充是 poché，墙的几何来自轮廓线。
      判定：某墙体区域内部是否有实心填充 → 有=剪力墙，无=隔墙。

用法
    python ingest/classify_walls.py in.pdf out.dxf --k 0.0066067 --x0 49.6 --y0 122.0
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

import ezdxf
import fitz

PT2MM = 25.4 / 72.0

# 墙体厚度分档（模型 mm）：剪力墙常见 200，隔墙 100/120
STR_MIN, STR_MAX = 60, 420      # 填充带的"薄边"范围
PAR_LO, PAR_HI = 60, 200        # 隔墙双线间距范围


# 建筑主体范围（模型 mm），用于排除图外的尺寸链/图框/说明区
BB = (-600, 17800, -600, 13400)


def build(pdf_path, out_path, k, x0, y0):
    def mx(pt):
        return (pt * PT2MM - x0) / k

    def my(pt):
        return (y0 - pt * PT2MM) / k

    page = fitz.open(pdf_path)[0]
    paths = page.get_drawings()

    # ---------- 1) 剪力墙：填充带（poché）→ 轮廓矩形 ----------
    str_fills = []
    for d in paths:
        if d.get("fill") is None:
            continue
        r = d["rect"]
        xa, xb = mx(r.x0), mx(r.x1)
        u, l = my(r.y0), my(r.y1)          # PDF y 向下：r.y0 在上
        ya, yb = min(u, l), max(u, l)
        w, h = xb - xa, yb - ya
        th, L = min(w, h), max(w, h)
        if STR_MIN <= th <= STR_MAX and L >= 200:
            str_fills.append({"x0": xa, "x1": xb, "y0": ya, "y1": yb,
                              "th": th, "L": L, "o": "H" if w >= h else "V"})

    # ---------- 2) 隔墙：长直描边线的平行配对，且中间无填充 ----------
    HF = defaultdict(list)   # y -> [(x0,x1)]
    VF = defaultdict(list)   # x -> [(y0,y1)]
    for d in paths:
        if d.get("color") is None or d.get("fill") is not None:
            continue
        for it in d["items"]:
            if it[0] != "l":
                continue
            (ax, ay), (bx, by) = (mx(it[1].x), my(it[1].y)), (mx(it[2].x), my(it[2].y))
            if not (BB[0] <= ax <= BB[1] and BB[0] <= bx <= BB[1]
                    and BB[2] <= ay <= BB[3] and BB[2] <= by <= BB[3]):
                continue
            if abs(by - ay) <= 30 and abs(bx - ax) > 120:
                HF[round((ay + by) / 2 / 10) * 10].append((min(ax, bx), max(ax, bx)))
            elif abs(bx - ax) <= 30 and abs(by - ay) > 120:
                VF[round((ax + bx) / 2 / 10) * 10].append((min(ay, by), max(ay, by)))

    # 已被剪力墙填充覆盖的坐标区间（用于排除"
    def covered(intervals):
        return intervals

    fill_boxes = [(f["x0"], f["x1"], f["y0"], f["y1"]) for f in str_fills]

    def inside_fill(px, py, pad=60):
        for x0, x1, y0, y1 in fill_boxes:
            if x0 - pad <= px <= x1 + pad and y0 - pad <= py <= y1 + pad:
                return True
        return False

    def merge(ivs, gap=1.0):
        out = []
        for a, b in sorted(ivs):
            if out and a <= out[-1][1] + gap:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return out

    par_walls = []

    MIN_LEN = 500     # 墙段最短长度（滤掉文字笔画、家具短边、尺寸箭头）
    END_TOL = 220     # 两端对齐容差：同一条墙的两个面，两端应基本齐平
    COMB = 70         # "梳状"判定容差：一对线附近若冒出第三条平行线 => 是填充/踏步，不是墙

    def comb_positions(pos):
        """标出"等间距平行线族"（填充 / 楼梯踏步）的坐标。

        墙 = 恰好两条平行线；填充、踏步 = 连续 ≥3 条、间距相近的平行线。
        先把这些线族的坐标整个标记出来，配对时跳过——比逐对做周期判断稳。
        """
        comb, n, i = set(), len(pos), 0
        while i < n - 1:
            d0 = pos[i + 1] - pos[i]
            if PAR_LO <= d0 <= PAR_HI:
                j = i + 1
                while j + 1 < n:
                    dj = pos[j + 1] - pos[j]
                    if PAR_LO <= dj <= PAR_HI and abs(dj - d0) <= 45:
                        j += 1
                    else:
                        break
                if j - i + 1 >= 3:
                    comb.update(pos[i:j + 1])
                i = j
            i += 1
        return comb

    def pair(faces, axis):
        pos = sorted(faces)
        comb = comb_positions(pos)
        for i in range(len(pos)):
            for j in range(i + 1, len(pos)):
                d = pos[j] - pos[i]
                if d < PAR_LO:
                    continue
                if d > PAR_HI:
                    break
                # 等间距线族（填充/踏步）直接跳过
                if pos[i] in comb or pos[j] in comb:
                    continue
                a, b = merge(faces[pos[i]]), merge(faces[pos[j]])
                for u0, u1 in a:
                    for v0, v1 in b:
                        o0, o1 = max(u0, v0), min(u1, v1)
                        if o1 - o0 < MIN_LEN:
                            continue
                        # 两端基本对齐 => 确是同一条墙的两个面（而非两条无关线）
                        if abs(u0 - v0) > END_TOL or abs(u1 - v1) > END_TOL:
                            continue
                        cl = (pos[i] + pos[j]) / 2
                        mid = (o0 + o1) / 2
                        if axis == "H" and inside_fill(mid, cl):
                            continue
                        if axis == "V" and inside_fill(cl, mid):
                            continue
                        par_walls.append([axis, cl, o0, o1, d])

    pair(HF, "H")
    pair(VF, "V")

    # 去重：同一轴、中心线相近、区间重叠 => 只留最长的一条
    par_walls.sort(key=lambda w: (w[0], round(w[1] / 30), -(w[3] - w[2])))
    dedup = []
    for w in par_walls:
        dup = False
        for k in dedup:
            if k[0] == w[0] and abs(k[1] - w[1]) <= 30 and not (w[3] < k[2] or w[2] > k[3]):
                dup = True
                break
        if not dup:
            dedup.append(w)
    par_walls = dedup

    # ---------- 3) 落 DXF ----------
    # 图层与 references/layers.md 对齐：
    #   轮廓与填充分开 —— 墙的几何是轮廓线（直线/多段线），实心黑只是叠在上面的填充命令。
    #   关掉 WALL-POC 仍能看到完整的墙轮廓，这正是分层的意义。
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    for n, c, lw in [("WALL-STR", 7, 50),   # 结构墙轮廓 b      = 0.50mm
                     ("WALL-POC", 7, 25),   # 剖切填充（实心黑）
                     ("WALL-PAR", 7, 35),   # 隔墙 0.7b         = 0.35mm
                     ("REF-INK", 8, 13)]:
        doc.layers.add(name=n, color=c, lineweight=lw)
    msp = doc.modelspace()

    n_str = 0
    for f in str_fills:
        pts = [(f["x0"], f["y0"]), (f["x1"], f["y0"]),
               (f["x1"], f["y1"]), (f["x0"], f["y1"])]
        # ① 轮廓：多段线（墙的几何本体）
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "WALL-STR"})
        # ② 填充：叠在轮廓内的实心黑，单独图层
        h = msp.add_hatch(color=7, dxfattribs={"layer": "WALL-POC"})
        h.set_solid_fill(color=7)
        h.paths.add_polyline_path(pts, is_closed=True)
        n_str += 1

    n_par = 0
    for axis, cl, a, b, th in par_walls:
        if axis == "H":
            msp.add_line((a, cl - th / 2), (b, cl - th / 2), dxfattribs={"layer": "WALL-PAR"})
            msp.add_line((a, cl + th / 2), (b, cl + th / 2), dxfattribs={"layer": "WALL-PAR"})
        else:
            msp.add_line((cl - th / 2, a), (cl - th / 2, b), dxfattribs={"layer": "WALL-PAR"})
            msp.add_line((cl + th / 2, a), (cl + th / 2, b), dxfattribs={"layer": "WALL-PAR"})
        n_par += 1

    doc.saveas(out_path)
    return {"shear_walls": n_str, "partition_walls": n_par, "entities": len(msp)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="矢量 PDF -> 墙体(剪力墙/隔墙) DXF")
    ap.add_argument("pdf")
    ap.add_argument("dxf")
    ap.add_argument("--k", type=float, required=True)
    ap.add_argument("--x0", type=float, default=0.0)
    ap.add_argument("--y0", type=float, default=0.0)
    a = ap.parse_args(argv)
    print("OK ->", a.dxf, build(a.pdf, a.dxf, a.k, a.x0, a.y0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
