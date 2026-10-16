"""主入口：图纸规格 JSON → DXF（+ 预览图 + 校验报告）。

用法：
    python build_dxf.py <spec.json> -o <out.dxf> [--png out.png] [--svg out.svg] [--no-validate]

    示例（技能自带的样例）：
    python build_dxf.py ../templates/examples/plan_studio_1f.spec.json \
        -o ../templates/examples/out/plan_studio_1f.dxf --png ../templates/examples/out/plan_studio_1f.png

设计约束（不可违反）：
    本脚本是纯确定性程序。相同 spec 必然产出完全相同的 DXF。
    禁止在此处引入任何模型调用、随机数或时间戳。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _ensure_hash_seed() -> None:
    """保证「同 spec → 字节一致的 DXF」。

    ezdxf 写 OBJECTS 段（LAYOUT / ACDBPLACEHOLDER 等对象）时的顺序
    受 Python 字符串哈希随机化影响，同一个 spec 两次出图会有 4 行对象顺序不同。
    PYTHONHASHSEED 只能在解释器启动前生效，因此这里用子进程重启自身。
    """
    if os.environ.get("PYTHONHASHSEED") == "0":
        return
    if os.environ.get("CAD_DXF_REEXEC") == "1":
        return  # 已在重启后的进程中，避免无限重启
    env = dict(os.environ, PYTHONHASHSEED="0", CAD_DXF_REEXEC="1")
    sys.exit(subprocess.call([sys.executable] + sys.argv, env=env))


_ensure_hash_seed()

from cadlib.export import export_png, export_svg, save_dxf   # noqa: E402
from pipeline import render_spec                            # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CAD 施工图 DXF 生成器（国标）")
    parser.add_argument("spec", help="图纸规格 JSON 路径")
    parser.add_argument("-o", "--out", required=True, help="输出 DXF 路径")
    parser.add_argument("--png", help="同时输出预览 PNG")
    parser.add_argument("--svg", help="同时输出矢量预览 SVG")
    parser.add_argument("--dxf-version", default="R2018",
                        help="DXF 版本，默认 R2018（可选 R2013 / R2010 / R2007）")
    parser.add_argument("--no-validate", action="store_true",
                        help="跳过出图后自检")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    with open(args.spec, "r", encoding="utf-8") as fp:
        spec = json.load(fp)

    doc, ctx, info = render_spec(spec, dxf_version=args.dxf_version)

    save_dxf(doc, args.out)
    if not args.quiet:
        print(f"[ok] DXF  -> {os.path.abspath(args.out)}")
        print(f"     实体 {info['entities']} / 图层 {info['layers']} / "
              f"图幅 {ctx.sheet_size} / 比例 1:{int(ctx.scale)} / "
              f"线宽组 b={ctx.b}mm")

    if args.png:
        export_png(doc, args.png, extents=info["sheet_extents"])
        if not args.quiet:
            print(f"[ok] PNG  -> {os.path.abspath(args.png)}")
    if args.svg:
        export_svg(doc, args.svg)
        if not args.quiet:
            print(f"[ok] SVG  -> {os.path.abspath(args.svg)}")

    if ctx.warnings and not args.quiet:
        print(f"\n[!] 出图提示 {len(ctx.warnings)} 条：")
        for w in ctx.warnings:
            print(f"    - {w}")

    report = None
    if not args.no_validate:
        from validate import validate_dxf
        report = validate_dxf(args.out, spec=spec)

    if report is not None and not args.quiet:
        print()
        print(report.as_text())

    exit_code = 0
    if info.get("layout_errors"):
        print()
        print("===== 版面校验（ERROR）=====")
        for msg in info["layout_errors"]:
            print(f"  [ERROR] LAYOUT: {msg}")
        exit_code = 1

    if report is not None and report.has_error():
        exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
