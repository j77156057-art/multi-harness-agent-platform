/**
 * 内置终端面板。
 *
 * 为什么要有它：装 CLI 与登录这两步原来只能人自己开终端。而登录几乎都是**交互式**的
 * （清单里 `login_note` 就写着「运行 codex 并选择 Sign in with ChatGPT」「运行 claude
 * 按浏览器提示登录」），所以一个"点一下就替你跑命令"的按钮面板根本不够 —— 得给一个
 * 真 PTY，人能在里面跟着 CLI 自己的提示走。
 *
 * 边界（见 docs/notes/terminal-design.md）：
 *   · 只有人能开（顶栏按钮），harness / 管理员窗口 / 助手都拿不到这条路；
 *   · 不代跑任何命令：三个按钮只是**把命令打到终端里**，按回车由人自己按；
 *   · 会话会留痕（输入输出脱敏后落 data/terminals/），面板上明写这件事。
 */
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import './terminal-panel.css';

const AVAILABILITY = '/api/formal/terminal/availability';
const TICKET = '/api/formal/terminal/ticket';
const INSTALLERS = '/api/formal/installers?probe_local=false';

/** 面板里那一行按钮用的 CLI 清单：只留能驱动的、并且这个系统上真有安装命令的。 */
function actionable(rows) {
  return (rows || [])
    .filter((row) => row && row.driven !== false && row.supported !== false)
    .filter((row) => typeof row.install_command === 'string' && row.install_command.trim())
    .map((row) => ({
      id: String(row.harness_id || ''),
      name: String(row.display_name || row.harness_id || ''),
      install: String(row.install_command).trim(),
      verify: String(row.verify_command || `${row.command} --version`).trim(),
      run: String(row.command || row.harness_id || '').trim(),
      loginNote: String(row.login_note || '').trim(),
      status: String(row.status || 'unknown'),
    }))
    .filter((row) => row.id);
}

