#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# hermes-agent / hermes-panel 打包脚本（自维护）
#
# 为什么不用 fnpack：fnpack 只打包白名单目录（app/cmd/config/wizard/
# manifest/ICON/LICENSE），会丢掉 app.tgz 内的 venv-bundle.tar.gz 与
# bin/uv，而且要求 version 是 x.y.z[-r]（不接受 0.22.2.2 这种 4 段）。
# 本体旧包就是手工 tar 打的，这里沿用同一套结构。
#
# 用法：
#   ./scripts/build-fpk.sh agent     # 本体 hermes-agent
#   ./scripts/build-fpk.sh panel     # 面板 hermes-panel
#   ./scripts/build-fpk.sh all       # 两个都打
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="${ROOT}/dist"
mkdir -p "${DIST}"

pack_agent() {
  local SRC="${ROOT}/fnpack"
  local VER
  # 对外版本号取自 app/VERSION（0.22.2.2 这种 4 段写法）；
  # manifest 里的 0.22.2-2 只是给 fnpack 校验用的等价写法
  local DISPLAY_VER
  DISPLAY_VER="$(tr -d '[:space:]' < "${SRC}/app/VERSION")"
  local BUILD
  BUILD="$(mktemp -d)/agent"
  mkdir -p "${BUILD}/app" "${BUILD}/fpk"

  echo "── 组装 ${DISPLAY_VER} ──"
  # app.tgz 内容 = 部署目录（APP_DIR）内容
  cp -a "${SRC}/app/." "${BUILD}/app/"
  cp "${SRC}/venv-bundle.tar.gz" "${BUILD}/app/venv-bundle.tar.gz"
  mkdir -p "${BUILD}/app/bin"
  [ -f "${SRC}/bin/uv" ] && cp "${SRC}/bin/uv" "${BUILD}/app/bin/uv"
  echo "${DISPLAY_VER}" > "${BUILD}/app/VERSION"

  # install-stamp.json：hermes 的版本/来源真相（hermes_cli/version_info.py → pm/paths.py
  # install_stamp_path() = <hermes-src 根>/install-stamp.json）。缺它时 dashboard 的
  # /api/health 会返回 version:"unknown"，桌面端与「关于」页也读不到版本。
  python3 "${SRC}/app/hermes-src/scripts/write_install_stamp.py" \
    --output "${BUILD}/app/hermes-src/install-stamp.json" \
    --commit "$(git -C "${ROOT}" rev-parse HEAD 2>/dev/null || echo fnos-build)" \
    --branch "${STAMP_BRANCH:-fnos}" \
    --base-version "${DISPLAY_VER}" --display-version "${DISPLAY_VER}" --distance 0 \
    --source build --distribution desktop-app --update-mechanism app-installer

  # 前置校验
  for f in server/monitor.js desktop-app/index.html hermes-src/hermes_cli/web_dist/index.html venv-bundle.tar.gz; do
    [ -e "${BUILD}/app/$f" ] || { echo "✗ 缺少 app/$f" >&2; exit 1; }
  done
  # 隐私扫描
  local LEAKS
  LEAKS="$(grep -rnE 'password[=:][^ ]{4,}|token[=:][A-Za-z0-9_\-.]{20,}|/vol[0-9]+/@app(home|data)/' \
      "${BUILD}/app/server" "${BUILD}/app/config" 2>/dev/null \
      | grep -vE '\.pyc|app_version|password[:=](password|true|false)|token[:=](token|true|false)' | head -5 || true)"
  [ -z "${LEAKS}" ] || { echo "✗ 隐私扫描命中："; echo "${LEAKS}"; exit 1; }
  echo "✓ 前置校验 + 隐私扫描通过"

  tar czf "${BUILD}/fpk/app.tgz" -C "${BUILD}/app" .
  local MD5
  MD5="$(md5sum "${BUILD}/fpk/app.tgz" | awk '{print $1}')"
  echo "app.tgz: $(du -h "${BUILD}/fpk/app.tgz" | cut -f1) md5=${MD5}"

  cp "${SRC}/manifest" "${BUILD}/fpk/manifest"
  cp -a "${SRC}/cmd" "${SRC}/config" "${SRC}/wizard" "${BUILD}/fpk/"
  cp "${SRC}/ICON.PNG" "${SRC}/ICON_256.PNG" "${SRC}/LICENSE" "${BUILD}/fpk/"
  sed -i "s/^version.*=.*/version               = ${DISPLAY_VER}/" "${BUILD}/fpk/manifest"
  if grep -q '^checksum' "${BUILD}/fpk/manifest"; then
    sed -i "s/^checksum.*=.*/checksum              = ${MD5}/" "${BUILD}/fpk/manifest"
  else
    echo -e "checksum              = ${MD5}" >> "${BUILD}/fpk/manifest"
  fi

  local OUT="${DIST}/hermes-agent_v${DISPLAY_VER}_x86.fpk"
  rm -f "${OUT}"
  tar czf "${OUT}" -C "${BUILD}/fpk" manifest app.tgz cmd config wizard ICON.PNG ICON_256.PNG LICENSE
  echo "✓ ${OUT} ($(du -h "${OUT}" | cut -f1))"
  rm -rf "${BUILD}"
}

pack_panel() {
  local SRC="${ROOT}/fnpack-panel"
  local VER
  VER="$(tr -d '[:space:]' < "${SRC}/VERSION")"
  local BUILD
  BUILD="$(mktemp -d)/panel"
  mkdir -p "${BUILD}/app" "${BUILD}/fpk"

  echo "── 组装面板 ${VER} ──"
  cp -a "${SRC}/app/." "${BUILD}/app/"
  echo "${VER}" > "${BUILD}/app/VERSION"
  # 面板资源包（自更新用）另发一个资产
  tar czf "${DIST}/hermes-panel-res_v${VER}.tar.gz" -C "${BUILD}/app/panel_dist" .
  echo "✓ 资源包 hermes-panel-res_v${VER}.tar.gz ($(du -h "${DIST}/hermes-panel-res_v${VER}.tar.gz" | cut -f1))"

  tar czf "${BUILD}/fpk/app.tgz" -C "${BUILD}/app" .
  local MD5
  MD5="$(md5sum "${BUILD}/fpk/app.tgz" | awk '{print $1}')"
  cp "${SRC}/manifest" "${BUILD}/fpk/manifest"
  cp -a "${SRC}/cmd" "${SRC}/config" "${SRC}/wizard_install.json" "${SRC}/wizard_uninstall.json" "${BUILD}/fpk/"
  cp "${SRC}/ICON.PNG" "${SRC}/ICON_256.PNG" "${BUILD}/fpk/"
  if grep -q '^checksum' "${BUILD}/fpk/manifest"; then
    sed -i "s/^checksum.*=.*/checksum              = ${MD5}/" "${BUILD}/fpk/manifest"
  else
    echo -e "checksum              = ${MD5}" >> "${BUILD}/fpk/manifest"
  fi

  local OUT="${DIST}/hermes-panel_v${VER}_x86.fpk"
  rm -f "${OUT}"
  tar czf "${OUT}" -C "${BUILD}/fpk" manifest app.tgz cmd config wizard_install.json wizard_uninstall.json ICON.PNG ICON_256.PNG
  echo "✓ ${OUT} ($(du -h "${OUT}" | cut -f1))"
  rm -rf "${BUILD}"
}

case "${1:-all}" in
  agent) pack_agent ;;
  panel) pack_panel ;;
  all)   pack_agent; pack_panel ;;
  *) echo "用法: $0 {agent|panel|all}" >&2; exit 1 ;;
esac
