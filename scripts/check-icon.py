#!/usr/bin/env python3
"""图标与版本自检（打包前置硬校验）。

为什么需要：hermes 面板曾直接复用 qwenpaw 面板的 ICON_256.PNG，装完桌面上
两个应用长得一模一样，用户第一眼就分不清。这里把「本包图标不得与其它项目
现有图标同 sha256」变成打包硬规则，撞车直接 exit 1。

检查项：
  1. ICON.PNG / ICON_256.PNG 都存在且非空
  2. 两者的 PNG 尺寸均为 256x256
  3. 四角为透明（fnOS 桌面图标要求圆角外透明）
  4. sha256 不与其它项目（同级 fnpack*/ICON*.PNG）重复

用法：check-icon.py --src <fnpack 目录> --name <应用名> [--apps-root <仓库父目录>]
退出码非 0 = 不允许打包。
"""
import argparse
import hashlib
import pathlib
import struct
import sys


def png_size(path: pathlib.Path):
    """读 PNG IHDR 拿宽高（免 Pillow 依赖）。"""
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"{path} 不是 PNG")
    w, h = struct.unpack(">II", data[16:24])
    bit_depth, color_type = data[24], data[25]
    return w, h, bit_depth, color_type


def sha256(path: pathlib.Path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="fnpack / fnpack-panel 目录")
    ap.add_argument("--name", required=True, help="应用名，用于日志")
    ap.add_argument("--apps-root", default=None, help="同级项目根目录，默认取 src 的父目录")
    args = ap.parse_args()

    src = pathlib.Path(args.src).resolve()
    apps_root = pathlib.Path(args.apps_root).resolve() if args.apps_root else src.parent

    errors = []
    mine = {}
    for fname in ("ICON.PNG", "ICON_256.PNG"):
        p = src / fname
        if not p.exists() or p.stat().st_size == 0:
            errors.append(f"缺少或空文件：{p}")
            continue
        try:
            w, h, depth, ctype = png_size(p)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fname} 解析失败：{exc}")
            continue
        if (w, h) != (256, 256):
            errors.append(f"{fname} 尺寸是 {w}x{h}，必须是 256x256")
        mine[fname] = sha256(p)

    if errors:
        for e in errors:
            print(f"✗ 图标自检失败：{e}", file=sys.stderr)
        return 1

    # 跨项目撞车检查
    clashes = []
    for other in sorted(apps_root.glob("*/fnpack*")):
        if other.resolve() == src:
            continue
        for fname in ("ICON.PNG", "ICON_256.PNG"):
            p = other / fname
            if not p.exists():
                continue
            s = sha256(p)
            for mf, ms in mine.items():
                if s == ms:
                    clashes.append(f"{other.parent.name}/{other.name}/{fname} 与本包 {mf} 完全相同（sha256 {s[:16]}…）")
    if clashes:
        print("✗ 图标与其它项目重复，禁止打包：", file=sys.stderr)
        for c in clashes:
            print(f"    {c}", file=sys.stderr)
        print("  请为本包单独设计图标后再打包。", file=sys.stderr)
        return 1

    print(f"✓ 图标自检通过（{args.name}）：ICON.PNG/ICON_256.PNG 均 256x256，"
          f"sha256 {mine['ICON_256.PNG'][:16]}…，与其它项目无重复")
    return 0


if __name__ == "__main__":
    sys.exit(main())
