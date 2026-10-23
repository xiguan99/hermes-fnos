/* Hermes Panel — 前端（无框架，原生 JS） */
'use strict';

const ICONS = {
  dashboard: '<path d="M3 13h8V3H3v10Zm10 8h8V11h-8v10ZM3 21h8v-6H3v6ZM13 9h8V3h-8v6Z"/>',
  power: '<path d="M11 2h2v10h-2z"/><path d="M6.3 5.3l1.4 1.4A6 6 0 1 0 18 12a6 6 0 0 0-1.7-5.3l1.4-1.4A8 8 0 1 1 6.3 5.3Z"/>',
  tag: '<path d="M12 2 2 12l10 10 10-10L12 2Zm0 3.4 6.6 6.6-6.6 6.6L5.4 12 12 5.4Z"/>',
  'file-text': '<path d="M6 2h8l4 4v16H6V2Zm7 1.5V7h3.5L13 3.5ZM8 11h8v1.6H8V11Zm0 3.4h8v1.6H8v-1.6Zm0 3.4h5.5V19H8v-1.2Z"/>',
  sliders: '<path d="M4 6h10v2H4zM18 6h2v2h-2zM4 11h4v2H4zM12 11h8v2h-8zM4 16h12v2H4zM18 16h2v2h-2z"/>',
  archive: '<path d="M3 4h18v4H3zM5 9h14v11H5zM9 12h6v2H9z"/>',
  shield: '<path d="M12 2 4 5v7c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10V5l-8-3Zm0 2.2 6 2.2V12c0 4-2.6 6.9-6 8.1-3.4-1.2-6-4.1-6-8.1V6.4l6-2.2Z"/>',
  settings: '<path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm9 4a7 7 0 0 1-.1 1.2l2 1.5-2 3.4-2.3-1a7.6 7.6 0 0 1-2 1.2l-.3 2.5h-4l-.3-2.5a7.6 7.6 0 0 1-2-1.2l-2.3 1-2-3.4 2-1.5A7 7 0 0 1 3 12c0-.4 0-.8.1-1.2l-2-1.5 2-3.4 2.3 1a7.6 7.6 0 0 1 2-1.2L7.7 3h4l.3 2.5c.7.3 1.4.7 2 1.2l2.3-1 2 3.4-2 1.5c.1.4.1.8.1 1.2Z"/>',
  refresh: '<path d="M12 4V1L7 6l5 5V7a5 5 0 1 1-5 5H5a7 7 0 1 0 7-8Z"/>',
  external: '<path d="M14 3h7v7h-2V6.4l-8.3 8.3-1.4-1.4L17.6 5H14V3ZM5 5h6v2H7v10h10v-4h2v6H5V5Z"/>',
};
function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">${ICONS[name] || ICONS.dashboard}</svg>`;
}

const VIEWS = [
  ['overview', '总览', 'dashboard'],
  ['service', '运行控制', 'power'],
  ['versions', '版本管理', 'tag'],
  ['logs', '日志', 'file-text'],
  ['config', '配置', 'sliders'],
  ['snapshots', '快照', 'archive'],
  ['access', '授权与访问', 'shield'],
  ['settings', '设置', 'settings'],
  ['update', '面板更新', 'refresh'],
];

const state = {
  overview: null,
  view: 'overview',
  me: {},
  hermesUrl: '',
  timer: null,
  logName: 'app',
  autoScroll: true,
};

/* ── 基础 ─────────────────────────────────────── */
/* 网关前缀兼容：从当前页面路径推出相对 base。
   直连 2281 → "/"；飞牛网关 → "/app/hermes-panel/"。
   所有接口都用相对路径，绝不写死根绝对路径（否则前缀下全 404）。 */
