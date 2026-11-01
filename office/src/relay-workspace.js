/**
 * 转接视图：一个项目选了「转接」之后，办公室就换成这一页。
 *
 * 为什么单独一页而不是在办公室上加一块：转接这条路**没有任务**——不建任务、不开工作树、
 * 不判内容，平台只按「给<收件人>：」把信投到各窗口的收件箱。任务表、方案、审核、合入
 * 在这里统统不适用，硬塞进派单那套界面只会让人以为「怎么什么都没有」。所以分开：
 * 这一页只讲三件事——在不在跑、有几条线、哪封没投出去。
 *
 * 边界（见 docs/notes/relay-mode.md）：
 *   · 只读：这一页不改信箱、不发信；写信是成员自己的动作（或者人自己在终端里写）。
 *   · 不摆进度百分比，也不猜进度：转接没有进度可言，只有"投了/退回了"。
 *   · 长清单默认收起，标题上写明几条（展开才看得见的清单等于没写）。
 */
import './relay-workspace.css';

const OVERVIEW = '/api/formal/relay/overview';
/** 与后端 `MAX_HOPS` 同一个口径（后端是权威，这里只用来标"快到上限了"）。 */
const MAX_HOPS = 6;

function escape(text) {
  return String(text == null ? '' : text).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char]));
}

function when(iso) {
  if (!iso) return '时间未记';
  const stamp = new Date(iso);
  if (Number.isNaN(stamp.getTime())) return '时间未记';
  return stamp.toLocaleString('zh-CN', { hour12: false });
}

