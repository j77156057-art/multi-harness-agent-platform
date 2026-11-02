/**
 * 转接视图：一个项目选了「转接」之后，办公室就换成这一页。
 *
 * 为什么单独一页而不是在办公室上加一块：转接这条路**没有任务**——不建任务、不开工作树、
 * 不判内容，平台只按「给<收件人>：」把信投到各窗口的收件箱。任务表、方案、审核、合入
 * 在这里统统不适用，硬塞进派单那套界面只会让人以为「怎么什么都没有」。所以分开：
 * 这一页只讲三件事——在不在跑、有几条线、哪封没投出去。
 *
 * 边界（见 docs/notes/relay-mode.md §16）：
 *   · 除了「写一封信」，这一页**只读**：不改信箱、不动 archive/。
 *   · 写信的作者永远是**当前这个人**：第一行「来自 …」由平台按令牌主体写死，
 *     界面上没有"以哪个窗口的身份发"这种选项 —— 那会让收信方无从分辨谁说的。
 *   · 信**不进收件箱**，只进 outbox，然后由邮局按同一套判据投递（名册、跳数、回路）。
 *     绕开 outbox 等于绕开三个刹车。
 *   · 不摆进度百分比，也不猜进度：转接没有进度可言，只有"投了/退回了"。
 *   · 长清单默认收起，标题上写明几条（展开才看得见的清单等于没写）。
 */
import './relay-workspace.css';

const OVERVIEW = '/api/formal/relay/overview';
const ROSTER = '/api/formal/relay/roster';
const LETTERS = '/api/formal/relay/letters';
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

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data && data.error;
    // 平台的回包有两种形状：网关自己的 `{error:"码", message:"…"}`，和平台的
    // `{error:{code, message}}`。两种都要能读出那句话，别把对象直接印出来。
    const text = (detail && detail.message) || (typeof detail === 'string' ? data.message || detail : '') || `HTTP ${response.status}`;
    throw new Error(text);
  }
  return data;
}

const fetchOverview = () => fetchJson(OVERVIEW);
const fetchRoster = () => fetchJson(ROSTER);