function apiBase() {
  let p = location.pathname || '/';
  if (!p.endsWith('/')) p = p.replace(/\/[^\/]*$/, '/');
  return p;
}
function apiUrl(path) {
  return apiBase() + String(path).replace(/^\/+/, '');
}
async function api(path, opts = {}) {
  const res = await fetch(apiUrl(path), {
    method: opts.method || (opts.body ? 'POST' : 'GET'),
    headers: opts.body ? { 'Content-Type': 'application/json' } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: 'same-origin',
  });
  let data = {};
  try { data = await res.json(); } catch { data = { ok: false, error: '响应解析失败' }; }
  if (res.status === 401 && data.login) { showLogin(); throw new Error('需要登录'); }
  return data;
}
function $(sel) { return document.querySelector(sel); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; setTimeout(() => el.remove(), 320); }, 3200);
}
function fmtSec(v) {
  v = Math.max(0, Math.floor(v));
  const d = Math.floor(v / 86400), h = Math.floor(v % 86400 / 3600), m = Math.floor(v % 3600 / 60);
  if (d) return `${d} 天 ${h} 小时`;
  if (h) return `${h} 小时 ${m} 分`;
  if (m) return `${m} 分 ${v % 60} 秒`;
  return `${v} 秒`;
}
function statusBadge(ok, okText = '运行中', badText = '已停止') {
  return `<span class="badge ${ok ? 'ok' : 'bad'}">${ok ? okText : badText}</span>`;
}

/* ── 登录 ─────────────────────────────────────── */
function showLogin() {
  $('#login-mask').hidden = false;
  setTimeout(() => $('#login-pass')?.focus(), 60);
}
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const pass = $('#login-pass').value;
  const out = await api('/api/panel/login', { body: { password: pass } });
  if (out.ok) {
    $('#login-mask').hidden = true;
    $('#login-err').textContent = '';
    $('#login-pass').value = '';
    boot();
  } else {
    $('#login-err').textContent = out.error || '登录失败';
  }
});
$('#btn-logout').addEventListener('click', async () => {
  await api('/api/panel/logout', { body: {} });
  location.reload();
});
$('#btn-refresh').addEventListener('click', () => render());

/* ── 导航 ─────────────────────────────────────── */
function buildNav() {
  $('#nav').innerHTML = VIEWS.map(([id, title, ic]) =>
    `<a data-view="${id}" class="${id === state.view ? 'on' : ''}">${icon(ic)}<span>${title}</span></a>`).join('');
  $('#nav').querySelectorAll('a').forEach(a => {
    a.addEventListener('click', () => {
      state.view = a.dataset.view;
      $('#nav').querySelectorAll('a').forEach(x => x.classList.toggle('on', x === a));
      clearInterval(state.timer); state.timer = null;
      render();
    });
  });
}

/* 本体入口地址：不能用后端返回的 127.0.0.1（手机/别的电脑点了打不开），
   统一以浏览器当前访问的主机名为准，端口取后端报告的本体监听端口。 */
function hermesBase() {
  const h = (state.overview && state.overview.hermes) || {};
  const host = location.hostname || '127.0.0.1';
  const port = h.port || 2280;
  return `${location.protocol}//${host}:${port}`;
}

