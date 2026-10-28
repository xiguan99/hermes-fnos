#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# 生成 hermes-agent 的内置运行时包 fnpack/venv-bundle.tar.gz
#
# 为什么要自带 Python 3.14：
#   上游 hermes 0.22.5 起，pyproject.toml 里 49 处依赖标记为
#   `; python_version >= '3.14'`，并明写「3.11 只是安装桥，运行必须是 3.14」。
#   系统只有 3.11/3.12 → 只装到 76 个包（应有 255 个），网关起来就
#   ModuleNotFoundError: No module named 'rich'。
#   所以在包里带一份 CPython 3.14（python-build-standalone）+ 完整 venv。
#
# 产物结构（解压到 DATA_DIR 后）：
#   venv/.base/                       CPython 3.14.7 独立运行时
#   venv/{bin,lib,pyvenv.cfg}         uv sync 出来的 venv（255 包）
#   venv_fixup.py                     安装时重写路径用的补丁脚本
#
# 用法：./scripts/build-venv-bundle.sh
# 前置：uv（/vol1/@appcenter/hermes-agent/bin/uv 或 PATH 里的 uv）
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ROOT}/fnpack/app/hermes-src"
OUT="${ROOT}/fnpack/venv-bundle.tar.gz"
PY_VER="${PY_VER:-3.14}"
UV="${UV:-/vol1/@appcenter/hermes-agent/bin/uv}"
[ -x "$UV" ] || UV="$(command -v uv)"
PYRT_DIR="${PYRT_DIR:-/tmp/pyrt}"
WORK="${WORK:-/tmp/hermes-venv-build}"
VENV="${WORK}/venv"
STAGE="${WORK}/stage"
IDX="${UV_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

export UV_PYTHON_INSTALL_DIR="${PYRT_DIR}"
export UV_INDEX_URL="${IDX}"
export UV_LINK_MODE=copy

echo "── 1. 准备 CPython ${PY_VER} 独立运行时 ──"
"$UV" python install "$PY_VER" 2>&1 | tail -2
PYBIN="$(ls -d "${PYRT_DIR}"/cpython-${PY_VER}*-linux-x86_64-gnu 2>/dev/null | grep -v freethreaded | head -1)"
[ -n "${PYBIN}" ] && [ -x "${PYBIN}/bin/python${PY_VER}" ] || { echo "✗ 找不到 ${PY_VER} 运行时" >&2; exit 1; }
echo "   运行时：${PYBIN} ($(du -sh "${PYBIN}" | cut -f1))"

echo "── 2. 按 uv.lock 装全量依赖（--frozen，含 all + voice extra）──"
rm -rf "${VENV}" "${STAGE}"
mkdir -p "${WORK}" "${STAGE}/venv"
( cd "${SRC}" && UV_PROJECT_ENVIRONMENT="${VENV}" "$UV" sync --frozen \
    --python "${PYBIN}/bin/python${PY_VER}" --no-dev --extra all --extra voice 2>&1 | tail -5 )
PKGS=$(ls "${VENV}/lib/python${PY_VER}/site-packages" | wc -l)
echo "   site-packages 条目数：${PKGS}"
"${VENV}/bin/python3" -c "import rich, openai, httpx, pydantic, ruamel.yaml; print('   imports OK')"
[ "$("${VENV}/bin/python3" --version | awk '{print $2}' | cut -d. -f1-2)" = "$(echo ${PY_VER} | cut -d. -f1-2)" ] \
  || { echo "✗ venv 解释器版本不是 ${PY_VER}" >&2; exit 1; }

echo "── 3. 瘦身（只删缓存/测试数据，不动依赖）──"
find "${VENV}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "${VENV}" -name '*.pyc' -delete 2>/dev/null || true
rm -rf "${VENV}"/lib/python${PY_VER}/site-packages/{pip/_vendor/distlib/t32.exe,pip/_vendor/distlib/t64.exe} 2>/dev/null || true

echo "── 4. 放入运行时并组装 ──"
cp -a "${PYBIN}" "${STAGE}/venv/.base"
rm -rf "${STAGE}/venv/.base/lib/python${PY_VER}/test" \
       "${STAGE}/venv/.base/lib/python${PY_VER}/ensurepip" \
       "${STAGE}/venv/.base/include" 2>/dev/null || true
# venv 本体（不含自带 .base）
( cd "${VENV}" && tar cf - --exclude=.base . ) | ( cd "${STAGE}/venv" && tar xf - )
cp "${ROOT}/scripts/venv_fixup.py" "${STAGE}/venv_fixup.py"
chmod +x "${STAGE}/venv_fixup.py"
# 记录构建机路径，安装期 venv_fixup.py 据此把写死的路径改到真实安装位置
python3 - "${STAGE}/venv_build_info.json" "${VENV}" "${SRC}" <<'PY'
import json, sys
out, venv, src = sys.argv[1:4]
json.dump({"oldVenv": venv, "oldSrc": src}, open(out, "w"), ensure_ascii=False, indent=2)
PY

echo "── 5. 打 tar.gz ──"
rm -f "${OUT}"
tar czf "${OUT}" -C "${STAGE}" venv venv_fixup.py venv_build_info.json
SIZE=$(stat -c %s "${OUT}")
echo "✓ ${OUT}  ${SIZE} B ($(du -h "${OUT}" | cut -f1))  sha256=$(sha256sum "${OUT}" | awk '{print $1}')"
echo "   展开体积：$(du -sh "${STAGE}/venv" | cut -f1)"