async function getJson(path) {
  const response = await fetch(path, { cache: 'no-store' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
  return data;
}

async function postJson(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Office-Formal': '1' },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
  return data;
}

/**
 * 挂一个终端面板。
 *
 * @param {{ onClose?: () => void, sent?: string[] }} options
 *   `sent` 用来在同一次页面停留里记住"已经打到终端里的命令"，方便人看到自己点过什么。
 */
export function mountTerminalPanel({ onClose, sent = [] } = {}) {
  const overlay = document.createElement('div');
  overlay.className = 'tm-overlay';
  overlay.innerHTML = `
    <div class="tm-panel" role="dialog" aria-label="本机终端">
      <header class="tm-head">
        <div>
          <h2>终端（这台机器）</h2>
          <p class="tm-note">装 CLI 与登录都在这里做。这个会话的输入输出会脱敏后留痕，方便事后查。</p>
        </div>
        <button type="button" class="tm-close" data-tm="close" aria-label="关闭终端">关闭</button>
      </header>
      <div class="tm-tools">
        <label class="tm-pick">CLI
          <select data-tm="cli"></select>
        </label>
        <button type="button" data-tm="install">安装</button>
        <button type="button" data-tm="verify">重探版本</button>
        <button type="button" data-tm="login">登录</button>
        <span class="tm-hint" data-tm="hint"></span>
      </div>
      <div class="tm-body" data-tm="body"></div>
      <p class="tm-status" data-tm="status">正在准备终端…</p>
    </div>`;
  document.body.append(overlay);

  const body = overlay.querySelector('[data-tm="body"]');
  const statusLine = overlay.querySelector('[data-tm="status"]');
  const hint = overlay.querySelector('[data-tm="hint"]');
  const picker = overlay.querySelector('[data-tm="cli"]');
  const buttons = ['install', 'verify', 'login'].map((name) => overlay.querySelector(`[data-tm="${name}"]`));

  let term = null;
  let fit = null;
  let socket = null;
  let closed = false;
  let clis = [];

  const setStatus = (text) => { statusLine.textContent = text; };
  const setToolsEnabled = (enabled) => {
    picker.disabled = !enabled;
    for (const button of buttons) button.disabled = !enabled;
  };

  /** 把一条命令打进终端：**不自动回车**，由人看过再按。 */
  const typeCommand = (command) => {
    if (!term || !socket || socket.readyState !== WebSocket.OPEN) return;
    const line = command.trim();
    if (!line) return;
    socket.send(JSON.stringify({ type: 'input', data: line }));
    sent.push(line);
    hint.textContent = `已打进终端：${line}（看过没问题再按回车）`;
  };

  const closePanel = (reason) => {
    if (closed) return;
    closed = true;
    try { socket?.send(JSON.stringify({ type: 'close' })); } catch {}
    try { socket?.close(); } catch {}
    try { fit?.dispose(); } catch {}
    try { term?.dispose(); } catch {}
    overlay.remove();
    onClose?.(reason);
  };

  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) { closePanel('backdrop'); return; }
    const action = event.target.closest('[data-tm]')?.dataset.tm;
    if (!action) return;
    if (action === 'close') { closePanel('button'); return; }
    const picked = clis.find((row) => row.id === picker.value);
    if (!picked) return;
    if (action === 'install') typeCommand(picked.install);
    if (action === 'verify') typeCommand(picked.verify);
    if (action === 'login') {
      // 清单里只有 login_note（一句人话），没有登录命令原文 —— 所以**不猜子命令**，
      // 只运行该 CLI 本身，跟着它自己的提示走。
      typeCommand(picked.run);
      if (picked.loginNote) hint.textContent = picked.loginNote;
    }
  });

  document.addEventListener('keydown', function onKey(event) {
    if (closed) { document.removeEventListener('keydown', onKey); return; }
    if (event.key === 'Escape') closePanel('escape');
  });

  (async () => {
    let availability = null;
    try {
      availability = await getJson(AVAILABILITY);
    } catch (problem) {
      setStatus(`终端不可用：${problem.message}`);
      setToolsEnabled(false);
      return;
    }
    if (!availability || availability.available !== true) {
      setStatus(`这台机器上终端不可用：${availability?.reason || 'node-pty 没装上'}`);
      setToolsEnabled(false);
      return;
    }

    term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Consolas, "Cascadia Mono", Menlo, monospace',
      theme: { background: '#0f1115', foreground: '#e6e8ee', cursor: '#e6e8ee' },
      scrollback: 4000,
    });
    fit = new FitAddon();
    term.loadAddon(fit);
    term.open(body);
    try { fit.fit(); } catch {}

    try {
      const ticket = await postJson(TICKET, {});
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${scheme}://${location.host}/api/formal/terminal?ticket=${encodeURIComponent(ticket.ticket)}`
        + `&cols=${term.cols}&rows=${term.rows}`;
      socket = new WebSocket(url);
    } catch (problem) {
      setStatus(`拿不到终端票据：${problem.message}`);
      return;
    }

    socket.addEventListener('message', (event) => {
      let message = null;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === 'ready') {
        setStatus(`${message.shell} · 会话 ${message.session_id} · 记录 ${message.log}`);
        setToolsEnabled(true);
        return;
      }
      if (message.type === 'data') { term?.write(String(message.data || '')); return; }
      if (message.type === 'exit') {
        setToolsEnabled(false);
        const why = message.reason === 'idle'
          ? '会话空闲太久，已经被回收（重新打开即是新会话）'
          : message.reason === 'cap'
            ? '已经有两个终端开着，先关掉一个'
            : message.reason === 'unavailable'
              ? `终端不可用：${message.message || ''}`
              : `会话结束${message.code !== undefined ? `（退出码 ${message.code}）` : ''}`;
        setStatus(why);
      }
    });
    socket.addEventListener('close', () => {
      if (!closed) setToolsEnabled(false);
    });
    socket.addEventListener('error', () => setStatus('终端连接断了；关掉重开即可'));

    term.onData((data) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'input', data }));
    });
    term.onResize(({ cols, rows }) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'resize', cols, rows }));
    });

    const onWindowResize = () => { try { fit.fit(); } catch {} };
    window.addEventListener('resize', onWindowResize);
    overlay.addEventListener('tm-closed', () => window.removeEventListener('resize', onWindowResize));

    try {
      const payload = await getJson(INSTALLERS);
      clis = actionable(payload.installers);
      picker.innerHTML = clis
        .map((row) => `<option value="${row.id}">${row.name}${row.status === 'ready' ? '（已安装）' : ''}</option>`)
        .join('');
      setToolsEnabled(true);
      if (sent.length) hint.textContent = `本次已经打进终端 ${sent.length} 条命令`;
    } catch {
      setToolsEnabled(false);
      hint.textContent = '读不到 CLI 清单，只能手动敲命令';
    }
  })();

  return { close: () => closePanel('caller'), overlay };
}