/* ── 总览 ─────────────────────────────────────── */
async function viewOverview() {
  const d = await api('/api/panel/overview');
  if (!d.ok) return `<div class="card"><h3>读取失败</h3><p class="muted">${esc(d.error || '')}</p></div>`;
  state.overview = d;
  const h = d.hermes, p = h.process || {}, sys = d.system || {};
  const alive = !!h.alive;
  state.hermesUrl = h.url;
  $('#alive-dot').className = 'dot ' + (alive ? 'ok' : 'bad');
  $('#panel-ver').textContent = 'v' + (d.panel?.version || '–');
  const gwOk = h.gateway && h.gateway.ok;
  return `
  <div class="hero">
    <div class="state">${statusBadge(alive, 'Hermes 运行中', 'Hermes 已停止')}</div>
    <div class="grow meta">
      <div>版本 <b>${esc(h.version || '–')}</b> · 端口 <b>${esc(h.port || 2280)}</b> · PID <b>${esc(h.pid || '–')}</b></div>
      <div>数据目录 <span style="font-size:11.5px">${esc(h.data_dir || '未发现')}</span></div>
    </div>
    <div class="btns">
      <a class="btn primary" href="${hermesBase()}/desktop-app/" target="_blank" rel="noopener">${icon('external')} 进入 Hermes</a>
    </div>
  </div>
  <div class="grid">
    <div class="card">
      <h3>运行状态 <span class="sub">进程</span></h3>
      <div class="row"><span>主进程</span><b>${alive ? '在线' : '离线'}</b></div>
      <div class="row"><span>内存占用</span><b>${p.rss_mb ? p.rss_mb + ' MB' : '–'}</b></div>
      <div class="row"><span>CPU 累计</span><b>${p.cpu_sec != null ? p.cpu_sec + ' s' : '–'}</b></div>
      <div class="row"><span>已运行</span><b>${p.uptime_sec ? fmtSec(p.uptime_sec) : '–'}</b></div>
    </div>
    <div class="card">
      <h3>消息网关 <span class="sub">gateway</span></h3>
      <div class="row"><span>状态</span><b>${gwOk ? '<span class="ok">正常</span>' : '<span class="muted">未运行</span>'}</b></div>
      <div class="row"><span>端口</span><b>${esc(h.gateway?.port || '–')}</b></div>
      <div class="row"><span>延迟</span><b>${h.gateway?.latency != null ? h.gateway.latency + ' ms' : '–'}</b></div>
      <div class="row"><span>会话数</span><b>${esc(h.sessions)}</b></div>
    </div>
    <div class="card">
      <h3>系统 <span class="sub">本机</span></h3>
      <div class="row"><span>CPU 负载</span><b>${sys.cpu_pct != null ? sys.cpu_pct + '%' : '–'}</b></div>
      <div class="row"><span>内存</span><b>${sys.mem_used_mb || 0} / ${sys.mem_total_mb || 0} MB</b></div>
      <div class="row"><span>运行时长</span><b>${sys.uptime ? fmtSec(parseFloat(sys.uptime)) : '–'}</b></div>
      <div class="row"><span>面板版本</span><b>v${esc(d.panel?.version || '–')}</b></div>
    </div>
    <div class="card">
      <h3>模型服务商 <span class="sub">providers</span></h3>
      <div class="row"><span>已配置模型</span><b>${esc(h.models)}</b></div>
      <div class="row"><span>本体入口</span><b style="font-size:11.5px">${esc(hermesBase())}/desktop-app/</b></div>
      <div class="row"><span>部署目录</span><b style="font-size:11px;word-break:break-all">${esc(h.appdest || '–')}</b></div>
    </div>
  </div>`;
}

/* ── 运行控制 ─────────────────────────────────── */
async function viewService() {
  const d = await api('/api/panel/overview');
  if (d.ok) state.overview = d;
  const h = d.ok ? d.hermes : { alive: false };
  const p = h.process || {};
  return `
  <div class="card">
    <h3>服务控制 <span class="sub">Managed by 应用中心生命周期脚本</span></h3>
    <div class="row"><span>当前状态</span><b>${statusBadge(!!h.alive)}</b></div>
    <div class="row"><span>PID</span><b>${esc(h.pid || '–')}</b></div>
    <div class="row"><span>已运行</span><b>${p.uptime_sec ? fmtSec(p.uptime_sec) : '–'}</b></div>
    <div class="spacer"></div>
    <div class="btns">
      <button class="btn primary" data-act="start" ${h.alive ? 'disabled' : ''}>启动</button>
      <button class="btn" data-act="restart">重启</button>
      <button class="btn danger" data-act="stop" ${h.alive ? '' : 'disabled'}>停止</button>
      <a class="btn ghost" href="${hermesBase()}/desktop-app/" target="_blank" rel="noopener">进入 Hermes</a>
    </div>
    <p class="muted" style="margin:12px 0 0">停止会让消息网关与会话中断；日常更新请优先用应用中心，避免面板直接停止服务。</p>
  </div>
  <div class="card">
    <h3>网关与 Dashboard</h3>
    <div class="row"><span>Gateway</span><b>${h.gateway?.ok ? '<span class="ok">正常</span>' : '未运行'}</b></div>
    <div class="row"><span>Dashboard 端口</span><b>${esc(h.gateway?.port || '–')}</b></div>
    <div class="btns" style="margin-top:10px">
      <button class="btn sm" data-act="gateway-restart">重启网关</button>
    </div>
  </div>`;
}

