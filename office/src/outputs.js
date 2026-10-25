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
import { DONE_STATES, FAILED_STATES, outcomeLabel, stateLabel } from './state-labels.js';
import { escapeHtml } from './html-escape.js';
import { headlineText, parseTranscript, readableText, shortenPath } from './cli-transcript.js';

const $ = (selector) => document.querySelector(selector);
const escape = escapeHtml;

/** 收着显示时最多给多少字（超长的单行 JSON 也要兜住）。 */
const INLINE_LIMIT = 1200;

const api = async (route) => {
  const response = await fetch(`/api/formal${route}`, { cache: 'no-store' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
};

const state = { status: null, outputs: [], scope: null, group: null, filter: 'all', error: null, expanded: new Set() };

/** 窗口的登记名（平台的 `codex-window` → 「Codex 窗口」）。 */
function harnessName(id) {
  if (!id) return '执行方未知';
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
    // 筛选按**活的状态**：一条 merged 的活，它每一轮都是"这条活做成了"；
    // 用 attempt 那个停在 verifying 的旧状态去筛，会一条都筛不出来。
    const keep =
      state.filter === 'all'
        ? true
        : state.filter === 'done'
          ? DONE_STATES.has(String(task.status))
          : FAILED_STATES.has(String(task.status));
    if (!keep) continue;
    for (const attempt of task.attempts || []) {
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

/**
 * 长文本只给开头几行，其余折起来。
 *
 * 真数据里一条活的结论有 386 行（字段映射表），整段摆出来人读到的是一堵墙。
 */
function clipLines(text, limit) {
  const lines = String(text || '').split('\n');
  if (lines.length <= limit) return { text: lines.join('\n'), rest: 0, restText: '' };
  return {
    text: lines.slice(0, limit).join('\n'),
    rest: lines.length - limit,
    restText: lines.slice(limit).join('\n'),
  };
}

/** 一次尝试说出来的话。 */
function words(task, attempt) {
  const stderr = String(attempt.stderr || '');
  // 网关在服务端已经把机器日志拆好了（texts/tools）；老网关没有这一层时，
  // 客户端自己拆一次（同一个解析器）。
  const texts = Array.isArray(attempt.texts) ? attempt.texts : null;
  const tools = Array.isArray(attempt.tools) ? attempt.tools : null;
  const stdout = String(attempt.stdout || '');
  const transcript = texts
    ? { isJson: true, texts, tools: tools || [] }
    : parseTranscript(stdout);
  const key = `${task.task_id}:${attempt.attempt_id}`;
  const expanded = state.expanded.has(key);

  // 机器日志（JSONL）先给人话：结论 + 做了哪些事；原文收在后面。
  if (transcript.isJson && transcript.texts.length) {
    const headline = readableText(headlineText(transcript));
    const others = transcript.texts.slice(0, -1).reverse();
    const steps = transcript.tools.slice(-8).reverse();
    const raw = attempt.stdout_tail ?? stdout.slice(-2000);
    // 结论常常是**很长**的（真数据里有一份 386 行的字段映射表）。整段摆出来，
    // 人读到的是一堵墙 —— 上面那几行才是"做完了什么"，其余收在折叠里。
    const head = clipLines(headline, 6);
    return `
      <div class="outputs-said">
        <div class="outputs-said-head">最后一次输出</div>
        <pre class="outputs-words">${escape(shortenPath(head.text) || '（这段里没有可读的文字）')}</pre>
        ${
          head.rest
            ? `<details class="outputs-more-block"><summary>它还写了 ${head.rest} 行</summary><pre class="outputs-words">${escape(shortenPath(head.restText))}</pre></details>`
            : ''
        }
        ${steps.length ? `<div class="outputs-said-head">它做了什么（最近 ${steps.length} 步）</div><ul class="flat-list outputs-tools">${steps.map((tool) => `<li><span>${escape(tool.label)}</span></li>`).join('')}</ul>` : ''}
        ${others.length ? `<details class="outputs-more-block"><summary>它之前还说了 ${others.length} 段</summary>${others.map((item) => `<pre class="outputs-words">${escape(shortenPath(readableText(item.text)))}</pre>`).join('')}</details>` : ''}
        ${raw.trim() ? `<details class="outputs-more-block"><summary>看原始记录（机器日志）</summary><pre class="outputs-raw">${escape(shortenPath(raw))}</pre></details>` : ''}
      </div>
      ${stderr.trim() ? `<div class="outputs-said-head">它报的错</div><pre class="outputs-err">${escape(shortenPath(readableText(stderr)))}</pre>` : ''}`;
  }

  const plain = stdout || String(attempt.stdout_tail || '');
  const lines = plain.split('\n');
  const long = lines.length > 12;
  let shown = long && !expanded ? lines.slice(-12).join('\n') : plain;
  // 真数据实测：有些窗口吐出的是**一整行超长的 JSON**（会话日志），按行截断
  // 等于没截 —— 4000 字直接糊在脸上。再按字数兜一道底。
  let clipped = long && !expanded;
  if (shown.length > INLINE_LIMIT) {
    const dropped = shown.length - INLINE_LIMIT;
    shown = `…（前面还有 ${dropped} 字）\n${shown.slice(-INLINE_LIMIT)}`;
    clipped = true;
  }
  return `
    <pre class="outputs-words ${clipped ? 'is-clipped' : ''}">${escape(shortenPath(shown) || '（无输出）')}</pre>
    ${long ? `<button type="button" class="outputs-more" data-more="${escape(key)}">${expanded ? '收起' : `展开全部 ${lines.length} 行`}</button>` : ''}
    ${stderr.trim() ? `<pre class="outputs-err">${escape(shortenPath(stderr))}</pre>` : ''}`;
}

/**
 * 这是哪个项目。
 *
 * 本机开着办公室时，localStorage 里有项目名（那才是人起的名字）；这个独立
 * 窗口可能是在没有本机项目的情况下直接打开的。群号是机器 id（`office-formal-2`），
 * 摆出来也没人认得 —— 说"这个项目"至少不骗人。
 */
function scopeName() {
  if (state.scope && state.scope.name) return state.scope.name;
  return '这个项目';
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
        <h1>各窗口输出记录</h1>
        <p>${escape(scopeName())} · ${total} 次尝试留下了话</p>
        <p class="outputs-note">执行成员每完成一次任务，都会留下一段<b>其自身输出</b>（测试怎么报的错、改了哪些文件、最后写了什么）。这里是这些原文，按窗口摊开；一条活做过几次，这里就摊几次，新的那次在前。</p>
        <div class="outputs-tools">
          <label for="outputsFilter">看</label>
          <select id="outputsFilter">
            <option value="all"${state.filter === 'all' ? ' selected' : ''}>全部</option>
            <option value="done"${state.filter === 'done' ? ' selected' : ''}>已完成的</option>
            <option value="failed"${state.filter === 'failed' ? ' selected' : ''}>未完成的</option>
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
                              // 这一轮的结果来自**事件**，不是那个停在 verifying 的
                              // attempt 状态：交上去了 / 放弃了（带原因）/ 放开了。
                              const outcome = attempt.outcome
                                ? `<span class="outputs-tag ${attempt.outcome === '交上去了' ? 'is-done' : 'is-stuck'}">这一轮：${escape(outcomeLabel(attempt.outcome))}</span>`
                                : '';
                              const reason = attempt.reason
                                ? `<p class="outputs-reason">它当时说：${escape(attempt.reason)}</p>`
                                : '';
                              return `<div class="outputs-attempt">
                                <div class="outputs-attempt-head">
                                  <b>第 ${escape(String(attempt.attempt_no))} 次尝试</b>
                                  ${outcome}
                                </div>
                                ${reason}
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
                    ? '这些任务尚未完成 —— 切换至「全部」可查看其当前输出。'
                    : '尚无窗口留下输出。任务开始执行后，其输出将在此展示。'
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
    state.group = outputs.group_id || null;
    if (!state.error) state.error = null;
  } catch (error) {
    state.error = String(error.message || error);
  }
  render();
}

void refresh();
// 这个页面是"看历史"的：一次要向平台要几十条活、每一轮的原话，15 秒一轮
// 太勤了（真平台上一次 45 个上游请求）。60 秒一轮，想立刻看新的按「刷新」。
setInterval(() => void refresh(), 60000);
