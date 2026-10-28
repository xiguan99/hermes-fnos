#!/usr/bin/env python3
"""安装期补丁：把打包时预构建的 venv 搬到真实安装路径上。

背景：venv 里写死了构建机的绝对路径（shebang、pyvenv.cfg、editable finder、
direct_url.json），装机目录不同，直接解压跑不起来（历史上就是
`spawn .../venv/bin/hermes ENOENT` + `No module named 'rich'` 的来源）。
本脚本只做路径改写，不装任何包，秒级完成。

由 cmd/install_callback 调用：
    <venv>/.base/bin/python3.14 venv_fixup.py --venv <venv> --src <hermes-src>

幂等：重复执行结果一致。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import stat
import sys
from pathlib import Path


def _read_info(here: Path) -> dict:
    p = here / "venv_build_info.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _runtime_bin(base: Path) -> Path:
    cands = sorted(glob.glob(str(base / "bin" / "python3.*")))
    cands = [c for c in cands if not c.endswith(".py") and not c.endswith("-config")]
    if not cands:
        raise SystemExit(f"fixup: 运行时缺少解释器：{base}/bin/python3.*")
    return Path(cands[-1])


def fix_pyvenv_cfg(venv: Path, base: Path, pybin: Path) -> None:
    cfg = venv / "pyvenv.cfg"
    want = {
        "home": str(base / "bin"),
        "executable": str(pybin),
        "include-system-site-packages": "false",
    }
    lines, seen = [], set()
    for line in cfg.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else None
        if key in want:
            lines.append(f"{key} = {want[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for k, v in want.items():
        if k not in seen:
            lines.append(f"{k} = {v}")
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fix_symlinks(venv: Path, pybin: Path) -> None:
    binp = venv / "bin"
    rel = os.path.relpath(pybin, binp)  # 相对链接：整目录可再搬迁
    names = {"python", "python3", f"python{pybin.name.split('python')[-1]}"}
    names.add(pybin.name)
    for name in names:
        dst = binp / name
        if dst.is_symlink() or dst.exists():
            dst.unlink()
        os.symlink(rel, dst)


def _iter_files(root: Path):
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            yield p


def rewrite_bin(venv: Path, old_venv: str) -> None:
    """bin/* 的 shebang 指向本 venv；activate 里的旧路径一并替换。"""
    shebang_new = f"#!{venv}/bin/python"
    for p in (venv / "bin").iterdir():
        if p.is_symlink() or not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if data.startswith(b"#!") and b"python" in data.split(b"\n", 1)[0]:
            first, _, rest = data.partition(b"\n")
            p.write_bytes(shebang_new.encode() + b"\n" + rest)
            continue
        if p.name.startswith("activate") and old_venv and old_venv.encode() in data:
            p.write_bytes(data.replace(old_venv.encode(), str(venv).encode()))


def rewrite_site_packages(venv: Path, old_venv: str, old_src: str, src: str) -> None:
    """editable finder / .pth / direct_url.json 里的构建机路径改成安装路径。"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    sp = None
    for cand in (venv / "lib").glob("python*/site-packages"):
        sp = cand
        break
    if sp is None:
        raise SystemExit("fixup: 找不到 site-packages")
    targets = list(sp.glob("__editable__*_finder.py")) + list(sp.glob("*.pth"))
    targets += list(sp.glob("*.dist-info/direct_url.json"))
    for p in targets:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        new = data
        if old_src:
            new = new.replace(old_src.encode(), src.encode())
        if old_venv:
            new = new.replace(old_venv.encode(), str(venv).encode())
        if new != data:
            p.write_bytes(new)


def rewrite_leftover_venv_paths(venv: Path, old_venv: str) -> int:
    """兜底：site-packages 里还有零星写死旧 venv 路径的文本文件（如 _virtualenv.py）。"""
    if not old_venv:
        return 0
    needle = old_venv.encode()
    fixed = 0
    for p in _iter_files(venv):
        rel = p.relative_to(venv)
        if str(rel).startswith(".base"):
            continue
        if p.suffix in {".so", ".pyc", ".a", ".o", ".png", ".jpg", ".woff", ".woff2", ".ttf", ".gz", ".whl"}:
            continue
        try:
            if p.stat().st_size > 4_000_000:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        if needle in data:
            p.write_bytes(data.replace(needle, str(venv).encode()))
            fixed += 1
    return fixed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--old-venv", default="")
    ap.add_argument("--old-src", default="")
    args = ap.parse_args()

    venv = Path(args.venv).resolve()
    src = str(Path(args.src).resolve())
    base = venv / ".base"
    if not base.is_dir():
        raise SystemExit(f"fixup: 运行时目录缺失：{base}")

    info = _read_info(venv.parent)
    old_venv = args.old_venv or info.get("oldVenv", "")
    old_src = args.old_src or info.get("oldSrc", "")

    pybin = _runtime_bin(base)
    fix_pyvenv_cfg(venv, base, pybin)
    fix_symlinks(venv, pybin)
    rewrite_bin(venv, old_venv)
    rewrite_site_packages(venv, old_venv, old_src, src)
    leftover = rewrite_leftover_venv_paths(venv, old_venv)

    # 校验
    py = venv / "bin" / "python3"
    probe = os.spawnv(os.P_WAIT, str(py), [str(py), "-c", "import rich, openai, httpx; print('fixup ok')"])
    print(f"fixup: runtime={pybin.name} leftover_rewritten={leftover} probe_rc={probe}")
    return 0 if probe == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