/* ── 版本管理 ─────────────────────────────────── */
async function viewVersions() {
  const d = await api('/api/panel/versions');
  if (!d.ok) return `<div class="card"><h3>读取失败</h3><p class="muted">${esc(d.error || '')}</p></div>`;
  const off = d.hermes?.official || {};
  const offTxt = off.ok === false ? `<span class="muted">检查失败：${esc(off.error || '')}</span>`
    : (off.latest ? `最新 <b>${esc(off.latest)}</b>` : '<span class="muted">未获取到官方版本</span>');
  return `
  <div class="grid">
    <div class="card">
      <h3>Hermes Agent 本体</h3>
      <div class="row"><span>已安装</span><b>${esc(d.hermes?.installed || '–')}</b></div>
      <div class="row"><span>官方</span><b>${offTxt}</b></div>
      <div class="spacer"></div>
      <p class="muted" style="margin:0">${esc(d.hermes?.note || '')}</p>
      <div class="btns" style="margin-top:10px">
        <button class="btn sm" data-act="check-hermes">检查本体版本</button>
      </div>
    </div>
    <div class="card">
      <h3>Hermes Panel（本面板）</h3>
      <div class="row"><span>当前版本</span><b>v${esc(d.panel?.version || '–')}</b></div>
      <div class="row"><span>版本线</span><b>${esc(d.panel?.line || '')}</b></div>
      <div class="row"><span>发布仓库</span><b style="font-size:11.5px">${esc(d.panel?.repo || '')}</b></div>
      <div class="btns" style="margin-top:10px">
        <button class="btn sm primary" data-goto="update">前往面板更新</button>
      </div>
    </div>
  </div>`;
}

/* ── 日志 ─────────────────────────────────────── */
async function viewLogs() {
  const names = [['app', '应用日志'], ['gateway', '网关'], ['dashboard', 'Dashboard'], ['panel', '面板']];
  const d = await api(`/api/panel/logs?name=${state.logName}&kb=64`);
  const text = d.ok ? (d.text || '(空)') : `读取失败：${d.error || ''}`;
  return `
  <div class="card">
    <h3 style="justify-content:space-between">
      <span>日志 <span class="sub">${esc(d.path || '')}</span></span>
      <span class="btns">
        <select id="log-name" style="width:auto">
          ${names.map(([v, t]) => `<option value="${v}" ${v === state.logName ? 'selected' : ''}>${t}</option>`).join('')}
        </select>
        <button class="btn sm" data-act="log-refresh">刷新</button>
        <label class="checkline" style="font-size:12px"><input type="checkbox" id="log-auto" ${state.autoScroll ? 'checked' : ''}>自动刷新</label>
      </span>
    </h3>
    <pre class="log" id="log-box">${esc(text)}</pre>
  </div>`;
}

/* ── 配置 ─────────────────────────────────────── */
async function viewConfig() {
  const d = await api('/api/panel/config');
  if (!d.ok) return `<div class="card"><h3>读取失败</h3><p class="muted">${esc(d.error || '')}</p></div>`;
  const cfg = d.config || {};
  const providers = cfg.providers || [];
  const rows = providers.length
    ? providers.map(p => `<tr><td>${esc(p.name || p.id || '')}</td><td>${esc(p.base_url || '')}</td><td>${esc(p.model || '')}</td><td>${p.active || p.is_active ? '<span class="badge ok">当前</span>' : ''}</td></tr>`).join('')
    : '<tr><td colspan="4" class="empty">尚未配置模型服务商，请到 Hermes 桌面端「设置 → 模型」添加</td></tr>';
  return `
  <div class="card">
    <h3>模型服务商 <span class="sub">只读，修改请在 Hermes 桌面端进行</span></h3>
    <table class="list">
      <thead><tr><th>名称</th><th>接口地址</th><th>默认模型</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>
  <div class="card">
    <h3>config.yaml <span class="sub">只读预览</span></h3>
    <pre class="log" style="max-height:38vh">${esc(d.yaml || '(未找到 config.yaml)')}</pre>
  </div>`;
}

