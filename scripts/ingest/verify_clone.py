#!/usr/bin/env python
"""复刻校验器：用**独立证据源**判定「是否 1:1 复刻」。

设计纪律（本文件最重要的一条）
================================
**绝不 import 生成侧的任何模块**（pdf_to_dxf / raster_to_dxf / classify_walls / cadlib）。
否则就是让同一套逻辑自己证明自己 —— 同一次误读会在两边同时出现，
比对结果恒为「一致」，画错了也查不出来。

两条腿各自独立：
    A. PyMuPDF 读**参考图**的路径与文字
    B. ezdxf   读**复刻件**的实体与文字
两边各自换算到「模型毫米」后叠加比对。

四路证据
--------
① 几何覆盖率   PDF 线段集合 ⇄ DXF 线段集合，双向覆盖率（漏画 / 多画）
② 尺度自校正   扫描比例系数 k，看最佳值是否偏离给定 k（抓标定错误）
③ 文字层比对   内容集合 / 数量 / 最近邻位置偏差（漏标、错标、错位）
④ 总尺寸核对   图上最大尺寸数字 vs DXF 建筑外包尺寸（确认尺度真的 1:1）

用法
----
    python verify_clone.py 参考图.pdf 复刻.dxf --k 0.0066067 --x0 49.6 --y0 122.0
    python verify_clone.py 参考图.pdf 复刻.dxf --k K --x0 X --y0 Y \
        [--tol-px 3] [--ppm 2] [--out 输出目录] [--json 报告.json] [--no-scan]

坐标系约定
----------
    PDF   : rect/point 单位是 pt  ->  paper_mm = pt * 25.4/72
            model_mm = (paper_mm - origin) / k
    DXF   : 实体坐标本就是 model_mm
其中 k = 纸面毫米 / 模型毫米（= 比例分母的倒数）。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

PT2MM = 25.4 / 72.0

# 判定阈值（覆盖率，双向取小）
PASS_COVERAGE = 0.97
WARN_COVERAGE = 0.90

# 文字位置偏差的容许上限（以「几倍字高」计）。
# 用绝对毫米没有意义 —— 排版侧把插入点放在包围盒左下角、比对侧取中心，
# 天然就差半个字宽，用毫米判会恒定误报。
TEXT_SHIFT_RATIO = 2.0

# 尺寸数字与 DXF 实测线段长度的相对容差
SIZE_MATCH_TOL = 0.005


# ==================================================================== 工具
def _pix(v, origin, ppm):
    return int(round((v - origin) * ppm))


def rasterize(segments, origin, ppm, shape):
    """把线段集合栅格化成 bool 掩码。segments: 序列 [(xa,ya,xb,yb), ...]（模型毫米）。"""
    mask = np.zeros(shape, dtype=bool)
    ox, oy = origin
    h, w = shape
    for xa, ya, xb, yb in segments:
        length_px = math.hypot(xb - xa, yb - ya) * ppm
        n = max(2, int(length_px) + 1)
        xs = np.linspace(xa, xb, n)
        ys = np.linspace(ya, yb, n)
        ix = np.rint((xs - ox) * ppm).astype(np.int64)
        iy = np.rint((ys - oy) * ppm).astype(np.int64)
        ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        mask[iy[ok], ix[ok]] = True
    return mask


def dilate(mask, r):
    """膨胀 r 像素作为容差。

    用**可分离的方形结构元**（先沿行、再沿列各做 1D 膨胀），
    比逐像素圆形核快一个数量级，且对覆盖率判定的影响可以忽略
    （对角方向容差放宽到约 sqrt(2)·r）。
    """
    if r <= 0:
        return mask.copy()
    out = mask
    for axis in (0, 1):
        acc = out.copy()
        for d in range(1, r + 1):
            acc |= np.roll(out, d, axis=axis)
            acc |= np.roll(out, -d, axis=axis)
        out = acc
    return out


# ==================================================================== 证据源 A：PDF
def _item_subpaths(item):
    """把 get_drawings() 的一个 item 展开成若干折点序列。"""
    kind = item[0]
    if kind == "l":
        p1, p2 = item[1], item[2]
        return [[(p1.x, p1.y), (p2.x, p2.y)]]
    if kind == "c":
        p1, p2, p3, p4 = item[1], item[2], item[3], item[4]
        pts = []
        for i in range(9):
            t = i / 8.0
            mt = 1.0 - t
            x = (mt ** 3) * p1.x + 3 * mt * mt * t * p2.x + 3 * mt * t * t * p3.x + t ** 3 * p4.x
            y = (mt ** 3) * p1.y + 3 * mt * mt * t * p2.y + 3 * mt * t * t * p3.y + t ** 3 * p4.y
            pts.append((x, y))
        return [pts]
    if kind == "re":
        r = item[1]
        return [[(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)]]
    if kind == "qu":
        q = item[1]
        return [[(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                 (q.lr.x, q.lr.y), (q.ll.x, q.ll.y), (q.ul.x, q.ul.y)]]
    return []


def read_pdf(path):
    """返回 (paper_segments, paper_texts)，**全部是纸面毫米**。

    刻意不在这里做标定/翻转 —— 那是「假设」，不是「证据」。
    换算交给 paper_to_model()，y 轴方向由主流程**自动判定**。
    """
    try:
        import fitz
    except ImportError:
        raise SystemExit("需要 PyMuPDF：pip install pymupdf")

    doc = fitz.open(str(path))
    page = doc[0]
    segs = []
    for d in page.get_drawings():
        for item in d.get("items") or []:
            for sub in _item_subpaths(item):
                for a, b in zip(sub, sub[1:]):
                    segs.append((a[0] * PT2MM, a[1] * PT2MM,
                                 b[0] * PT2MM, b[1] * PT2MM))

    texts = []
    for blk in page.get_text("dict").get("blocks") or []:
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines") or []:
            for span in line.get("spans") or []:
                s = (span.get("text") or "").strip()
                if not s:
                    continue
                bb = span["bbox"]
                cx = (bb[0] + bb[2]) / 2.0 * PT2MM
                cy = (bb[1] + bb[3]) / 2.0 * PT2MM
                texts.append({"text": s, "paper": (cx, cy),
                              "size_pt": float(span.get("size") or 0.0)})
    doc.close()
    return segs, texts


def paper_to_model(segs, k, x0, y0, y_flip=True):
    """纸面毫米线段 -> 模型毫米线段。

    y_flip=True 表示图纸 y 轴向上（PDF 页面 y 轴向下，故取反），
    即 model_y = (y0 - paper_y) / k。同一份 paper 数据可反复用不同 k / 方向换算。
    """
    out = []
    for xa, ya, xb, yb in segs:
        ya2 = (y0 - ya) if y_flip else (ya - y0)
        yb2 = (y0 - yb) if y_flip else (yb - y0)
        out.append(((xa - x0) / k, ya2 / k, (xb - x0) / k, yb2 / k))
    return out


def paper_point_to_model(p, k, x0, y0, y_flip=True):
    x, y = p
    return ((x - x0) / k, ((y0 - y) if y_flip else (y - y0)) / k)


# ==================================================================== 证据源 B：DXF
def _text_center(entity, fallback):
    """取文字实体的**包围盒中心**；失败时退回插入点。

    必须用包围盒中心：排版侧常把插入点放在左下角（LEFT / baseline），
    而 PDF 侧取到的是 span 的几何中心。两者基准不同，直接用插入点比对
    会让长文本恒定偏差约「半个文本长度」，那是方法误差，不是复刻错误。

    用 fast=False 精确测量文字宽度（需要字体文件解析）。
    若字体不可用会抛异常，此时退回插入点，仍能靠「插入点」这条备选基准参与比对。
    """
    try:
        from ezdxf import bbox as ezbbox
        ext = ezbbox.extents([entity], fast=False)
        c = ext.center
        if c is not None:
            return float(c.x), float(c.y)
    except Exception:
        pass
    return float(fallback[0]), float(fallback[1])


def read_dxf(path):
    """独立读 DXF：返回 (segments, texts, bbox)，坐标即模型毫米。"""
    try:
        import ezdxf
    except ImportError:
        raise SystemExit("需要 ezdxf：pip install ezdxf")

    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    segs = []
    texts = []

    def add_arc(c, r, a0, a1, n=36):
        a0, a1 = math.radians(a0), math.radians(a1)
        if a1 <= a0:
            a1 += 2 * math.pi
        pts = [(c[0] + r * math.cos(a0 + (a1 - a0) * i / n),
                c[1] + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]
        for a, b in zip(pts, pts[1:]):
            segs.append((a[0], a[1], b[0], b[1]))

    for e in msp:
        t = e.dxftype()
        try:
            if t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                if e.closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    segs.append((a[0], a[1], b[0], b[1]))
            elif t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    segs.append((a[0], a[1], b[0], b[1]))
            elif t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                segs.append((a.x, a.y, b.x, b.y))
            elif t == "CIRCLE":
                c = e.dxf.center
                add_arc((c.x, c.y), e.dxf.radius, 0, 360)
            elif t == "ARC":
                c = e.dxf.center
                add_arc((c.x, c.y), e.dxf.radius, e.dxf.start_angle, e.dxf.end_angle)
            elif t == "SOLID":
                v = [e.dxf.vtx0, e.dxf.vtx1, e.dxf.vtx3, e.dxf.vtx2]
                pts = [(p.x, p.y) for p in v] + [(v[0].x, v[0].y)]
                for a, b in zip(pts, pts[1:]):
                    segs.append((a[0], a[1], b[0], b[1]))
            elif t == "TEXT":
                p = e.dxf.insert
                cx, cy = _text_center(e, (p.x, p.y))
                texts.append({"text": (e.dxf.text or "").strip(),
                              "model": (cx, cy),
                              "insert": (float(p.x), float(p.y)),
                              "h": float(e.dxf.height or 0.0)})
            elif t == "MTEXT":
                p = e.dxf.insert
                cx, cy = _text_center(e, (p.x, p.y))
                texts.append({"text": e.plain_text().strip(),
                              "model": (cx, cy),
                              "insert": (float(p.x), float(p.y)),
                              "h": float(e.dxf.char_height or 0.0)})
        except Exception:
            continue

    if segs:
        arr = np.asarray(segs, dtype=float)
        bbox = (arr[:, [0, 2]].min(), arr[:, [1, 3]].min(),
                arr[:, [0, 2]].max(), arr[:, [1, 3]].max())
    else:
        bbox = (0.0, 0.0, 0.0, 0.0)
    return segs, texts, bbox


# ==================================================================== ① 几何覆盖率
def coverage_report(pdf_segs, dxf_segs, bbox, ppm, tol_px, max_px=4.0e8):
    ox, oy = bbox[0], bbox[1]
    w = max(2, int((bbox[2] - bbox[0]) * ppm) + 2)
    h = max(2, int((bbox[3] - bbox[1]) * ppm) + 2)
    if float(w) * h > max_px:
        k = math.sqrt(max_px / (float(w) * h))
        ppm = ppm * k
        w = max(2, int((bbox[2] - bbox[0]) * ppm) + 2)
        h = max(2, int((bbox[3] - bbox[1]) * ppm) + 2)
        print("  [INFO] 栅格过大，已自动降到 ppm=%.3f（约 %.2f mm/px）"
              % (ppm, 1.0 / ppm))
    shape = (h, w)

    m_pdf = rasterize(pdf_segs, (ox, oy), ppm, shape)
    m_dxf = rasterize(dxf_segs, (ox, oy), ppm, shape)
    if not m_pdf.any() or not m_dxf.any():
        return {"ok": False, "why": "某一侧几何为空", "cov_pdf_to_dxf": 0.0,
                "cov_dxf_to_pdf": 0.0, "masks": (m_pdf, m_dxf, None, None),
                "shape": shape, "origin": (ox, oy)}

    d_pdf = dilate(m_pdf, tol_px)
    d_dxf = dilate(m_dxf, tol_px)

    cov_p2d = float((m_pdf & d_dxf).sum()) / float(m_pdf.sum())
    cov_d2p = float((m_dxf & d_pdf).sum()) / float(m_dxf.sum())
    both = m_pdf & m_dxf
    only_pdf = m_pdf & ~d_dxf       # 参考图有、复刻没有 => 漏
    only_dxf = m_dxf & ~d_pdf       # 复刻有、参考图没有 => 多
    near = (m_pdf | m_dxf) & ~both & ~only_pdf & ~only_dxf

    return {
        "ok": True,
        "cov_pdf_to_dxf": cov_p2d,
        "cov_dxf_to_pdf": cov_d2p,
        "worst": min(cov_p2d, cov_d2p),
        "shape": shape, "origin": (ox, oy),
        "masks": (m_pdf, m_dxf, only_pdf, only_dxf),
        "for_diff": (both, near, only_pdf, only_dxf),
    }


def _downsample(mask, factor):
    """按 factor 做块级 any 下采样（保形，不丢"有线的块"）。"""
    if factor <= 1:
        return mask
    h, w = mask.shape
    h2, w2 = h // factor, w // factor
    if h2 < 2 or w2 < 2:
        return mask
    m = mask[:h2 * factor, :w2 * factor]
    return m.reshape(h2, factor, w2, factor).any(axis=(1, 3))


def save_diff(res, out_png, title, max_side=3600):
    """输出叠加差分图。

    刻意不用 matplotlib.imshow —— 它会把整幅图转成 float64，
    在 1 亿像素级别直接 OOM。这里改成先分块下采样、再用 PIL 直写。
    """
    from PIL import Image

    both, near, only_pdf, only_dxf = res["for_diff"]
    h, w = both.shape
    factor = max(1, int(math.ceil(max(h, w) / float(max_side))))
    both = _downsample(both, factor)
    near = _downsample(near, factor)
    only_pdf = _downsample(only_pdf, factor)
    only_dxf = _downsample(only_dxf, factor)

    hh, ww = both.shape
    img = np.full((hh, ww, 3), 255, dtype=np.uint8)
    img[near] = (205, 205, 205)      # 灰：容差内
    img[only_pdf] = (214, 60, 60)    # 红：参考图有 / 复刻缺
    img[only_dxf] = (60, 100, 214)   # 蓝：复刻多画
    img[both] = (0, 0, 0)            # 黑：重合
    Image.fromarray(img, mode="RGB").save(str(out_png))
    print("  差分图     : %s  (下采样 1/%d, 输出 %dx%d)"
          % (out_png, factor, ww, hh))
    print("             黑=重合  灰=容差内  红=漏 %d 像素  蓝=多 %d 像素"
          % (int(only_pdf.sum()), int(only_dxf.sum())))


# ==================================================================== ② 尺度自校正
def scan_scale(pdf_paper_segs, dxf_segs, bbox, ppm, tol_px, k, x0, y0,
               span=0.03, steps=13, y_flip=True):
    """扫描 k 的乘性偏差，找覆盖率最高的那一个。"""
    best = None
    rows = []
    for i in range(steps):
        delta = -span + 2 * span * i / (steps - 1)
        kk = k * (1.0 + delta)
        segs = paper_to_model(pdf_paper_segs, kk, x0, y0, y_flip)
        r = coverage_report(segs, dxf_segs, bbox, ppm, tol_px)
        if not r["ok"]:
            continue
        rows.append({"delta": delta, "worst": r["worst"]})
        if best is None or r["worst"] > best["worst"]:
            best = {"delta": delta, "worst": r["worst"], "k": kk}
    return best, rows


# ==================================================================== ③ 文字层
def _norm(s):
    return "".join(s.split())


def _greedy_match(a_list, b_list):
    """一对一贪心匹配（按距离升序取用），避免同一目标被多个源重复占用。

    若同一内容在图上出现多次（如公司名、图签文字），
    各自独立找最近邻会把多个源都吸到同一个目标上，产生假偏差。
    """
    cand = []
    for i, a in enumerate(a_list):
        for j, b in enumerate(b_list):
            d = math.hypot(b["model"][0] - a["model"][0],
                           b["model"][1] - a["model"][1])
            ins = b.get("insert")
            if ins is not None:
                d = min(d, math.hypot(ins[0] - a["model"][0],
                                      ins[1] - a["model"][1]))
            cand.append((d, i, j))
    cand.sort(key=lambda x: x[0])
    ua, ub, out = set(), set(), []
    for d, i, j in cand:
        if i in ua or j in ub:
            continue
        ua.add(i)
        ub.add(j)
        out.append((a_list[i], b_list[j], d))
    return out


def compare_texts(pdf_texts, dxf_texts, tol_px, ppm):
    """文字层比对。

    位置偏差用**相对指标**：偏差 ÷ 双方字高。因为排版侧把文字插入点放在
    包围盒左下角（LEFT/baseline），而比对侧取的是包围盒中心，两者天然差半个字宽。
    用绝对毫米判会恒定误报，用「几倍字高」判才说明问题。
    """
    pdf_c = {}
    for t in pdf_texts:
        pdf_c.setdefault(_norm(t["text"]), []).append(t)
    dxf_c = {}
    for t in dxf_texts:
        dxf_c.setdefault(_norm(t["text"]), []).append(t)

    only_pdf = sorted(k for k in pdf_c if k not in dxf_c)
    only_dxf = sorted(k for k in dxf_c if k not in pdf_c)
    count_gap = {k: (len(pdf_c[k]), len(dxf_c[k]))
                 for k in pdf_c if k in dxf_c and len(pdf_c[k]) != len(dxf_c[k])}

    shifts = []
    ratios = []
    over = []
    worst = []
    for key in pdf_c:
        if key not in dxf_c:
            continue
        for a, bestb, best in _greedy_match(pdf_c[key], dxf_c[key]):
            shifts.append(best)
            hb = max(float(a.get("h") or 0.0), float(bestb.get("h") or 0.0))
            if hb <= 1e-6:
                continue
            # 容忍度取「2 倍字高」与「半个文本长度」的较大者：
            # 短文本（尺寸数字、轴号）用字高判，长文本按自身长度判。
            est_len = max(1.0, float(len(key))) * hb * 0.7
            tol_here = max(TEXT_SHIFT_RATIO * hb, 0.5 * est_len)
            ratios.append(best / hb)
            over.append(best / tol_here)
            worst.append((best / tol_here, key, best, hb, tol_here))
    ratios.sort(reverse=True)
    worst.sort(key=lambda x: -x[0])
    return {
        "pdf_unique": len(pdf_c),
        "dxf_unique": len(dxf_c),
        "only_pdf": only_pdf,
        "only_dxf": only_dxf,
        "count_gap": count_gap,
        "shift_max_mm": max(shifts) if shifts else 0.0,
        "shift_mean_mm": (sum(shifts) / len(shifts)) if shifts else 0.0,
        "max_ratio": ratios[0] if ratios else 0.0,
        "over_limit": sum(1 for r in over if r > 1.0),
        "ratio_limit": TEXT_SHIFT_RATIO,
        "worst": [{"over": round(o, 2), "text": t, "mm": round(mm, 1),
                   "h": round(h, 1), "tol": round(tol, 1)}
                  for o, t, mm, h, tol in worst[:8]],
    }


# ==================================================================== ④ 总尺寸
def check_overall(pdf_texts, dxf_segs, bbox):
    """总尺寸核对：图上最大的尺寸数字，DXF 里是否真有这个长度的线段。

    这条判据的独立点在于 —— 数字来自**图纸作者**，长度来自**独立读出的 DXF 几何**，
    两边都不经过生成侧的换算逻辑。

    刻意*不*用「DXF 整体外包框」去比尺寸数字：全量转绘的 DXF 包含图框、尺寸链、
    门窗表与设计说明，外包框必然远大于建筑本身，那样比只会恒定误报。
    """
    nums = []
    for t in pdf_texts:
        s = t["text"].replace(",", "").strip()
        if s.isdigit():
            v = int(s)
            if 1000 <= v <= 200000:
                nums.append(v)
    if not nums:
        return {"found": False}

    lens_h, lens_v = [], []
    for xa, ya, xb, yb in dxf_segs:
        if abs(ya - yb) < 1e-6:
            lens_h.append(abs(xb - xa))
        elif abs(xa - xb) < 1e-6:
            lens_v.append(abs(yb - ya))

    uniq = sorted(set(nums), reverse=True)[:6]
    matched, missed = [], []
    for v in uniq:
        hit = []
        nearest = None
        for name, lens in (("水平", lens_h), ("垂直", lens_v)):
            if not lens:
                continue
            near = min(lens, key=lambda L: abs(L - v))
            if nearest is None or abs(near - v) < abs(nearest[1] - v):
                nearest = (name, near)
            if abs(near - v) / float(v) <= SIZE_MATCH_TOL:
                hit.append({"value": v, "axis": name, "measured": round(near, 1)})
        if hit:
            matched.extend(hit)
        else:
            missed.append({"value": v,
                           "nearest_axis": nearest[0] if nearest else None,
                           "nearest_len": round(nearest[1], 1) if nearest else None,
                           "gap_pct": round(abs(nearest[1] - v) / float(v) * 100, 2)
                           if nearest else None})
    return {"found": True, "candidates": uniq, "matched": matched,
            "missed": missed, "match_rate": len(matched) / float(len(uniq)),
            "top_matched": any(m["value"] == uniq[0] for m in matched),
            "segments": {"水平": len(lens_h), "垂直": len(lens_v)}}


# ==================================================================== 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description="复刻 1:1 校验器（独立证据源）")
    ap.add_argument("reference", help="参考图（矢量 PDF）")
    ap.add_argument("dxf", help="复刻出的 DXF")
    ap.add_argument("--k", type=float, required=True, help="纸面毫米/模型毫米")
    ap.add_argument("--x0", type=float, default=0.0, help="原点纸面 x (mm)")
    ap.add_argument("--y0", type=float, default=0.0, help="原点纸面 y (mm)")
    ap.add_argument("--tol-px", type=int, default=3, help="容差（像素）")
    ap.add_argument("--ppm", type=float, default=1.0, help="栅格分辨率 px/mm")
    ap.add_argument("--out", default=".", help="输出目录")
    ap.add_argument("--json", default=None, help="报告 JSON 路径")
    ap.add_argument("--no-scan", action="store_true", help="跳过尺度扫描")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print("复刻 1:1 校验报告（独立证据源）")
    print("=" * 68)

    pdf_paper, pdf_texts = read_pdf(args.reference)
    dxf_segs, dxf_texts, dxf_bbox = read_dxf(args.dxf)
    print("参考图路径段 : %d" % len(pdf_paper))
    print("参考图文字   : %d" % len(pdf_texts))
    print("DXF 线段     : %d" % len(dxf_segs))
    print("DXF 文字     : %d" % len(dxf_texts))
    print("DXF 外包框   : x %.1f~%.1f  y %.1f~%.1f  (%.1f x %.1f mm)"
          % (dxf_bbox[0], dxf_bbox[2], dxf_bbox[1], dxf_bbox[3],
             dxf_bbox[2] - dxf_bbox[0], dxf_bbox[3] - dxf_bbox[1]))

    # 联盟包围框：以 DXF 为准，容纳 PDF 侧的少量溢出
    bbox = dxf_bbox
    if dxf_bbox[2] <= dxf_bbox[0]:
        arr = np.asarray(pdf_segs, dtype=float)
        bbox = (arr[:, [0, 2]].min(), arr[:, [1, 3]].min(),
                arr[:, [0, 2]].max(), arr[:, [1, 3]].max())

    print("\n---- ① 几何覆盖率 ----")
    # y 轴方向不假设：两种都算，取覆盖率高的那个。
    # 这本身就是一项独立检查 —— 若生成侧漏了 y 翻转，这里会立刻暴露。
    orient = None
    r_any = None
    for flip in (True, False):
        segs_try = paper_to_model(pdf_paper, args.k, args.x0, args.y0, flip)
        r = coverage_report(segs_try, dxf_segs, bbox, args.ppm, args.tol_px)
        if r_any is None or (r["ok"] and not r_any["ok"]):
            r_any = r
        if r["ok"] and (orient is None or r["worst"] > orient[2]["worst"]):
            orient = (flip, segs_try, r)

    if orient is None:
        cov = r_any or {"ok": False, "why": "无有效结果",
                        "cov_pdf_to_dxf": 0.0, "cov_dxf_to_pdf": 0.0, "worst": 0.0}
        cov["tol_px"] = args.tol_px
        print("  [ERROR] %s" % cov.get("why", "?"))
        y_flip = True
        pdf_segs = paper_to_model(pdf_paper, args.k, args.x0, args.y0, y_flip)
    else:
        y_flip, pdf_segs, cov = orient
        cov["tol_px"] = args.tol_px
        print("  y 轴方向   : %s（由覆盖率自动判定）"
              % ("向上，已翻转" if y_flip else "向下，未翻转"))
        print("  PDF -> DXF : %.2f%%   (参考图上的线有多少被复刻到)"
              % (cov["cov_pdf_to_dxf"] * 100))
        print("  DXF -> PDF : %.2f%%   (复刻的线有多少能在参考图上找到)"
              % (cov["cov_dxf_to_pdf"] * 100))
        print("  重合率     : %.2f%%" % (cov["worst"] * 100))
        save_diff(cov, out_dir / "verify_overlay.png",
                  "复刻叠加差分  黑=重合 灰=容差内 红=漏 蓝=多")
        if not y_flip:
            print("  [WARN] 未翻转方向覆盖率更高 —— 生成侧可能存在 y 轴方向错误")

    print("\n---- ② 尺度自校正 ----")
    scan_best = None
    if args.no_scan:
        print("  （已跳过）")
    else:
        scan_best, rows = scan_scale(pdf_paper, dxf_segs, bbox, args.ppm,
                                     args.tol_px, args.k, args.x0, args.y0,
                                     y_flip=y_flip)
        if scan_best:
            print("  给定 k = %.7f" % args.k)
            print("  最佳 k = %.7f  (偏差 %+.2f%%, 覆盖率 %.2f%%)"
                  % (scan_best["k"], scan_best["delta"] * 100,
                     scan_best["worst"] * 100))
        else:
            print("  扫描未得到有效结果")

    print("\n---- ③ 文字层比对 ----")
    pdf_texts_m = [dict(t, model=paper_point_to_model(
        t["paper"], args.k, args.x0, args.y0, y_flip)) for t in pdf_texts]
    tc = compare_texts(pdf_texts_m, dxf_texts, args.tol_px, args.ppm)
    print("  参考图不同文本 %d 种 / DXF %d 种" % (tc["pdf_unique"], tc["dxf_unique"]))
    print("  仅参考图有 %d 种；仅 DXF 有 %d 种" % (len(tc["only_pdf"]), len(tc["only_dxf"])))
    print("  数量不一致 %d 种" % len(tc["count_gap"]))
    print("  位置偏差   最大 %.1fmm / 均值 %.1fmm；最大 %.1f 倍字高"
          % (tc["shift_max_mm"], tc["shift_mean_mm"], tc["max_ratio"]))
    print("  超容忍度   %d 处（容忍度 = max(%.0f 倍字高, 半个文本长度)）"
          % (tc["over_limit"], tc["ratio_limit"]))
    if tc["worst"]:
        print("    偏差最严重的几处:")
        for it in tc["worst"][:5]:
            print("      %.2f 倍容忍度 (偏 %.1fmm，字高 %.1f，容忍 %.1f)  「%s」"
                  % (it["over"], it["mm"], it["h"], it["tol"], it["text"][:40]))
    if tc["only_pdf"]:
        print("    [仅参考图] " + " | ".join(tc["only_pdf"][:12]))
    if tc["only_dxf"]:
        print("    [仅 DXF]   " + " | ".join(tc["only_dxf"][:12]))

    print("\n---- ④ 总尺寸核对 ----")
    ov = check_overall(pdf_texts, dxf_segs, bbox)
    if not ov["found"]:
        print("  未从参考图提取到尺寸数字")
    else:
        print("  参考图尺寸候选 : %s" % ov["candidates"])
        print("  DXF 线段统计   : %s" % ov["segments"])
        for m in ov["matched"]:
            print("  [OK] 尺寸 %d 与 DXF 中长度 %.1fmm 的线段相符（%s向）"
                  % (m["value"], m["measured"], m["axis"]))
        if ov["missed"]:
            print("  [注意] 以下尺寸数字在 DXF 中没有等长线段（附最接近的实测长度）：")
            for m in ov["missed"]:
                print("         %d  ->  最接近 %s向 %.1fmm（差 %.2f%%）"
                      % (m["value"], m["nearest_axis"], m["nearest_len"],
                         m["gap_pct"]))
            print("         可能原因：原图标注值与该处几何不一致（非 1:1 绘制），")
            print("                   或该尺寸线在图上被图元打断，需人工核对。")

    # ---------------------------------------------------------- 结论
    verdict = "PASS"
    reasons = []
    if cov["ok"]:
        if cov["worst"] < WARN_COVERAGE:
            verdict = "FAIL"
            reasons.append("几何重合率 %.1f%% 低于 %.0f%%" % (cov["worst"] * 100, WARN_COVERAGE * 100))
        elif cov["worst"] < PASS_COVERAGE:
            verdict = "WARN"
            reasons.append("几何重合率 %.1f%% 未达 %.0f%%" % (cov["worst"] * 100, PASS_COVERAGE * 100))
    else:
        verdict = "FAIL"
        reasons.append(cov["why"])
    if scan_best and abs(scan_best["delta"]) > 0.01 and verdict != "FAIL":
        verdict = "WARN" if verdict == "PASS" else verdict
        reasons.append("最佳 k 偏离给定 k %.2f%%（标定可能不准）" % (scan_best["delta"] * 100))
    if tc["only_pdf"] or tc["only_dxf"]:
        if verdict == "PASS":
            verdict = "WARN"
        reasons.append("文字层有 %d/%d 种不一致" % (len(tc["only_pdf"]), len(tc["only_dxf"])))
    # 文字位置：只有**系统性错位**才算问题。
    # 两侧字体度量天然不同（PDF 用真实字形、DXF 用样式估算），个别文字
    # 偏半个到一个字长属方法误差；大面积超限才说明坐标换算真错了。
    pos_budget = max(3, int(0.05 * max(1, tc["pdf_unique"])))
    if tc["over_limit"] > pos_budget:
        if verdict == "PASS":
            verdict = "WARN"
        reasons.append("文字位置系统性错位：%d 处超容忍度（允许 %d 处）"
                       % (tc["over_limit"], pos_budget))
    if ov.get("found"):
        if not ov.get("top_matched"):
            if verdict == "PASS":
                verdict = "WARN"
            reasons.append("最大的尺寸数字 %s 在 DXF 中找不到等长线段 —— 尺度可能不对"
                           % (ov["candidates"][0] if ov["candidates"] else "?"))
        elif ov.get("missed"):
            print("\n  [说明] 有 %d 个尺寸数字未能对应，但**最大尺寸已匹配**，"
                  "判定不受影响（此项不计入结论）" % len(ov["missed"]))

    print("\n" + "=" * 68)
    print("结论：%s" % verdict)
    for r in reasons:
        print("  - %s" % r)
    print("=" * 68)

    if args.json:
        payload = {
            "verdict": verdict, "reasons": reasons,
            "coverage": {k: v for k, v in cov.items() if k not in ("masks", "for_diff")},
            "scale_scan": scan_best,
            "texts": tc,
            "overall": ov,
            "dxf_bbox": dxf_bbox,
        }
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("报告 JSON -> %s" % args.json)
    return 0 if verdict == "PASS" else (2 if verdict == "FAIL" else 1)


if __name__ == "__main__":
    sys.exit(main())
