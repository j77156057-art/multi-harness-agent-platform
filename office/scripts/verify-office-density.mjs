import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.resolve(process.env.OFFICE_DENSITY_DIST || path.join(here, '../dist'));
const label = (process.env.OFFICE_DENSITY_LABEL || 'after').replace(/[^a-z0-9_-]/gi, '-');
const captureOnly = process.env.OFFICE_DENSITY_CAPTURE_ONLY === '1';
const output = path.resolve(here, '../../artifacts/ui-density', label);
const widths = [1600, 1440, 1280, 1024];
const height = 900;
assert.ok(fs.existsSync(path.join(dist, 'index.html')), `先构建，或设置 OFFICE_DENSITY_DIST：${dist}`);
fs.mkdirSync(output, { recursive: true });

const admin = 'codex-window-admin';
const member = 'qoder-member-1';
const stamp = '2026-11-03T08:00:00.000Z';
const harnesses = [
  { harness_id: admin, name: 'Codex · 管理员', provider: 'openai' },
  { harness_id: member, name: 'Qoder · 前端工程', provider: 'qoder' },
  { harness_id: 'claude-member-1', name: 'Claude · 质量审核', provider: 'anthropic' },
].map(item => ({ ...item, status: 'online', registration_version: 'reg-1', credential_count: 1, capabilities: ['coding'], running_tasks: 1 }));
const taskRows = [
  ['in_progress', '统一核心页面信息层级', member],
  ['awaiting_human', '确认项目验收范围', member],
  ['ready', '完成窄屏布局回归', null],
  ['awaiting_review', '核对首屏关键动作', 'claude-member-1'],
  ['approved', '梳理协作任务与成员分工', member],
  ['abandoned', '修复长路径溢出问题', 'claude-member-1'],
];
const tasks = taskRows.map(([status, title, owner], index) => ({
  task_id: `UI-${index + 1}`,
  group_id: 'density-group',
  project_label: '产品体验升级',
  title: `产品体验升级 · ${title}`,
  status,
  owner,
  attempt_id: `attempt-${index + 1}`,
  attempt_no: 1,
  max_attempts: 3,
  epoch: 1,
  assigned_harness_ids_json: JSON.stringify(owner ? [owner] : []),
  created_at: stamp,
  updated_at: `2026-11-03T0${8 + Math.floor(index / 2)}:${index % 2 ? '30' : '00'}:00.000Z`,
  state_changed_seq: index + 1,
  last_failure: status === 'abandoned' ? { reason: '长文本容器需要收敛' } : null,
}));
const projects = [
  { id: 'project-density', index: '01', name: '产品体验升级', createdAt: '2026/10/28 10:00:00', phase: 'scene', source: 'platform', members: [member, 'claude-member-1'], taskIds: tasks.map(task => task.task_id) },
  { id: 'project-release', index: '02', name: '交付链路治理', createdAt: '2026/10/26 09:30:00', phase: 'scene', source: 'platform', members: [member], taskIds: [] },
  { id: 'project-research', index: '03', name: '窗口协作调研', createdAt: '2026/10/21 14:20:00', phase: 'phone', source: 'platform', members: [], taskIds: [] },
];
const status = {
  configured: true,
  connected: true,
  project_id: 'density',
  group_id: 'density-group',
  operator: 'office-operator',
  admin: { configured: true, identity: admin },
  project_members: [member, 'claude-member-1'],
  harnesses,
  base_adapters: [],
};
const installers = {
  probed: true,
  guidance_only: false,
  platform: { os: 'windows', arch: 'x86_64' },
  installers: [
    { harness_id: 'codex', display_name: 'Codex CLI', provider: 'OpenAI', command: 'codex', status: 'ready', version: '0.1.0', driven: true, official_url: 'https://developers.openai.com/codex/cli/', install_command: 'npm install -g @openai/codex', verify_command: 'codex --version' },
    { harness_id: 'qoder', display_name: 'Qoder CLI', provider: 'Qoder', command: 'qoder', status: 'ready', version: '0.2.0', driven: true, official_url: 'https://qoder.com/', install_command: 'npm install -g qoder', verify_command: 'qoder --version' },
    { harness_id: 'claude', display_name: 'Claude Code', provider: 'Anthropic', command: 'claude', status: 'missing', driven: true, official_url: 'https://docs.anthropic.com/', install_command: 'npm install -g @anthropic-ai/claude-code', verify_command: 'claude --version' },
  ],
  summary: { ready: 2, missing: 1, version_failed: 0, unknown: 0 },
};
const outputs = [
  {
    task_id: 'UI-2', title: '产品体验升级 · 确认项目验收范围', status: 'awaiting_human', owner: member, updated_at: stamp, attempt_total: 1,
    attempts: [{ attempt_no: 1, attempt_id: 'UI-2.a1', attempt_status: 'in_progress', outcome: '', reason: '', harness_id: member, current: true, stdout: '已核对四条验收标准。\n请确认 1024 宽度下仍保留运维摘要。', stderr: '' }],
  },
  {
    task_id: 'UI-6', title: '产品体验升级 · 修复长路径溢出问题', status: 'abandoned', owner: 'claude-member-1', updated_at: stamp, attempt_total: 2,
    attempts: [
      { attempt_no: 2, attempt_id: 'UI-6.a2', attempt_status: 'in_progress', outcome: '放弃了', reason: '需要调整布局约束', harness_id: 'claude-member-1', current: true, stdout: '检查任务详情与输出页。\n发现长路径需要 anywhere 换行。', stderr: 'LayoutError: content exceeds container\n' },
      { attempt_no: 1, attempt_id: 'UI-6.a1', attempt_status: 'in_progress', outcome: '放弃了', reason: '等待补充验收范围', harness_id: member, current: false, stdout: '第一轮：已复现。', stderr: '' },
    ],
  },
];
const directMessages = [
  { message_id: 'd1', sender_type: 'human', sender_id: 'office-operator', body: '请确认本轮只调整信息密度，不改变任务流程。', created_seq: 1, created_at: '2026-11-03T08:00:00', refs: ['direct'], recipients: [admin] },
  { message_id: 'd2', sender_type: 'harness', sender_id: admin, body: '收到，功能入口与角色边界保持不变。', created_seq: 2, created_at: '2026-11-03T08:01:00', refs: ['direct'], recipients: ['office-operator'] },
  { message_id: 'd3', sender_type: 'human', sender_id: 'office-operator', body: '重点检查 1024 宽度的运维摘要。', created_seq: 3, created_at: '2026-11-03T08:02:00', refs: ['direct'], recipients: [admin] },
  { message_id: 'd4', sender_type: 'harness', sender_id: admin, body: '已纳入四档视口回归，关键动作会逐项检查。', created_seq: 4, created_at: '2026-11-03T08:03:00', refs: ['direct'], recipients: ['office-operator'] },
];