/* ── 快照 ─────────────────────────────────────── */
async function viewSnapshots() {
  const d = await api('/api/panel/snapshots');
  const items = d.items || [];
  const rows = items.length ? items.map(it => `
    <tr>
      <td>${esc(it.name)}</td>
      <td class="num">${it.size_mb} MB</td>
      <td class="num">${esc(it.created)}</td>
      <td style="text-align:right">
        <button class="btn sm" data-snap-restore="${esc(it.name)}">恢复</button>
        <button class="btn sm danger" data-snap-delete="${esc(it.name)}">删除</button>
      </td>
    </tr>`).join('') : '<tr><td colspan="4" class="empty">还没有快照</td></tr>';
  return `
  <div class="card">
    <h3 style="justify-content:space-between">
      <span>快照 <span class="sub">config / providers / sessions / skills / memories / cron</span></span>
      <button class="btn sm primary" data-act="snap-create">立即创建快照</button>
    </h3>
    <table class="list">
      <thead><tr><th>文件</th><th>大小</th><th>创建时间</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="muted" style="margin:10px 0 0">恢复会先自动创建一份「恢复前备份」，随后覆盖当前配置与会话；恢复后建议重启本体。</p>
  </div>`;
}

/* ── 授权与访问 ───────────────────────────────── */
async function viewAccess() {
  const d = await api('/api/panel/access');
  const st = d.settings || {};
  let fn = { installed: false, authorized: false, error: '' };
  try { const r = await api('/api/panel/fnos'); if (r && r.fnos) fn = r.fnos; } catch (e) { /* 老版后端无此接口 */ }
  const fnCard = `
    <div class="card">
      <h3>飞牛 OS 集成</h3>
      <div class="row"><span>授权状态</span><b>${fn.authorized ? '<span class="badge ok">已授权</span>' : '<span class="badge warn">未授权</span>'}</b></div>
      <div class="row"><span>设备</span><b>${esc(fn.device || '—')}</b></div>
      <div class="row"><span>最近授权</span><b>${esc(fn.at || '—')}</b></div>
      <div class="row"><span>客户端</span><b>${fn.installed ? '包内自带' : '缺失'}</b></div>
      ${fn.error ? `<div class="row"><span>错误</span><b class="warn">${esc(fn.error)}</b></div>` : ''}
      <div class="spacer"></div>
      <div class="btns" style="margin-top:10px">
        <button class="btn primary sm" data-act="fnos-auth">${fn.authorized ? '重新授权' : '点击授权'}</button>
        ${fn.authorized ? '<button class="btn sm" data-act="fnos-logout">取消授权</button>' : ''}
      </div>
      <div id="fnos-auth-box" hidden style="margin-top:12px;border-top:1px solid var(--line);padding-top:12px">
        <p class="muted" style="margin:0 0 8px">1. 打开下面的链接登录飞牛 OS 并确认授权<br>2. 复制页面给出的一次性授权码，粘到下面提交</p>
        <div class="row"><span>授权链接</span><b><a id="fnos-url" href="#" target="_blank" rel="noopener">点此打开</a></b></div>
        <label class="field" style="margin-top:8px">一次性授权码
          <input type="text" id="fnos-code" placeholder="粘贴授权码后点提交">
        </label>
        <div class="btns" style="margin-top:10px">
          <button class="btn primary sm" data-act="fnos-complete">提交授权码</button>
          <button class="btn sm" data-act="fnos-cancel">取消</button>
        </div>
      </div>
      <p class="muted" style="margin:10px 0 0 0">授权后，面板与 Hermes 可以使用飞牛 OS 的文件、应用中心、监控等能力；凭据只存在本机面板数据目录。</p>
    </div>`;
  return `
  <div class="grid">
    ${fnCard}
    <div class="card">
      <h3>访问密码</h3>
      <div class="row"><span>当前状态</span><b>${d.enabled ? '<span class="badge ok">已启用</span>' : '<span class="badge warn">未设置</span>'}</b></div>
      <div class="row"><span>最近修改</span><b>${esc(d.updated || '–')}</b></div>
      <div class="spacer"></div>
      <label class="field">设置新密码（留空并保存 = 关闭密码）
        <input type="password" id="new-pass" placeholder="至少 4 位">
      </label>
      <div class="btns" style="margin-top:10px">
        <button class="btn primary sm" data-act="set-pass">保存</button>
        <button class="btn sm" data-act="logout">退出登录</button>
      </div>
      <p class="muted" style="margin:10px 0 0">密码只保护<b>面板自己的门</b>；Hermes 本体登录不归这里管。局域网可信网段默认放行。</p>
    </div>
    <div class="card">
      <h3>当前访问</h3>
      <div class="row"><span>来源 IP</span><b>${esc(d.ip || '')}</b></div>
      <div class="row"><span>是否可信网段</span><b>${d.trusted ? '<span class="ok">是</span>' : '否'}</b></div>
      <div class="row"><span>有效会话</span><b>${esc(d.sessions || 0)}</b></div>
      <div class="row"><span>面板端口</span><b>${esc(d.panel_port || location.port || '—')}</b></div>
    </div>
  </div>`;
}

