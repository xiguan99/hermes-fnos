#!/usr/bin/env python3
"""对 desktop web 产物做最小的兼容补丁（构建后、打包前执行）。

为什么需要：上游 0.22.2+ 的 web 产物里 `normalizeProfileKey()` 直接对入参调用
`.trim()`，假定调用方一定给字符串。web 打包版里 `$activeGatewayProfile` 在
gateway 激活阶段有可能拿到对象（Electron 下由 IPC 给字符串，web 下由
`window.hermesDesktop.getConnection()` 这条路给描述符），于是首屏渲染直接抛
`TypeError: (e ?? "").trim is not a function`，根 error boundary 接管，用户看到
"Something broke in the interface"。

补丁只做两件事，不改变语义：
1. 入参不是字符串时，按上游意图归一成空串（→ 最终仍是 `default`）；
2. 对象形态若带 name/key/profile 字段则取之，否则空串。

用法：patch-desktop-bundle.py <desktop-app 目录>
"""
import glob
import os
import re
import sys

OLD = 'function w(e){return(e??``).trim()||`default`}'
NEW = (
    'function w(e){return(typeof e===`string`?e:'
    '(e&&typeof e===`object`?(e.name??e.key??e.profile??``):``)).trim()||`default`}'
)

# 早前调试用的诊断版，重跑时要能覆盖掉
DIAG_MARK = '"[diag] normalizeProfileKey got"'


# 0.22.5 起上游改了压缩产物形态：normalizeProfileKey 不再叫 `w`，同形态代码散在多个 chunk。
# 这里按"字形"打补丁：凡是 `(IDENT??``).trim()||`default`` 都改成对非字符串入参容错。
_SHAPE = re.compile(r"\((\w+)\?\?``\)\.trim\(\)\|\|`default`")
def _safe_sub(m):
    v = m.group(1)
    return (f"((typeof {v}===`string`?{v}:({v}&&typeof {v}===`object`"
            f"?({v}.name??{v}.key??{v}.profile??``):``))??``).trim()||`default`")
def patch_shapes(root: str) -> int:
    n = 0
    for f in glob.glob(os.path.join(root, "assets", "*.js")):
        s = open(f, encoding="utf-8").read()
        s2, k = _SHAPE.subn(_safe_sub, s)
        if k:
            open(f, "w", encoding="utf-8").write(s2); n += k
    return n


def patch(root: str) -> int:
    hits = 0
    for f in glob.glob(os.path.join(root, "assets", "profile-*.js")):
        s = open(f, encoding="utf-8").read()
        if DIAG_MARK in s:
            # 诊断版：整段函数换回干净版本
            import re

            s2 = re.sub(
                r"function w\(e\)\{if\(typeof e!==\"string\"\).*?\|\|`default`\}",
                NEW,
                s,
                count=1,
                flags=re.S,
            )
            if s2 != s:
                open(f, "w", encoding="utf-8").write(s2)
                print(f"cleaned diag build: {f}")
                hits += 1
            continue
        if OLD in s:
            open(f, "w", encoding="utf-8").write(s.replace(OLD, NEW))
            print(f"patched: {f}")
            hits += 1
    return hits


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    total = 0
    for root in sys.argv[1:]:
        if not os.path.isdir(root):
            print(f"skip (not a dir): {root}")
            continue
        n = patch(root)
        n += patch_shapes(root)
        total += n
        if n == 0:
            print(f"warning: no profile-*.js matched in {root}")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