async function fetchOverview() {
  const response = await fetch(OVERVIEW, { cache: 'no-store' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
  return data;
}

function stateLine(overview) {
  if (!overview || !overview.heartbeat_at) {
    return {
      tone: 'stopped',
      text: '平台侧还没有在跑转接：信放进信箱不会有人投。转接由管理员窗口负责拉起 —— 长期如此说明管理员窗口也没在跑。',
    };
  }
  if (!overview.running) {
    return {
      tone: 'stale',
      text: `平台侧已经停了（最后一次工作 ${when(overview.heartbeat_at)}）。这段时间放进去的信不会被投递；管理员窗口下一次巡检会把它拉回来。`,
    };
  }
  return { tone: 'running', text: `平台侧正在转接（最后一次工作 ${when(overview.heartbeat_at)}），由管理员窗口保持运行。` };
}

function threadStatus(thread) {
  if (thread.unreadable) return { tone: 'broken', label: '账本读不出来' };
  if (thread.hops >= MAX_HOPS) return { tone: 'at-ceiling', label: `已到上限（${thread.hops} 跳）` };
  if (thread.hops === MAX_HOPS - 1) return { tone: 'near-ceiling', label: `${thread.hops} 跳 · 还剩 1 跳` };
  return { tone: 'ok', label: `${thread.hops} 跳` };
}

export function mountRelayWorkspace({ root, project }) {
  const wrap = document.createElement('div');
  wrap.className = 'relay-workspace';
  root.append(wrap);

  const state = {
    overview: null,
    error: '',
    openThreads: false,
    openReturned: false,
  };

  function projectName() {
    const current = typeof project === 'function' ? project() : project;
    return (current && current.name) || '转接项目';
  }

  function render() {
    const overview = state.overview;
    const status = stateLine(overview);
    const threads = (overview && overview.threads) || [];
    const returned = (overview && overview.returned) || [];
    const pending = (overview && overview.pending) || 0;

    wrap.innerHTML = `
      <header class="relay-head">
        <p class="relay-eyebrow">转接</p>
        <h1>${escape(projectName())}</h1>
        <p class="relay-lead">本项目走转接：平台只当邮局。成员把要说的话写成 md 信放进信箱，平台按「给&lt;收件人&gt;：」分块投递，不建任务、不开工作树、不判内容。</p>
        <p class="relay-state relay-state-${status.tone}">${escape(status.text)}</p>
        <div class="relay-actions">
          <button type="button" data-relay-refresh>刷新</button>
        </div>
      </header>

      ${state.error ? `<p class="relay-error">读不到信箱：${escape(state.error)}</p>` : ''}

      <section class="relay-card">
        <h2>写一封信</h2>
        <ol class="relay-steps">
          <li>在 <code>relay/outbox/</code> 放一个 <code>.md</code> 文件（文件名随意，别带路径）。</li>
          <li>第一行写 <code>来自 &lt;你的窗口&gt;</code>；回信再加一行 <code>回复 thr-xxxxxxxx</code>，平台据此接上同一条线程、跳数 +1。</li>
          <li>用 <code>给&lt;收件人&gt;：</code> 分段，一段投给一个窗口；写给管理员用 <code>给管理员：</code>。</li>
          <li>投出去的信挪进 <code>archive/</code>；投不出去的挪进 <code>returned/</code> 并附一份原因，照原因改好重新放进 outbox 即可。</li>
        </ol>
        <p class="relay-note">一条线程最多转 ${MAX_HOPS} 跳；同一对窗口 10 分钟内来回超过 6 次也会被停下。越限一律整封退回并写明原因，不会静默丢弃。</p>
      </section>

      <section class="relay-card">
        <h2>待投 <span class="relay-count">${pending} 封</span></h2>
        ${pending
          ? `<p class="relay-note">${overview && overview.running ? '这些信还在 outbox 里，下一轮就会被投出去。' : '这些信正在等平台侧把转接跑起来，否则不会有人投。'}</p>`
          : '<p class="relay-empty">信箱里没有等待投递的信。</p>'}
      </section>

      <section class="relay-card">
        <details data-relay-fold="threads"${state.openThreads ? ' open' : ''}>
          <summary><h2>线程 <span class="relay-count">${threads.length} 条</span></h2></summary>
          ${threads.length
            ? `<ul class="relay-list">${threads.map((thread) => {
                const stamp = threadStatus(thread);
                const who = thread.unreadable
                  ? '参与者不明'
                  : (thread.participants || []).join(' ↔ ') || '还没有参与者记录';
                return `<li class="relay-thread relay-thread-${stamp.tone}">
                  <b>${escape(who)}</b>
                  <span class="relay-badge">${escape(stamp.label)}</span>
                  <span class="relay-when">${escape(when(thread.updated_at))}</span>
                  <code class="relay-id">${escape(thread.thread_id)}</code>
                  ${thread.unreadable ? `<span class="relay-hint">${escape(thread.unreadable)}：这份账本可能被手改坏了，回信会先被退回并提示找平台方看。</span>` : ''}
                </li>`;
              }).join('')}</ul>`
            : '<p class="relay-empty">还没有转过任何一封信。</p>'}
        </details>
      </section>

      <section class="relay-card">
        <details data-relay-fold="returned"${state.openReturned ? ' open' : ''}>
          <summary><h2>退回 <span class="relay-count">${returned.length} 封</span></h2></summary>
          ${returned.length
            ? `<ul class="relay-list">${returned.map((item) => `<li class="relay-returned">
                <b>${escape(item.letter)}</b>
                <span class="relay-reason">${escape(item.reason)}</span>
              </li>`).join('')}</ul>`
            : '<p class="relay-empty">没有退回的信。</p>'}
        </details>
      </section>

      <p class="relay-foot">这一页只读：它不改信箱、不发信。写信是成员自己的动作。</p>`;
  }

  async function refresh() {
    try {
      state.overview = await fetchOverview();
      state.error = '';
    } catch (error) {
      state.error = String((error && error.message) || error);
    }
    // 折叠状态是本地偏好，重画时保住（不然每 15 秒收回去一次，人正看着的那块会跳走）。
    const openFolds = new Set(
      [...wrap.querySelectorAll('[data-relay-fold]')].filter((node) => node.open).map((node) => node.dataset.relayFold),
    );
    if (openFolds.size) {
      state.openThreads = openFolds.has('threads') || state.openThreads;
      state.openReturned = openFolds.has('returned') || state.openReturned;
    }
    render();
  }

  wrap.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.closest('[data-relay-refresh]')) void refresh();
  });

  void refresh();
  const timer = setInterval(() => void refresh(), 15000);

  return {
    refresh,
    destroy() {
      clearInterval(timer);
      wrap.remove();
    },
  };
}