/* ── 设置 ─────────────────────────────────────── */
async function viewSettings() {
  const d = await api('/api/panel/settings');
  const s = d.settings || {};
  return `
  <div class="card">
    <h3>面板设置</h3>
    <div class="grid" style="gap:12px">
      <label class="checkline"><input type="checkbox" id="set-allow-lan" ${s.allow_lan ? 'checked' : ''}>允许局域网访问面板</label>
      <label class="checkline"><input type="checkbox" id="set-trusted" ${s.require_trusted ? 'checked' : ''}>设了密码后仍强制登录（关闭可信网段自动放行）</label>
      <label class="field">自动刷新间隔（秒）
        <input type="text" id="set-refresh" value="${esc(s.refresh_sec || 8)}">
      </label>
      <label class="field">日志读取大小（KB）
        <input type="text" id="set-logkb" value="${esc(s.log_tail_kb || 48)}">
      </label>
    </div>
    <div class="btns" style="margin-top:12px">
      <button class="btn primary" data-act="save-settings">保存设置</button>
    </div>
  </div>
  <div class="card">
    <h3>关于</h3>
    <div class="row"><span>面板</span><b>Hermes Panel v${esc(state.me.panel_version || '–')}</b></div>
    <div class="row"><span>说明</span><b style="font-weight:400;font-size:12.5px">本体更新走应用中心；面板可独立升级（1.x 版本线）</b></div>
  </div>`;
}

/* ── 面板更新 ─────────────────────────────────── */
async function viewUpdate() {
  const prog = await api('/api/panel/update');
  return `
  <div class="grid">
    <div class="card">
      <h3>检查更新</h3>
      <div class="row"><span>当前版本</span><b>v${esc(state.me.panel_version || '–')}</b></div>
      <div class="row"><span>最新版本</span><b id="up-latest">–</b></div>
      <div class="row"><span>状态</span><b id="up-state">${esc(prog.version ? '已更新到 v' + prog.version : '未检查')}</b></div>
      <div class="btns" style="margin-top:10px">
        <button class="btn sm primary" data-act="up-check">检查更新</button>
        <button class="btn sm" data-act="up-apply">安装更新</button>
        <button class="btn sm" data-act="up-rollback">回滚</button>
      </div>
      <p class="muted" style="margin:12px 0 0">下载路线：加速源 → 备用加速 → 官方直连；每次都会做 sha256 校验、结构探活与原子切换，失败自动保留 <code>panel_dist.bak-&lt;版本&gt;</code> 以便回滚。</p>
    </div>
    <div class="card">
      <h3>更新说明</h3>
      <pre class="log" id="up-notes" style="max-height:34vh">检查更新后显示发布说明</pre>
    </div>
  </div>`;
}

/* ── 路由渲染 ─────────────────────────────────── */
const RENDERERS = {
  overview: viewOverview,
  service: viewService,
  versions: viewVersions,
  logs: viewLogs,
  config: viewConfig,
  snapshots: viewSnapshots,
  access: viewAccess,
  settings: viewSettings,
  update: viewUpdate,
};

async function render() {
  const [id, title] = VIEWS.find(v => v[0] === state.view) || VIEWS[0];
  $('#page-title').textContent = title;
  const box = $('#view');
  try {
    box.innerHTML = await RENDERERS[state.view]();
  } catch (e) {
    box.innerHTML = `<div class="card"><h3>出错了</h3><p class="muted">${esc(e.message)}</p></div>`;
    return;
  }
  bindActions();
  autoRefresh();
}

function autoRefresh() {
  clearInterval(state.timer);
  state.timer = null;
  const sec = Number(state.me.refresh_sec || 0) || 0;
  if ((state.view === 'overview' || state.view === 'service' || state.view === 'logs') && sec > 0) {
    state.timer = setInterval(async () => {
      if (document.hidden) return;
      if (state.view === 'logs') {
        const auto = $('#log-auto');
        if (!auto || !auto.checked) return;
        const d = await api(`/api/panel/logs?name=${state.logName}&kb=64`);
        if (d.ok) {
          const box = $('#log-box');
          if (box) { box.textContent = d.text || '(空)'; box.scrollTop = box.scrollHeight; }
        }
        return;
      }
      render();
    }, Math.max(3, sec) * 1000);
  }
}