function fixtureFor(pathname) {
  if (pathname === '/status') return status;
  if (pathname === '/tasks') return { tasks };
  if (pathname === '/availability') return { availability: harnesses.map(item => ({ ...item, adapter: 'cli', model: '模型由窗口提供', model_source: 'cfg', usable: true, warnings: [], reasons: [], last_task_id: null })) };
  if (pathname === '/events') return { events: [], next_seq: 0, has_more: false };
  if (pathname === '/messages') return { messages: directMessages, next_seq: 4, has_more: false };
  if (pathname === '/pool') return { upstreams: [], models: [], usage: { requests: 12, consumers: {} } };
  if (pathname === '/plan') return { plans: [], draft: null };
  if (pathname === '/installers') return installers;
  if (pathname === '/outputs') return { outputs };
  if (pathname === '/user-keys') return { keys: [], pending: null };
  if (pathname === '/assistant/runs') return { runs: [] };
  if (pathname === '/allocation/rounds') return { rounds: [] };
  if (pathname === '/trash') return { projects: [] };
  return null;
}

const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json' };
const server = http.createServer((req, res) => {
  const pathname = decodeURIComponent(new URL(req.url, 'http://127.0.0.1').pathname);
  const file = path.resolve(dist, `.${pathname === '/' ? '/index.html' : pathname}`);
  if (!file.startsWith(`${dist}${path.sep}`) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { 'Content-Type': `${types[path.extname(file)] || 'application/octet-stream'}; charset=utf-8` });
  res.end(fs.readFileSync(file));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
const results = [];
const failures = [];
let screenshots = 0;

async function inspect(page, width, scene, selectors, required) {
  const report = await page.evaluate(({ selectors, required }) => {
    const visibleRect = element => {
      if (!element) return null;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      if (style.display === 'none' || style.visibility === 'hidden' || !rect.width || !rect.height) return null;
      return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
    };
    const boxes = selectors.map(selector => ({ selector, element: document.querySelector(selector), rect: visibleRect(document.querySelector(selector)) })).filter(item => item.rect);
    const overlaps = [];
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j];
        if (a.element.contains(b.element) || b.element.contains(a.element)) continue;
        const x = Math.min(a.rect.right, b.rect.right) - Math.max(a.rect.left, b.rect.left);
        const y = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top);
        if (x > 2 && y > 2) overlaps.push(`${a.selector} / ${b.selector}: ${Math.round(x)}x${Math.round(y)}`);
      }
    }
    const actionFailures = [];
    for (const selector of required) {
      const elements = [...document.querySelectorAll(selector)];
      const element = elements.find(item => visibleRect(item));
      const rect = visibleRect(element);
      if (!rect) {
        actionFailures.push(`${selector}: 不可见`);
        continue;
      }
      if (rect.left < -1 || rect.right > innerWidth + 1 || rect.top < -1 || rect.bottom > innerHeight + 1) {
        actionFailures.push(`${selector}: 超出首屏 ${JSON.stringify(rect)}`);
      }
      if ('scrollWidth' in element && element.scrollWidth > element.clientWidth + 2) {
        actionFailures.push(`${selector}: 内容横向裁切 ${element.scrollWidth}/${element.clientWidth}`);
      }
    }
    const firstTask = visibleRect(document.querySelector('.flat-task'));
    const rail = visibleRect(document.querySelector('.flat-rail'));
    return {
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: innerWidth,
      overlaps,
      actionFailures,
      firstTaskTop: firstTask ? Math.round(firstTask.top) : null,
      railTop: rail ? Math.round(rail.top) : null,
    };
  }, { selectors, required });
  const sceneFailures = [];
  if (report.documentWidth > width + 1) sceneFailures.push(`横向溢出 ${report.documentWidth}/${width}`);
  sceneFailures.push(...report.overlaps.map(item => `区域重叠 ${item}`), ...report.actionFailures);
  if (sceneFailures.length) failures.push({ width, scene, failures: sceneFailures });
  results.push({ width, scene, ...report, failures: sceneFailures });
}

