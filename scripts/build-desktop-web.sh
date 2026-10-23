#!/bin/bash
# 从上游前端源码构建 Hermes 桌面端「Web 版」产物（不含 Electron 主进程）。
# 上游：apps/desktop（Vite + React），产物在 apps/desktop/dist。
# 本脚本做三件事：构建 → 裁掉 Electron 专用文件 → 注入我们自己维护的 web-shim.js 与移动端补丁。
# 用法: bash scripts/build-desktop-web.sh [--skip-build]
set -euo pipefail
TREE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$TREE/desktop-src"
DIST="$SRC/apps/desktop/dist"
WEB="$TREE/app/desktop-web"          # 自维护 web 层（web-shim.js、补丁脚本）
OUT="$TREE/fnpack/app/desktop-app"
export PATH=/var/apps/nodejs_v24/target/bin:$PATH

if [ "${1:-}" != "--skip-build" ]; then
  echo "── 构建上游 renderer ──"
  ( cd "$SRC" && node apps/desktop/scripts/build.mjs )
fi
[ -f "$DIST/index.html" ] || { echo "✗ 构建产物不存在：$DIST/index.html" >&2; exit 1; }

echo "── 组装 Web 产物 ──"
rm -rf "$OUT"; mkdir -p "$OUT"
# 1) 整树拷贝（不含 Electron 主进程与调试产物）
tar -C "$DIST" -cf - \
  --exclude='electron-main.mjs' \
  --exclude='electron-preload.js' \
  --exclude='preview-guest-preload.js' \
  --exclude='renderer-manifest.json' \
  --exclude='hermes-build.json' \
  --exclude='.hermes-product' \
  --exclude='node_modules' \
  . | tar -C "$OUT" -xf -

# 2) 注入 web 层：web-shim 必须在入口模块之前执行（window.hermes 由它提供）
cp "$WEB/web-shim.js" "$OUT/web-shim.js"
python3 - "$OUT/index.html" <<'PY'
import sys, re
p = sys.argv[1]
html = open(p, encoding='utf-8').read()

# 移动端：刘海安全区 + 软键盘收缩
html = html.replace(
    '<meta name="viewport" content="width=device-width, initial-scale=1.0" />',
    '<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover, '
    'interactive-widget=resizes-content" />')

# 启动动画窗口（intro）在 Web 下也要显示：补进窗口白名单
html = html.replace("['hud', 'overlay', 'quick', 'wake']",
                    "['hud', 'overlay', 'quick', 'wake', 'intro']")

# web-shim 必须早于入口 module script
shim = '<script src="./web-shim.js"></script>\n    '
m = re.search(r'<script type="module"[^>]*src="\./assets/[^"]+"', html)
if not m:
    raise SystemExit('✗ index.html 里找不到入口 module script')

# 服务端 shell 注入占位：monitor.js 用字符串替换把这一行换成真实配置
# （BASE_PATH / dashboard session token / profile）。缺了它 shim 会退回 token:""，
# 前端所有 API 401、首屏直接崩 —— 旧 veenyi 产物靠预埋这行，自建产物必须补上。
placeholder = ('<script>window.__HERMES_WEB_CONFIG__ = window.__HERMES_WEB_CONFIG__ || '
               '{ base: "/proxy/dashboard", token: "", profile: null };</script>\n    ')
if '__HERMES_WEB_CONFIG__' not in html:
    # 占位必须排在 web-shim 之前，shim 启动时才会读到已注入的配置
    shim = placeholder + '<script src="./web-shim.js"></script>\n    '
html = html[:m.start()] + shim + html[m.start():]
open(p, 'w', encoding='utf-8').write(html)
print('   ✓ index.html 已注入 web-shim 与移动端补丁')
PY

# 3) 上游 web 产物兼容补丁：normalizeProfileKey 对非字符串入参的容错
#    上游假定 $activeGatewayProfile 一定是字符串（Electron 由 IPC 保证），
#    web 打包版在 gateway 激活阶段可能拿到对象 → 首屏 TypeError → 错误页。
python3 "$(dirname "$0")/patch-desktop-bundle.py" "$OUT" || {
  echo "✗ desktop web 产物补丁失败" >&2; exit 1
}

# 4) 产物自检
for f in index.html web-shim.js assets; do
  [ -e "$OUT/$f" ] || { echo "✗ 产物缺少 $f" >&2; exit 1; }
done
if grep -q 'electron-main\|preview-guest-preload' "$OUT/index.html"; then
  echo "✗ index.html 仍引用 Electron 专用文件" >&2; exit 1
fi
echo "✓ Web 产物就绪：$OUT（$(du -sh "$OUT" | cut -f1)）"