/* ── 交互绑定 ─────────────────────────────────── */
function bindActions() {
  document.querySelectorAll('[data-act]').forEach(el => {
    el.addEventListener('click', () => handleAct(el.dataset.act, el));
  });
  document.querySelectorAll('[data-goto]').forEach(el => {
    el.addEventListener('click', () => { state.view = el.dataset.goto; buildNav(); render(); });
  });
  const logSel = $('#log-name');
  if (logSel) logSel.addEventListener('change', () => { state.logName = logSel.value; render(); });
  const auto = $('#log-auto');
  if (auto) auto.addEventListener('change', () => { state.autoScroll = auto.checked; autoRefresh(); });

  document.querySelectorAll('[data-snap-restore]').forEach(el => {
    el.addEventListener('click', async () => {
      if (!confirm('恢复快照会覆盖当前配置与会话，继续？')) return;
      const r = await api('/api/panel/snapshots', { body: { action: 'restore', name: el.dataset.snapRestore } });
      toast(r.ok ? `已恢复 ${r.restored}（自动备份 ${r.auto_backup}）` : `恢复失败：${r.error}`, r.ok ? 'ok' : 'bad');
      render();
    });
  });
  document.querySelectorAll('[data-snap-delete]').forEach(el => {
    el.addEventListener('click', async () => {
      if (!confirm(`删除快照 ${el.dataset.snapDelete}？`)) return;
      const r = await api('/api/panel/snapshots', { body: { action: 'delete', name: el.dataset.snapDelete } });
      toast(r.ok ? '已删除' : `删除失败：${r.error}`, r.ok ? 'ok' : 'bad');
      render();
    });
  });
}

