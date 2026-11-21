import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

// 覆盖面（2026-11-20 扩面）：原来只 goto('/')，于是 `outputs.html` / `direct.html`
// 这两个独立入口、以及「大厅里已经有选中项目」这个状态**天生漏网** —— 主题层
// 换 `--ao-faint`、换选中卡底色出的事，45 条全绿也发现不了。这里补两个维度：
//   · 入口：`/`、`/outputs.html`、`/direct.html`，每个入口都跑满 5 档视口；
//   · 状态：大厅空态 / 创建对话框 / 连接对话框 / 有项目 / **有选中项目** / 任务 /
//           详情 / 群聊 / 助手 / 开工向导；产出页空态 + 有记录；私聊空态 + 有记录。
// 阈值一律不动（正文 4.5、大字 3），检查项只增不减，空态页也不跳。
const dist = fileURLToPath(new URL('../dist/', import.meta.url));
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'office-design-'));
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  const pathname = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname);
  const file = path.resolve(dist, `.${pathname === '/' ? '/index.html' : pathname}`);
  if (!file.startsWith(dist) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { 'Content-Type': `${types[path.extname(file)] || 'application/octet-stream'}; charset=utf-8` });
  res.end(fs.readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = process.env.OFFICE_URL || `http://127.0.0.1:${server.address().port}`;
const admin = 'codex-window-admin';
const member = 'qoder-member-1';
const stamp = new Date().toISOString();
const harnesses = [
  { harness_id: admin, name: 'Codex · 管理员', provider: 'openai' },
  { harness_id: member, name: 'Qoder · 前端工程', provider: 'qoder' },
  { harness_id: 'claude-member-1', name: 'Claude · 质量审核', provider: 'anthropic' },
].map(item => ({ ...item, status: 'online', registration_version: 'reg-1', credential_count: 1, capabilities: ['coding'], running_tasks: 0 }));
const tasks = ['in_progress', 'awaiting_human', 'ready', 'approved'].map((status, index) => ({
  task_id: `UI-${index + 1}`, group_id: 'design-group', title: `产品体验升级 · ${['建立一致的组件与交互规范', '确认项目验收范围', '完成移动端布局回归', '梳理协作任务与成员分工'][index]}`,
  status, owner: index === 2 ? null : member, attempt_id: `attempt-${index + 1}`, attempt_no: 1, max_attempts: 3, epoch: 1,
  assigned_harness_ids_json: JSON.stringify([member]), created_at: stamp, updated_at: stamp, state_changed_seq: index + 1,
}));
const project = { id: 'project-design', index: '01', name: '产品体验升级', createdAt: '2026/10/28 10:00:00', phase: 'scene', source: 'platform', members: [member], taskIds: tasks.map(task => task.task_id) };
// 私聊页只认 `refs` 里带 `direct`、且收件人是管理员（或管理员自己发的）消息（见 src/direct-chat.js:41-52）。
const directMessages = [
  { message_id: 'd-1', created_seq: 1, sender_type: 'human', sender_id: 'office-operator', body: '这轮的间距按 8 的倍数收一下。', refs: ['direct'], recipients: [admin], mentions: [admin], created_at: stamp },
  { message_id: 'd-2', created_seq: 2, sender_type: 'agent', sender_id: admin, body: '收到，先改令牌层再回归。', refs: ['direct'], recipients: ['office-operator'], mentions: ['office-operator'], created_at: stamp },
];
// 产出页的空态与有记录态都要过（空态页恰恰是 `--ao-faint` 影响面最大的地方）。
const outputsRecords = {
  group_id: 'office-formal-2',
  outputs: [
    { ...tasks[0], attempt_total: 2, attempts: [
      { harness_id: member, attempt_no: 2, outcome: '交上去了', text: '改完令牌层，两条门禁都跑过。', stderr: '' },
      { harness_id: member, attempt_no: 1, outcome: '放开了', reason: '级联顺序不对', text: '先改顺序。', stderr: 'Error: cascade order' },
    ] },
    { ...tasks[3], attempt_total: 1, attempts: [
      { harness_id: member, attempt_no: 1, outcome: '交上去了', text: '补了长路径换行。', stderr: '' },
    ] },
  ],
};
const fixtures = {
    // 左栏只列**本项目的人**（`project_members`）：平台上登记但本群没活的
  // 窗口不是本项目的会话，不占一行（2026-11-18 的口径）。
  '/status': { configured: true, connected: true, project_id: 'design', group_id: 'design-group', operator: 'office-operator', admin: { configured: true, identity: admin }, project_members: [member, 'claude-member-1'], harnesses, base_adapters: [] },
  '/tasks': { tasks },
  '/availability': { availability: harnesses.map(item => ({ ...item, adapter: 'cli', model: '模型由窗口提供', model_source: 'cfg', usable: true, warnings: [], reasons: [], last_task_id: null })) },
  '/events': { events: [], next_seq: 0, has_more: false },
  '/messages': { messages: [{ message_id: 'm-1', created_seq: 1, sender_type: 'human', sender_id: 'office-operator', body: '优先统一核心页面体验，并保留现有协作流程。', refs: [], mentions: [], created_at: stamp }], next_seq: 1, has_more: false },
  '/pool': { upstreams: [], models: [], usage: { requests: 0, consumers: {} } },
  '/plan': { plans: [], draft: null },
};
// 独立页的数据开关：先空态、再切有记录，两个都要被量到。
const standalone = { outputs: 'empty', messages: 'empty', page: 'index' };

function contrastReport() {
  const rgb = value => {
    const parts = value.match(/[\d.]+/g)?.map(Number);
    return parts?.length >= 3 ? [...parts.slice(0, 3), parts[3] ?? 1] : [0, 0, 0, 0];
  };
  const over = (fg, bg) => [...fg.slice(0, 3).map((value, index) => value * fg[3] + bg[index] * (1 - fg[3])), 1];
  const luminance = color => color.slice(0, 3).map(value => value / 255).map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
  const failures = [];
  let checked = 0;
  let minimum = 100;
  const scope = document.querySelector('dialog:modal') || document.body;
  for (const element of scope.querySelectorAll('*')) {
    if (!(element instanceof HTMLElement) || element.closest('[hidden], dialog:not([open]), button:disabled, option, script, style')) continue;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height || rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth) continue;
    let visible = true;
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      const style = getComputedStyle(parent);
      const bounds = parent.getBoundingClientRect();
      if (style.visibility === 'hidden' || Number(style.opacity) === 0 || (/(auto|scroll|hidden|clip)/.test(style.overflowY) && (rect.top >= bounds.bottom || rect.bottom <= bounds.top))) visible = false;
    }
    if (!visible) continue;
    const hasText = [...element.childNodes].some(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
    const placeholder = element.matches('input[placeholder], textarea[placeholder]') && !element.value;
    if (!hasText && !placeholder && !element.matches('input[type=text], select')) continue;
    const style = getComputedStyle(element, placeholder ? '::placeholder' : null);
    if (style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
    let background = [255, 255, 255, 1];
    const ancestors = [];
    for (let item = element; item; item = item.parentElement) ancestors.unshift(item);
    for (const item of ancestors) background = over(rgb(getComputedStyle(item).backgroundColor), background);
    const foreground = over(rgb(style.color), background);
    const a = luminance(foreground), b = luminance(background);
    const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    const large = parseFloat(style.fontSize) >= 24 || (parseFloat(style.fontSize) >= 18.66 && Number(style.fontWeight) >= 700);
    checked++;
    minimum = Math.min(minimum, ratio);
    if (ratio < (large ? 3 : 4.5)) failures.push({ selector: element.className || element.tagName, text: (placeholder ? element.placeholder : element.textContent).trim().slice(0, 45), ratio: +ratio.toFixed(2) });
  }
  return { checked, minimum: +minimum.toFixed(2), failures };
}

const browser = await chromium.launch({ headless: true });
const results = [];
const proofs = [];
const summary = [];
try {
  for (const width of [1600, 1280, 900, 768, 375]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce' });
    const errors = [];
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await context.route('**/api/**', route => {
      const pathname = new URL(route.request().url()).pathname.replace('/api/formal', '');
      const task = tasks.find(item => pathname === `/tasks/${item.task_id}`);
      let payload;
      if (task) payload = { task: { ...task, spec: { goal: '在不改变业务流程的前提下，统一页面层级、间距与交互状态。', acceptance: ['主要操作可用', '窄屏无横向溢出'] } } };
      else if (pathname === '/api/harnesses/status') payload = { catalog: [], local_endpoints: [] };
      else if (pathname === '/outputs') payload = standalone.outputs === 'records' ? outputsRecords : { group_id: 'office-formal-2', outputs: [] };
      else if (pathname === '/messages' && standalone.page === 'direct') payload = { messages: standalone.messages === 'records' ? directMessages : [], next_seq: 0, has_more: false };
      else payload = fixtures[pathname] ?? {};
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
    });

    let entry = 'index';
    let states = 0;
    let measured = 0;
    const stateOf = name => [...results].filter(item => item.entry === entry && item.width === width && item.name === name);
    const check = async name => {
      const report = await page.evaluate(contrastReport);
      assert.ok(report.checked > 5, `${width}px ${entry}/${name}: must measure actual rendered text (got ${report.checked})`);
      assert.deepEqual(report.failures, [], `${width}px ${entry}/${name}: contrast ${JSON.stringify(report.failures)}`);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      assert.equal(overflow, false, `${width}px ${entry}/${name}: page overflow`);
      results.push({ entry, width, name, ...report });
      states += 1;
      measured += report.checked;
    };
    // 注入即红 / 撤掉回绿的通用自证：门禁必须能抓住这类退化，否则"扩面"是假的。
    // 注意：注入态本身就是**该红**的状态，所以这里直接记录、不走 check（check 断言空 failures）。
    const proof = async (label, expect) => {
      const report = await page.evaluate(contrastReport);
      const hit = report.failures.find(expect);
      assert.ok(hit, `${width}px ${label}: 注入后门禁没有变红，说明这条检查是假的`);
      proofs.push({ width, label, caught: hit });
      results.push({ entry, width, name: label.replace('注入：', 'injected-'), injected: true, ...report });
    };

    // ---------------------------------------------------------------- 入口 1：办公室本体
    entry = 'index';
    standalone.page = 'index';
    await page.goto(base);
    await page.locator('.project-empty').waitFor();
    await check('empty-lobby');
    await page.locator('#projectLobbyTitle').evaluate(element => { element.style.color = '#eeeeee'; });
    assert.ok((await page.evaluate(contrastReport)).failures.some(item => item.text === '项目空间'), 'contrast check must reject low-contrast heading');
    await page.locator('#projectLobbyTitle').evaluate(element => { element.style.removeProperty('color'); });
    await page.keyboard.press('Tab');
    const focus = await page.evaluate(() => ({ visible: document.activeElement.matches(':focus-visible'), outline: getComputedStyle(document.activeElement).outlineStyle }));
    assert.equal(focus.visible, true, 'keyboard focus is visible');
    assert.notEqual(focus.outline, 'none', 'keyboard focus has outline');
    await page.locator('[data-new-project]').click();
    await page.locator('.project-create-dialog[open]').waitFor();
    await check('create-dialog');
    if (width === 1600 || width === 375) await page.screenshot({ path: path.join(output, `create-${width}.png`) });
    await page.locator('[data-project-name]').fill('新的协作项目');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('dialog[open]').count(), 0);
    await page.locator('[data-open-connections]').click();
    await page.locator('.harness-dialog[open]').waitFor();
    await check('connections-dialog');
    await page.locator('[data-close-connections]').click();
    await page.evaluate(value => localStorage.setItem('agent-office.projects.v1', JSON.stringify([value])), project);
    await page.reload();
    await page.locator('.project-card-open').waitFor();
    await check('project-lobby');
    if (width === 1600) await page.screenshot({ path: path.join(output, 'lobby-1600.png') });

    // 新增状态：大厅里已经有选中项目（`active-project.v1` 存的是**纯 id 字符串**，
    // 不是 JSON 对象 —— 写错了这张卡根本不会带上 data-active，等于没测）。
    await page.evaluate(value => localStorage.setItem('agent-office.active-project.v1', value), project.id);
    await page.reload();
    await page.locator('.project-card[data-active="1"]').waitFor();
    await check('project-lobby-active');
    // 自证 ①：把选中卡恢复成"橙字压橙底"（主题第一版就是这个组合，4.37:1），门禁必须红。
    // 选择器要跟主题层同权重（0,3,0）才压得住，否则字色被 `.project-card[data-active="1"] .project-card-open` 挡回去。
    const orangeTea = await page.addStyleTag({ content: '.project-card[data-active="1"] { background: var(--ao-accent-soft); } .project-card[data-active="1"] .project-card-open { color: var(--ao-accent-ink); }' });
    await proof('注入：选中卡橙字压橙底', item => /进入办公室/.test(item.text) && item.ratio < 4.5);
    await orangeTea.evaluate(element => element.remove());
    assert.deepEqual((await page.evaluate(contrastReport)).failures, [], `${width}px 撤掉注入后大厅必须回绿`);

    await page.locator('.project-card-open').click();
    await page.locator('.flat-task').first().waitFor();
    await check('tasks');
    const clippedActions = await page.locator('.ao-topbar button').evaluateAll(buttons => buttons.filter(button => {
      const rect = button.getBoundingClientRect();
      return rect.width && (rect.left < 0 || rect.right > innerWidth || button.scrollWidth > button.clientWidth + 1);
    }).map(button => button.textContent.trim()));
    assert.deepEqual(clippedActions, [], `${width}px clipped topbar actions`);
    const overlap = await page.evaluate(() => {
      const selectors = ['.flat-convs', '.flat-main', '.flat-rail'];
      const boxes = selectors.map(selector => document.querySelector(selector).getBoundingClientRect());
      return boxes.some((a, index) => boxes.slice(index + 1).some(b => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2));
    });
    assert.equal(overlap, false, `${width}px workspace regions overlap`);
    await page.screenshot({ path: path.join(output, `tasks-${width}.png`) });
    await page.locator('.flat-task').first().click();
    await page.locator('.flat-back').waitFor();
    await check('detail');
    await page.locator('.flat-back').click();
    await page.locator('.flat-conv', { hasText: '项目群聊' }).click();
    await page.locator('.flat-chat-form input').waitFor();
    await page.locator('.flat-chat-form input').fill('仅验证草稿，不发送');
    await check('chat');
    await page.locator('.flat-conv', { hasText: '平台助手' }).click();
    await page.locator('.flat-assistant-input').waitFor();
    await check('assistant');
    const faintPlaceholder = await page.addStyleTag({ content: '.flat-assistant-input::placeholder { color: #eeeeee !important; }' });
    assert.ok((await page.evaluate(contrastReport)).failures.some(item => item.selector.includes('flat-assistant-input')), 'contrast check must measure placeholders');
    await faintPlaceholder.evaluate(element => element.remove());
    if (width <= 860) {
      const lastMember = page.locator('.flat-conv[data-harness="claude-member-1"]');
      await lastMember.scrollIntoViewIfNeeded();
      const position = await page.locator('.flat-convs').evaluate(element => element.scrollLeft);
      assert.ok(position > 0, 'member is reached by horizontal navigation');
      await lastMember.click();
      const selectedIsVisible = async () => page.locator('.flat-conv.is-on').evaluate(element => {
        const item = element.getBoundingClientRect();
        const pane = element.parentElement.getBoundingClientRect();
        return item.left >= pane.left - 1 && item.right <= pane.right + 1;
      });
      assert.equal(await selectedIsVisible(), true, `${width}px selected member remains visible`);
      assert.ok(Math.abs(await page.locator('.flat-convs').evaluate(element => element.scrollLeft) - position) <= 1, 'selection preserves navigation scroll');
      await page.evaluate(() => window.officePreviewState.office.refresh());
      assert.equal(await selectedIsVisible(), true, `${width}px refresh preserves selected member visibility`);
      assert.ok(Math.abs(await page.locator('.flat-convs').evaluate(element => element.scrollLeft) - position) <= 1, 'refresh preserves navigation scroll');
    }
    await page.locator('#aoLobbyBtn').click();
    await page.locator('[data-new-project]').click();
    await page.locator('[data-project-name]').fill('新建流程验证');
    await page.locator('[data-confirm-create]').click();
    await page.locator('#openingDialog[open]').waitFor();
    await check('opening-dialog');
    if (width === 1600 || width === 375) await page.screenshot({ path: path.join(output, `opening-${width}.png`) });
    assert.equal(await page.locator('#aoWorkspace').isVisible(), true);
    assert.deepEqual(errors, [], `${width}px JavaScript errors`);
    summary.push({ entry, width, states, measured });

    // ---------------------------------------------------------------- 入口 2：交付物档案
    entry = 'outputs';
    standalone.page = 'outputs';
    standalone.outputs = 'empty';
    states = 0;
    measured = 0;
    await page.goto(`${base}/outputs.html`);
    await page.locator('.outputs-empty, .outputs-window').first().waitFor();
    await check('outputs-empty');
    if (width === 1600) await page.screenshot({ path: path.join(output, 'outputs-empty-1600.png') });
    standalone.outputs = 'records';
    await page.reload();
    await page.locator('.outputs-window').first().waitFor();
    await check('outputs-records');
    // 自证 ②：把 `--ao-faint` 注回 #8B8175（主题第一版的值），门禁必须红；
    // 这个令牌只服务这两个独立页，所以必须在这两个入口上自证，index 上注入是无效的。
    const faintBack = await page.addStyleTag({ content: ':root { --ao-faint: #8B8175; }' });
    await proof('注入：--ao-faint 退回 #8B8175', item => item.ratio < 4.5);
    await faintBack.evaluate(element => element.remove());
    assert.deepEqual((await page.evaluate(contrastReport)).failures, [], `${width}px 撤掉注入后产出页必须回绿`);
    if (width === 1600) await page.screenshot({ path: path.join(output, 'outputs-1600.png') });
    assert.deepEqual(errors, [], `${width}px outputs JavaScript errors`);
    summary.push({ entry, width, states, measured });

    // ---------------------------------------------------------------- 入口 3：私聊
    entry = 'direct';
    standalone.page = 'direct';
    standalone.messages = 'empty';
    states = 0;
    measured = 0;
    await page.goto(`${base}/direct.html`);
    await page.locator('.direct-empty, .direct-msg').first().waitFor();
    await check('direct-empty');
    standalone.messages = 'records';
    await page.reload();
    await page.locator('.direct-msg').first().waitFor();
    await check('direct-records');
    const faintBackDirect = await page.addStyleTag({ content: ':root { --ao-faint: #8B8175; }' });
    await proof('注入：私聊 --ao-faint 退回 #8B8175', item => item.ratio < 4.5);
    await faintBackDirect.evaluate(element => element.remove());
    assert.deepEqual((await page.evaluate(contrastReport)).failures, [], `${width}px 撤掉注入后私聊必须回绿`);
    if (width === 1600) await page.screenshot({ path: path.join(output, 'direct-1600.png') });
    assert.deepEqual(errors, [], `${width}px direct JavaScript errors`);
    summary.push({ entry, width, states, measured });

    await context.close();
  }
  const totals = summary.reduce((acc, item) => {
    acc[item.entry] = acc[item.entry] || { states: 0, measured: 0 };
    acc[item.entry].states += item.states;
    acc[item.entry].measured += item.measured;
    return acc;
  }, {});
  console.log(JSON.stringify({ summary, totals, proofs, results, screenshots: output }, null, 2));
  console.log(`verify-office-design: ok（3 个入口 × 5 档视口；index ${totals.index.states} 状态/${totals.index.measured} 处文字，outputs ${totals.outputs.states}/${totals.outputs.measured}，direct ${totals.direct.states}/${totals.direct.measured}）`);
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