async function capture(page, width, scene, file = `density-${scene}-${width}.png`) {
  await page.screenshot({ path: path.join(output, file), animations: 'disabled' });
  screenshots++;
}

try {
  for (const width of widths) {
    const context = await browser.newContext({ viewport: { width, height }, reducedMotion: 'reduce' });
    const errors = [];
    await context.addInitScript(value => {
      localStorage.setItem('agent-office.projects.v1', JSON.stringify(value));
      localStorage.removeItem('agent-office.active-project.v1');
    }, projects);
    await context.route('**/api/**', route => {
      const url = new URL(route.request().url());
      const pathname = url.pathname.replace('/api/formal', '').replace('/api/harnesses', '/harnesses');
      const task = tasks.find(item => pathname === `/tasks/${item.task_id}`);
      let payload = fixtureFor(pathname);
      if (task) {
        payload = {
          task: { ...task, spec: { goal: '在不改变业务流程的前提下统一页面信息密度，保证关键事实和下一步动作首屏可见。', acceptance: ['主要操作可用', '四档宽度无横向溢出', '1024 保留运维摘要'], exclusive_paths: ['office/src/app-shell.css', 'office/src/flat-office.css'] } },
          events: [
            { seq: 1, event: 'task.claimed', created_at: stamp, actor: member, task_id: task.task_id, payload: { status: 'claimed' } },
            { seq: 2, event: 'task.output', created_at: stamp, actor: member, task_id: task.task_id, payload: { summary: '已完成布局检查' } },
          ],
          diff: { files: ['office/src/app-shell.css'] },
          decisions: [],
        };
      } else if (pathname.endsWith('/output')) {
        payload = { output: { stdout: '密度回归已运行', attempt_id: 'attempt-2' } };
      } else if (pathname === '/allocation/assignment') {
        payload = { assignment: null };
      } else if (pathname === '/harnesses/status') {
        payload = { catalog: [], local_endpoints: [] };
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload || {}) });
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));

    await page.goto(base);
    await page.locator('.project-card-open').first().waitFor();
    await page.waitForFunction(() => !document.querySelector('[data-tools-summary]')?.textContent.includes('正在读取'));
    await inspect(page, width, 'lobby', ['.project-lobby-copy', '.project-lobby-side', '.project-onboarding'], ['[data-new-project]', '[data-open-connections]', '.project-card-open']);
    await capture(page, width, 'lobby');

    await page.locator('.project-card', { hasText: '产品体验升级' }).locator('.project-card-open').click();
    await page.locator('.flat-task').first().waitFor();
    await page.locator('.flat-rail-ledger').waitFor();
    await inspect(page, width, 'tasks', ['.flat-head', '.flat-body', '.flat-rail', '.flat-convs', '.flat-main'], ['#aoStartBtn', '.flat-filters button[aria-pressed="true"]', '.flat-task', '.flat-rail-head', '.flat-rail-ledger']);
    await capture(page, width, 'tasks');

    await inspect(page, width, 'operations', ['.flat-head', '.flat-body', '.flat-rail'], ['.flat-rail-head', '.flat-rail-ledger', '.flat-card-members [data-act="open-members"]']);
    await page.locator('.flat-rail').screenshot({ path: path.join(output, `density-operations-${width}.png`), animations: 'disabled' });
    screenshots++;

    const waitingTask = page.locator('.flat-task-awaiting_human').first();
    await (await waitingTask.count() ? waitingTask : page.locator('.flat-task').first()).click();
    await page.locator('.flat-detail-head h2').waitFor();
    await page.locator('.flat-spec').first().waitFor();
    await inspect(page, width, 'detail', ['.flat-head', '.flat-body', '.flat-rail', '.flat-convs', '.flat-main'], ['.flat-detail-head h2', '.flat-summary', '.flat-detail-actions button', '.flat-spec', '.flat-rail-head']);
    await capture(page, width, 'detail');

    await page.goto(`${base}/outputs.html`);
    await page.locator('.outputs-window').first().waitFor();
    await inspect(page, width, 'outputs', ['.outputs-head', '.outputs-list', '.outputs-window > header', '.outputs-window > .outputs-item'], ['#outputsFilter', '#outputsRefresh', '.outputs-item > header > b', '.outputs-attempt-head', '.outputs-words']);
    await capture(page, width, 'outputs');

    await page.goto(`${base}/direct.html`);
    await page.locator('.direct-msg').last().waitFor();
    await inspect(page, width, 'direct', ['.direct-head', '.direct-list', '.direct-form'], ['.direct-head h1', '.direct-msg:last-child', '#directInput', '#directForm button']);
    await capture(page, width, 'direct');

    if (errors.length) failures.push({ width, scene: 'all', failures: errors });
    await context.close();
  }
  const summary = { label, dist, output, viewports: widths.map(width => `${width}x${height}`), scenes: results.length, screenshots, failedScenes: failures.length, failures };
  console.log(JSON.stringify(summary, null, 2));
  if (!captureOnly) assert.equal(failures.length, 0, `${failures.length} 个界面/视口组合未通过密度回归`);
  console.log(`verify-office-density: ${captureOnly ? 'captured' : 'ok'}`);
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