async function handleAct(act, el) {
  const disable = (v) => { if (el) el.disabled = v; };
  switch (act) {
    case 'start': case 'stop': case 'restart': {
      if (act !== 'start' && !confirm(act === 'stop' ? '停止 Hermes 会中断消息网关，继续？' : '重启 Hermes？')) return;
      disable(true);
      const r = await api('/api/panel/service', { body: { action: act } });
      toast(r.ok ? `已执行 ${act}` : `失败：${r.error || r.detail || ''}`, r.ok ? 'ok' : 'bad');
      setTimeout(render, 1200);
      break;
    }
    case 'gateway-restart': {
      const r = await api('/api/panel/service', { body: { action: 'restart' } });
      toast(r.ok ? '已重启' : '重启失败', r.ok ? 'ok' : 'bad');
      break;
    }
    case 'check-hermes': {
      disable(true);
      const d = await api('/api/panel/versions');
      const off = d.hermes?.official || {};
      toast(off.ok === false ? `检查失败：${off.error}` : `本体已安装 ${d.hermes?.installed}，官方 ${off.latest || '未知'}`, 'ok');
      disable(false);
      break;
    }
    case 'log-refresh': render(); break;
    case 'snap-create': {
      disable(true);
      const r = await api('/api/panel/snapshots', { body: { action: 'create' } });
      toast(r.ok ? `快照已创建：${r.name}（${r.size_mb} MB）` : `创建失败：${r.error}`, r.ok ? 'ok' : 'bad');
      render();
      break;
    }
    case 'fnos-auth': {
      disable(true);
      const r = await api('/api/panel/fnos', { body: { action: 'start', host: location.hostname } });
      disable(false);
      if (!r.ok) { toast(`授权启动失败：${r.error}`, 'bad'); break; }
      const box = $('#fnos-auth-box');
      const link = $('#fnos-url');
      if (box && link) {
        box.hidden = false;
        link.href = r.url;
        link.textContent = r.url.length > 64 ? `${r.url.slice(0, 64)}…` : r.url;
        link.title = r.url;
      }
      toast('已生成授权链接，点击后登录飞牛 OS 并确认授权', 'ok');
      break;
    }
    case 'fnos-complete': {
      const code = ($('#fnos-code')?.value || '').trim();
      if (!code) { toast('请先粘贴一次性授权码', 'bad'); break; }
      disable(true);
      const r = await api('/api/panel/fnos', { body: { action: 'complete', code } });
      disable(false);
      toast(r.ok ? '飞牛 OS 授权成功' : `授权失败：${r.error}`, r.ok ? 'ok' : 'bad');
      if (r.ok) render();
      break;
    }
    case 'fnos-cancel': {
      await api('/api/panel/fnos', { body: { action: 'cancel' } });
      const box = $('#fnos-auth-box');
      if (box) box.hidden = true;
      toast('已取消', 'ok');
      break;
    }
    case 'fnos-logout': {
      disable(true);
      const r = await api('/api/panel/fnos', { body: { action: 'logout' } });
      disable(false);
      toast(r.ok ? '已取消飞牛 OS 授权' : `取消失败：${r.error}`, r.ok ? 'ok' : 'bad');
      render();
      break;
    }
    case 'set-pass': {
      const pass = $('#new-pass').value.trim();
      const r = await api('/api/panel/access', { body: { password: pass } });
      toast(r.ok ? (r.enabled ? '密码已设置' : '密码保护已关闭') : `保存失败：${r.error}`, r.ok ? 'ok' : 'bad');
      if (r.ok) render();
      break;
    }
    case 'logout': {
      await api('/api/panel/logout', { body: {} });
      location.reload();
      break;
    }
    case 'save-settings': {
      const body = {
        allow_lan: $('#set-allow-lan').checked,
        require_trusted: $('#set-trusted').checked,
        refresh_sec: Number($('#set-refresh').value) || 8,
        log_tail_kb: Number($('#set-logkb').value) || 48,
      };
      const r = await api('/api/panel/settings', { body });
      toast(r.ok ? '设置已保存' : '保存失败', r.ok ? 'ok' : 'bad');
      if (r.ok) { state.me.refresh_sec = body.refresh_sec; render(); }
      break;
    }
    case 'up-check': {
      disable(true);
      const r = await api('/api/panel/update', { body: { action: 'check' } });
      disable(false);
      if (!r.ok) { toast(`检查失败：${r.error}`, 'bad'); return; }
      $('#up-latest').textContent = r.latest || r.current || '–';
      $('#up-state').textContent = r.has_update ? '有新版本可安装' : '已是最新';
      if (r.release) $('#up-notes').textContent = `${r.release.name || ''}\n${r.release.body || ''}`;
      else $('#up-notes').textContent = '当前没有可用的面板资源包发布。';
      toast(r.has_update ? `发现新版本 ${r.latest}` : '已是最新版本', r.has_update ? 'ok' : '');
      break;
    }
    case 'up-apply': {
      if (!confirm('安装面板更新？完成后需要刷新页面。')) return;
      disable(true);
      const r = await api('/api/panel/update', { body: { action: 'apply' } });
      toast(r.ok ? `开始下载 v${r.version}…` : `无法更新：${r.error}`, r.ok ? 'ok' : 'bad');
      if (r.ok) {
        const poll = setInterval(async () => {
          const p = await api('/api/panel/update');
          $('#up-state').textContent = p.state === 'done' ? `已更新到 v${p.version}，请刷新页面` : `状态：${p.state || '等待中'}${p.error ? '（' + p.error + '）' : ''}`;
          if (p.state === 'done' || p.state === 'error') { clearInterval(poll); disable(false); }
        }, 1500);
      } else disable(false);
      break;
    }
    case 'up-rollback': {
      if (!confirm('回滚到上一份面板资源？')) return;
      const r = await api('/api/panel/update', { body: { action: 'rollback' } });
      toast(r.ok ? '已回滚，刷新页面生效' : `回滚失败：${r.error}`, r.ok ? 'ok' : 'bad');
      break;
    }
    default: break;
  }
}

/* ── 启动 ─────────────────────────────────────── */
async function boot() {
  const me = await api('/api/panel/auth/state');
  if (!me.ok) return;
  state.me = me;
  $('#ip-pill').textContent = me.ip || '';
  $('#btn-logout').hidden = !me.enabled;
  if (!me.logged_in) { showLogin(); return; }
  $('#login-mask').hidden = true;
  $('#panel-ver').textContent = 'v' + (me.panel_version || '–');
  buildNav();
  await render();
}
boot();