function stateLine(overview) {
  if (!overview || !overview.heartbeat_at) {
    return {
      tone: 'stopped',
      text: '平台侧还没有在跑转接：信放进信箱不会有人投。转接由管理员窗口负责拉起 —— 长期如此说明管理员窗口也没在跑。',
    };
  }
  if (!overview.running) {
    return {
      tone: 'stalled',
      text: `平台侧的转接已经停了（最后一次心跳 ${when(overview.heartbeat_at)}）。信还会写进发件箱，但目前没有人投递。`,
    };
  }
  return { tone: 'running', text: `平台侧正在转接（最后心跳 ${when(overview.heartbeat_at)}）。` };
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
    roster: null,
    error: '',
    openThreads: false,
    openReturned: false,
    openHowto: false,
    // 草稿要活过每 15 秒一次的重画：人正在写一半时被清空，比没有这个功能更坏。
    draft: { recipients: new Set(), body: '', replyTo: '' },
    sending: false,
    feedback: null,
  };

  function projectName() {
    const current = typeof project === 'function' ? project() : project;
    return (current && current.name) || '转接项目';
  }

  function recipientsOf(roster, kind) {
    const list = (roster && roster[kind]) || [];
    return list.filter((item) => item && item.id);
  }

  function recipientsBlock() {
    const people = recipientsOf(state.roster, 'people');
    const windows = recipientsOf(state.roster, 'windows');
    if (!people.length && !windows.length) {
      return '<p class="relay-empty">名册还没读到：平台库里既没有登记过的窗口，也没有人。先让窗口登记，再回来写信。</p>';
    }
    const group = (title, list) =>
      list.length
        ? `<div class="relay-pick-group">
            <p class="relay-pick-title">${escape(title)} <span class="relay-count">${list.length} 个</span></p>
            ${list
              .map(
                (item) => `<label class="relay-pick">
                  <input type="checkbox" data-recipient="${escape(item.id)}"${
                    state.draft.recipients.has(item.id) ? ' checked' : ''
                  }>
                  <span>${escape(item.id)}</span>
                </label>`,
              )
              .join('')}
          </div>`
        : '';
    return `${group('人', people)}${group('窗口', windows)}`;
  }

  function threadOptions() {
    const threads = (state.overview && state.overview.threads) || [];
    if (!threads.length) return '';
    const options = threads
      .map((thread) => {
        const stamp = threadStatus(thread);
        const who = thread.unreadable ? '参与者不明' : (thread.participants || []).join(' ↔ ');
        const value = escape(thread.thread_id);
        return `<option value="${value}"${state.draft.replyTo === thread.thread_id ? ' selected' : ''}>${
          thread.unreadable ? '账本读不出来' : escape(who)
        } · ${escape(stamp.label)} · ${value}</option>`;
      })
      .join('');
    return `<label class="relay-field">
        <span>这是回信吗</span>
        <select data-reply-to>
          <option value=""${state.draft.replyTo ? '' : ' selected'}>不是，新起一封</option>
          ${options}
        </select>
      </label>`;
  }

  function feedbackBlock() {
    const note = state.feedback;
    if (!note) return '';
    const known = (note.known || []).length
      ? `<p class="relay-hint">可以写：${note.known.map((name) => escape(name)).join('、')}</p>`
      : '';
    return `<div class="relay-feedback relay-feedback-${note.tone}">
        <p>${escape(note.text)}</p>${known}
      </div>`;
  }

  function render() {
    const overview = state.overview;
    const status = stateLine(overview);
    const threads = (overview && overview.threads) || [];
    const returned = (overview && overview.returned) || [];
    const pending = (overview && overview.pending) || 0;
    const author = (state.roster && state.roster.author) || '';
    const canSend = !state.sending && state.draft.recipients.size > 0 && Boolean(state.draft.body.trim());

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

      <section class="relay-card relay-compose">
        <h2>写一封信</h2>
        <p class="relay-note">这封信的作者是<b>你</b>${author ? `（${escape(author)}）` : ''}，第一行「来自 ${author ? escape(author) : '你'}」由平台写死。界面上没有、也不提供「以某个窗口的身份发」—— 要某个窗口说话，让它自己写。</p>
        <fieldset class="relay-picks"${state.sending ? ' disabled' : ''}>
          <legend>发给谁 <span class="relay-count">已选 ${state.draft.recipients.size} 个</span></legend>
          ${recipientsBlock()}
        </fieldset>
        <label class="relay-field">
          <span>说什么</span>
          <textarea data-body rows="6" placeholder="写清要对方做什么、或者告诉他什么。空行会保留。">${escape(state.draft.body)}</textarea>
        </label>
        ${threadOptions()}
        <div class="relay-actions relay-send-row">
          <button type="button" data-relay-send${canSend ? '' : ' disabled'}>${state.sending ? '正在写…' : '写进发件箱'}</button>
          <span class="relay-async">投递是异步的：邮局下一轮扫到，对方<b>下次读信箱</b>才会看到。</span>
        </div>
        ${feedbackBlock()}
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

      <section class="relay-card">
        <details data-relay-fold="howto"${state.openHowto ? ' open' : ''}>
          <summary><h2>成员自己怎么写（不用这个界面）</h2></summary>
          <ol class="relay-steps">
            <li>在 <code>relay/outbox/</code> 放一个 <code>.md</code> 文件（文件名随意，别带路径）。</li>
            <li>第一行写 <code>来自 &lt;你的窗口&gt;</code>；回信再加一行 <code>回复 thr-xxxxxxxx</code>，平台据此接上同一条线程、跳数 +1。</li>
            <li>用 <code>给&lt;收件人&gt;：</code> 分段，一段投给一个窗口；写给管理员用 <code>给管理员：</code>。</li>
            <li>投出去的信挪进 <code>archive/</code>；投不出去的挪进 <code>returned/</code> 并附一份原因，照原因改好重新放进 outbox 即可。</li>
          </ol>
          <p class="relay-note">一条线程最多转 ${MAX_HOPS} 跳；同一对窗口 10 分钟内来回超过 6 次也会被停下。越限一律整封退回并写明原因，不会静默丢弃。</p>
        </details>
      </section>

      <p class="relay-foot">除了「写一封信」，这一页只读：它不改信箱、不动已投过的信。</p>`;
  }

  async function refresh() {
    try {
      const [overview, roster] = await Promise.all([
        fetchOverview(),
        fetchRoster().catch(() => null),
      ]);
      state.overview = overview;
      if (roster) state.roster = roster;
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
      state.openHowto = openFolds.has('howto') || state.openHowto;
    }
    render();
  }

  async function send() {
    if (state.sending) return;
    const body = String(state.draft.body || '').trim();
    const recipients = [...state.draft.recipients];
    if (!body || !recipients.length) return;
    state.sending = true;
    state.feedback = null;
    render();
    try {
      const response = await fetch(LETTERS, {
        method: 'POST',
        cache: 'no-store',
        headers: { 'Content-Type': 'application/json', 'X-Office-Formal': '1' },
        body: JSON.stringify({ recipients, body, reply_to: state.draft.replyTo || '' }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = (data && data.error) || {};
        state.feedback = {
          tone: 'bad',
          text: (detail && detail.message) || `写不进去（${response.status}）`,
          known: (detail && detail.details && detail.details.known) || [],
        };
      } else {
        state.feedback = {
          tone: 'ok',
          text: `已写进发件箱：${data.letter}。投递由邮局下一轮完成，对方下次读信箱才会看到。`,
        };
        state.draft = { recipients: new Set(), body: '', replyTo: '' };
      }
    } catch (error) {
      state.feedback = { tone: 'bad', text: `没能写进去：${String((error && error.message) || error)}` };
    }
    state.sending = false;
    if (state.feedback && state.feedback.tone === 'ok') await refresh();
    else render();
  }

  /**
   * 只按"现在能不能发"更新那一小块，不整页重画。
   *
   * 为什么不能整页重画：重画会把光标从正文里挪走 —— 人正在写的字会跳。所以选中收件人、
   * 敲正文都只碰按钮的 disabled 与那个计数。
   */
  function syncSend() {
    const button = wrap.querySelector('[data-relay-send]');
    if (button) {
      const ready =
        !state.sending && state.draft.recipients.size > 0 && Boolean(String(state.draft.body || '').trim());
      button.disabled = !ready;
    }
    const count = wrap.querySelector('.relay-picks .relay-count');
    if (count) count.textContent = `已选 ${state.draft.recipients.size} 个`;
  }

  wrap.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.closest('[data-relay-refresh]')) void refresh();
    if (target.closest('[data-relay-send]')) void send();
  });

  // 草稿：输入即记，重画时再贴回去（见上）。
  wrap.addEventListener('input', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.matches('[data-body]')) {
      state.draft.body = /** @type {HTMLTextAreaElement} */ (target).value;
      syncSend();
    }
  });
  wrap.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.matches('[data-recipient]')) {
      const box = /** @type {HTMLInputElement} */ (target);
      if (box.checked) state.draft.recipients.add(box.dataset.recipient || '');
      else state.draft.recipients.delete(box.dataset.recipient || '');
      syncSend();
      return;
    }
    if (target.matches('[data-reply-to]')) {
      state.draft.replyTo = /** @type {HTMLSelectElement} */ (target).value;
    }
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
