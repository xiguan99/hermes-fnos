#!/bin/bash
# 本机测试实例脚手架（不碰生产 /vol1/@appcenter/hermes-agent 与 /vol1/@apphome/hermes-agent）
# 用法: bash scripts/test-instance.sh setup|start|stop|status
set -u
TREE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT=/tmp/h1
APP=$ROOT/app
HOME_DIR=$ROOT/home
VAR=$ROOT/var
DATA=$HOME_DIR/data
PORT=${UI_PORT:-2282}
# 测试实例必须用独立端口：monitor.js 的 gateway(8742)/dashboard(9219) 默认值与装机版相同，
# 共用会互相 forceKillHermes（实测把生产 gateway/dashboard 干掉过）
GWPORT=${GATEWAY_PORT:-8743}
DBPORT=${DASHBOARD_PORT:-9220}
LOG=$VAR/info.log

export PATH=/var/apps/nodejs_v24/target/bin:$PATH

setup() {
  echo "== 重建测试实例 $ROOT（端口 $PORT）=="
  rm -rf "$APP" "$HOME_DIR" "$VAR"
  mkdir -p "$APP" "$DATA" "$VAR"

  # APPDEST 内容
  cp -a "$TREE/fnpack/app/server"      "$APP/server"
  cp -a "$TREE/fnpack/app/ui"          "$APP/ui"
  cp -a "$TREE/fnpack/app/desktop-app" "$APP/desktop-app"
  cp -a "$TREE/fnpack/app/config"      "$APP/config"
  cp -a "$TREE/fnpack/app/hermes-src" "$APP/hermes-src"
  mkdir -p "$APP/config/prompts"
  cp -a "$TREE/fnpack/config/prompts/." "$APP/config/prompts/"
  # 版本号跟随维护树，别再写死（否则面板显示的本体版本与包不一致）
  tr -d "[:space:]" < "$TREE/fnpack/app/VERSION" > "$APP/VERSION"

  echo "-- 解压内置 venv --"
  tar xzf "$TREE/fnpack/venv-bundle.tar.gz" -C "$DATA"
  [ -x "$DATA/venv/bin/python3" ] || { echo "✗ venv 解压失败"; exit 1; }

  # 本体版本覆盖：monitor.js 的候选清单里有装机版 manifest 路径，测试实例必须自己钉住
  # 覆盖文件与 manifest 同一套解析正则（^version\s*=\s*(\S+)），只写纯版本号读不到
  printf "version = %s\n" "$(tr -d "[:space:]" < "$TREE/fnpack/app/VERSION")" > "$VAR/app_version"

  # install-stamp.json 必须跟着当前版本走（dashboard /api/health 与 web 版 appVersion 都读它）
  python3 "$APP/hermes-src/scripts/write_install_stamp.py" \
    --output "$APP/hermes-src/install-stamp.json" \
    --commit "$(git -C "$TREE" rev-parse HEAD 2>/dev/null || echo test-instance)" \
    --branch fnos \
    --base-version "$(cat "$APP/VERSION")" --display-version "$(cat "$APP/VERSION")" --distance 0 \
    --source build --distribution desktop-app --update-mechanism app-installer >/dev/null 2>&1 || true

  # editable 路径重写到测试 APPDEST
  for f in "$DATA"/venv/lib/python*/site-packages/__editable___hermes_agent_*_finder.py; do
    [ -f "$f" ] && sed -i "s|/vol3/@appcenter/hermes-agent/hermes-src|$APP/hermes-src|g; s|/vol1/@appcenter/hermes-agent/hermes-src|$APP/hermes-src|g" "$f"
  done
  sed -i "s|/vol3/@apphome/hermes-agent/data/venv|$DATA/venv|g; s|/vol1/@apphome/hermes-agent/data/venv|$DATA/venv|g" "$DATA/venv/bin/hermes" 2>/dev/null || true

  # uv 软链
  mkdir -p "$DATA/bin"
  cp "$TREE/fnpack/bin/uv" "$DATA/bin/uv" 2>/dev/null || true
  chmod +x "$DATA/bin/uv" 2>/dev/null || true
  ln -sf "$DATA/bin/uv" "$DATA/venv/bin/uv" 2>/dev/null || true

  # 基础数据目录
  mkdir -p "$DATA"/{sessions,skills,workspace,logs,memories,hooks,bin,cron/output}

  # prompts
  cp -a "$APP/config/prompts/skills/." "$DATA/skills/" 2>/dev/null || true
  for f in SOUL.md AGENTS.md config.yaml; do
    [ -f "$APP/config/prompts/$f" ] && [ ! -f "$DATA/$f" ] && cp "$APP/config/prompts/$f" "$DATA/$f"
  done
  echo "== setup 完成：$DATA/venv/bin/python3 --version =="
  "$DATA/venv/bin/python3" --version
}

start() {
  env -i PATH=/var/apps/nodejs_v24/target/bin:/usr/local/bin:/usr/bin:/bin \
      HOME="$DATA" \
      TRIM_APPDEST="$APP" TRIM_PKGHOME="$HOME_DIR" TRIM_PKGVAR="$VAR" \
      UI_PORT="$PORT" GATEWAY_PORT="$GWPORT" DASHBOARD_PORT="$DBPORT" BASE_PATH="${BASE_PATH:-/app/hermes-agent}" \
      bash "$TREE/fnpack/cmd/main" start
  sleep 1
  echo "-- log tail --"; tail -12 "$VAR/info.log" 2>/dev/null
}

stop() {
  env -i PATH=/var/apps/nodejs_v24/target/bin:/usr/local/bin:/usr/bin:/bin \
      TRIM_APPDEST="$APP" TRIM_PKGHOME="$HOME_DIR" TRIM_PKGVAR="$VAR" \
      bash "$TREE/fnpack/cmd/main" stop
}

status() {
  env -i PATH=/var/apps/nodejs_v24/target/bin:/usr/local/bin:/usr/bin:/bin \
      TRIM_APPDEST="$APP" TRIM_PKGHOME="$HOME_DIR" TRIM_PKGVAR="$VAR" \
      bash "$TREE/fnpack/cmd/main" status && echo "running (exit 0)" || echo "stopped (exit $?)"
}

case "${1:-}" in
  setup) setup ;;
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 setup|start|stop|status"; exit 1 ;;
esac
