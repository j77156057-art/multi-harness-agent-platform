// 任务书必须是**活的**：管理员新派的任务，要自己长出来。
//
// 断言：
//   1. 打开任务书后，平台那边多了一条任务，办公室刷新一次，任务书里就有它了
//      （不关对话框、不重开）；
//   2. 没人接的条数也跟着变；
//   3. 刷新不打断阅读：滚动位置不被拉回顶部。
import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const BASE = process.env.OFFICE_URL || 'http://127.0.0.1:4174';
const ADMIN = 'codex-window-admin';

const task = (id, title, extra = {}) => ({
  task_id: id, title, group_id: 'g', status: 'ready', owner: null, attempt_no: 0,
  max_attempts: 3, updated_at: '2026-10-22T10:00:00', acceptance: '做完提交。',
  state_changed_seq: 9, ...extra,
});

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));

  let tasks = [task('FD-1', '活任务书 · 第一条')];
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/formal/status') {
      return json({
        configured: true, connected: true, project_id: '112', group_id: 'g', operator: 'operator',
        admin: { configured: true, identity: ADMIN, error: null },
        project_members: [],
        harnesses: [
          { harness_id: ADMIN, name: 'Codex · 管理员', status: 'online', capabilities: ['coding'] },
          { harness_id: 'claude-code', name: 'Claude Code', status: 'online', capabilities: ['coding'] },
        ],
        base_adapters: [],
      });
    }
    if (url.pathname === '/api/formal/tasks') return json({ tasks });
    if (url.pathname === '/api/formal/plan') {
      return json({ draft: { plan_id: 'plan-1', status: 'draft', document: { title: '活任务书', markdown: '# 原文' } }, plans: [] });
    }
    if (url.pathname === '/api/formal/events') return json({ events: [], next_seq: 0, has_more: false });
    if (url.pathname === '/api/formal/messages') return json({ messages: [] });
    if (url.pathname === '/api/formal/availability') return json({ availability: [] });
    return json({});
  });

  await page.goto(BASE);
  await page.locator('[data-new-project]').click();
  await page.waitForSelector('.project-create-dialog[open]');
  await page.locator('[data-project-name]').fill('活任务书');
  await page.locator('[data-confirm-create]').click();
  await page.waitForSelector('#aoWorkspace .flat-task', { timeout: 20000 });
  await page.waitForTimeout(1200);

  await page.locator('[data-act="open-brief"]').click();
  const brief = page.locator('#flatBrief');
  await brief.waitFor({ state: 'visible' });
  const flat = (s) => s.replace(/\s+/g, ' ');
  let text = flat(await brief.innerText());
  assert.match(text, /FD-1/, '任务书里有第一条任务');
  assert.match(text, /另有 1 条任务尚未领取/, '待领取的条数是 1');
  assert.doesNotMatch(text, /FD-9/, '还没有第二条');

  // 平台那边新派了一条任务（标题带项目名 → 落在本项目范围内）。
  tasks = [...tasks, task('FD-9', '活任务书 · 管理员新派的')];
  await page.evaluate(() => window.officePreviewState?.office?.refresh?.());
  await page.waitForTimeout(1200);

  text = flat(await brief.innerText());
  assert.match(text, /FD-9/, '新任务自己长出来了（不用关掉对话框）');
  assert.match(text, /另有 2 条任务尚未领取/, '待领取的条数跟着变');
  assert.ok(await brief.isVisible(), '对话框一直开着');

  // 3. 刷新不把用户拉回顶部
  const before = await brief.evaluate((el) => {
    const list = el.querySelector('.flat-dialog-list');
    list.scrollTop = Math.min(30, Math.max(0, list.scrollHeight - list.clientHeight));
    return Math.round(list.scrollTop);
  });
  await page.evaluate(() => window.officePreviewState?.office?.refresh?.());
  await page.waitForTimeout(1000);
  const scrollTop = await brief.evaluate((el) => Math.round(el.querySelector('.flat-dialog-list').scrollTop));
  assert.equal(scrollTop, before, `刷新后滚动位置被拉回顶部了（${before} → ${scrollTop}）`);

  assert.deepEqual(errors, [], '页面报错');
  console.log('verify-office-brief-live: ok');
} finally {
  await browser.close();
}
