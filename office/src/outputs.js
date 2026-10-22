// 「各窗口说了什么」（独立页面）。
//
// 平台记的是**结果**（谁接了、谁提交了、审没审过），但人真正想看的往往是
// **窗口自己说的话** —— 那是它跑出来的东西：测试怎么报的错、改了哪些文件、
// 最后留下了什么。这个页面把这些话按窗口摊开，一眼看出谁在认真干活、
// 谁在瞎试。
//
// 数据来自平台的 `/tasks/{id}/output`（每一次 attempt 的 CLI 输出尾巴）。
// 页面本身只读，不发任何写。
import './outputs.css';
import { DONE_STATES, stateLabel } from './state-labels.js';
import { escapeHtml } from './html-escape.js';

const $ = (selector) => document.querySelector(selector);
const escape = escapeHtml;

const api = async (route) => {
  const response = await fetch(`/api/formal${route}`, { cache: 'no-store' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
};

const state = { status: null, outputs: [], scope: null, filter: 'done', error: null, expanded: new Set() };

/** 窗口的登记名（平台的 `codex-window` → 「Codex 窗口」）。 */
function harnessName(id) {
  if (!id) return '不知道是谁';
  const roster = (state.status && state.status.harnesses) || [];
  const hit = roster.find((item) => item.harness_id === id);
  return (hit && hit.name) || id;
}

function when(value) {
  const at = Date.parse(value || '');
  if (Number.isNaN(at)) return '';
  return new Date(at).toLocaleString('zh-CN', {
    hour12: false,
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** 列表里的任务名：去掉重复的项目名前缀（页面顶上已经写了是哪个项目）。 */
function titleOf(item) {
  const title = String(item.title || item.task_id || '');
  const name = String((state.scope && state.scope.name) || '').trim();
  const prefix = name ? `${name} · ` : '';
  return prefix && title.startsWith(prefix) ? title.slice(prefix.length) : title;
}

/**
 * 按窗口分组；同一条活在这个窗口下的多次尝试收在一起。
 *
 * 分组的键是**这一次尝试是谁做的**（attempt 的 owner），不是这条活现在的
 * 主人 —— 改派过之后，这两者不是一回事，而"谁当时说了什么"正是这里要的。
 */
function grouped() {
  const map = new Map();
  let shown = 0;
  for (const task of state.outputs || []) {
    const attempts = (task.attempts || []).filter((attempt) =>
      state.filter === 'all' ? true : DONE_STATES.has(String(attempt.attempt_status || task.status)),
    );
    for (const attempt of attempts) {
      const key = attempt.harness_id || task.owner || 'unknown';
      if (!map.has(key)) map.set(key, new Map());
      const tasks = map.get(key);
      if (!tasks.has(task.task_id)) {
        tasks.set(task.task_id, { task, attempts: [] });
      }
      tasks.get(task.task_id).attempts.push(attempt);
      shown += 1;
    }
  }
  const windows = [...map.entries()].map(([id, tasks]) => {
    const items = [...tasks.values()].map((entry) => ({
      ...entry,
      attempts: entry.attempts.sort(
        (a, b) => Number(b.attempt_no || 0) - Number(a.attempt_no || 0),
      ),
    }));
    return {
      id,
      name: harnessName(id),
      items,
      count: items.reduce((sum, entry) => sum + entry.attempts.length, 0),
    };
  });
  return { windows: windows.sort((a, b) => b.count - a.count), shown };
}

/** 一次尝试说出来的话。 */
/** 一次尝试说出来的话。 */
function words(task, attempt) {
  const stdout = String(attempt.stdout || '');
  const stderr = String(attempt.stderr || '');
  const lines = stdout.split('\n');
  const long = lines.length > 12;
  const key = `${task.task_id}:${attempt.attempt_id}`;
  const expanded = state.expanded.has(key);
  const shown = long && !expanded ? lines.slice(-12).join('\n') : stdout;
  return `
    <pre class="outputs-words ${long && !expanded ? 'is-clipped' : ''}">${escape(shown || '（它什么也没输出）')}</pre>
    ${long ? `<button type="button" class="outputs-more" data-more="${escape(key)}">${expanded ? '收起' : `展开全部 ${lines.length} 行`}</button>` : ''}
    ${stderr.trim() ? `<pre class="outputs-err">${escape(stderr)}</pre>` : ''}`;
}

function render() {
  const root = $('#outputsApp');
  const { windows, shown } = grouped();
  const total = (state.outputs || []).reduce(
    (sum, task) => sum + (task.attempts || []).length,
    0,
  );
  root.innerHTML = `
    <div class="outputs-shell">
      <header class="outputs-head">
        <h1>各个窗口都说了什么</h1>
        <p>${state.scope ? escape(state.scope.name) : '本项目'} · ${total} 次尝试留下了话</p>
        <p class="outputs-note">干活的人每做一次活，都会留下一段<b>它自己的话</b>（测试怎么报的错、改了哪些文件、最后写了什么）。这里是这些原文，按窗口摊开；一条活做过几次，这里就摊几次，新的那次在前。</p>
        <div class="outputs-tools">
          <label for="outputsFilter">看</label>
          <select id="outputsFilter">
            <option value="done"${state.filter === 'done' ? ' selected' : ''}>做完了的</option>
            <option value="all"${state.filter === 'all' ? ' selected' : ''}>全部</option>
          </select>
          <button type="button" id="outputsRefresh">刷新</button>
          <span class="outputs-count">显示 ${shown} 次</span>
        </div>
      </header>
      <div class="outputs-list">
        ${
          windows.length
            ? windows
                .map(
                  (group) => `<section class="outputs-window">
                    <header><b>${escape(group.name)}</b><span>${group.count} 次</span></header>
                    ${group.items
                      .map((entry) => {
                        const { task, attempts } = entry;
                        const done = DONE_STATES.has(String(task.status));
                        const stuck = ['abandoned', 'stale', 'cancelled', 'escalated'].includes(
                          String(task.status),
                        );
                        const total = Number(task.attempt_total || 0);
                        return `<article class="outputs-item">
                          <header>
                            <b>${escape(titleOf(task))}</b>
                            <span class="outputs-tag ${done ? 'is-done' : ''} ${stuck ? 'is-stuck' : ''}">${escape(stateLabel(task.status))}</span>
                            ${total > 1 ? `<span class="outputs-tag">做了 ${total} 次</span>` : ''}
                            <time>${escape(when(task.updated_at))}</time>
                          </header>
                          ${attempts
                            .map((attempt) => {
                              // 这一轮的状态只有在**和活的状态不一样**时才值得说 ——
                              // 两个一样的标签并排挂着只会让人以为页面坏了。
                              const round =
                                attempt.attempt_status && attempt.attempt_status !== task.status
                                  ? `<span class="outputs-tag">这一轮：${escape(stateLabel(attempt.attempt_status))}</span>`
                                  : '';
                              return `<div class="outputs-attempt">
                                <div class="outputs-attempt-head">
                                  <b>第 ${escape(String(attempt.attempt_no))} 次尝试</b>
                                  ${round}
                                </div>
                                ${words(task, attempt)}
                              </div>`;
                            })
                            .join('')}
                        </article>`;
                      })
                      .join('')}
                  </section>`,
                )
                .join('')
            : `<p class="outputs-empty">${
                state.error
                  ? ''
                  : total
                    ? '这些活都还没做完 —— 切到「全部」能看到它们现在说了什么。'
                    : '还没有窗口留下过话。等有活跑起来，这里就会摊开它们说了什么。'
              }</p>`
        }
        ${state.error ? `<p class="outputs-error">读不到：${escape(state.error)}</p>` : ''}
      </div>
    </div>`;

  $('#outputsRefresh').addEventListener('click', () => void refresh());
  $('#outputsFilter').addEventListener('change', (event) => {
    state.filter = event.target.value;
    render();
  });
  for (const button of root.querySelectorAll('[data-more]')) {
    button.addEventListener('click', () => {
      const id = button.dataset.more;
      if (state.expanded.has(id)) state.expanded.delete(id);
      else state.expanded.add(id);
      render();
    });
  }
}

/** 本机当前打开的项目（页面顶上要写是哪个项目，任务名里也有它）。 */
function activeProject() {
  try {
    const raw = localStorage.getItem('agent-office.active-project.v1');
    const projects = JSON.parse(localStorage.getItem('agent-office.projects.v1') || '[]');
    const hit = (projects || []).find((item) => item && item.id === raw);
    return hit ? { name: hit.name || hit.id } : null;
  } catch {
    return null;
  }
}

async function refresh() {
  try {
    const [status, outputs] = await Promise.all([
      api('/status').catch(() => null),
      api('/outputs').catch((error) => {
        state.error = String(error.message || error);
        return { outputs: [] };
      }),
    ]);
    state.status = status;
    state.scope = activeProject();
    state.outputs = outputs.outputs || [];
    if (!state.error) state.error = null;
  } catch (error) {
    state.error = String(error.message || error);
  }
  render();
}

void refresh();
setInterval(() => void refresh(), 15000);
