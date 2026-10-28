import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

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
const fixtures = {
  '/status': { configured: true, connected: true, project_id: 'design', group_id: 'design-group', operator: 'office-operator', admin: { configured: true, identity: admin }, project_members: [member], harnesses, base_adapters: [] },
  '/tasks': { tasks },
  '/availability': { availability: harnesses.map(item => ({ ...item, adapter: 'cli', model: '模型由窗口提供', model_source: 'cfg', usable: true, warnings: [], reasons: [], last_task_id: null })) },
  '/events': { events: [], next_seq: 0, has_more: false },
  '/messages': { messages: [{ message_id: 'm-1', created_seq: 1, sender_type: 'human', sender_id: 'office-operator', body: '优先统一核心页面体验，并保留现有协作流程。', refs: [], mentions: [], created_at: stamp }], next_seq: 1, has_more: false },
  '/pool': { upstreams: [], models: [], usage: { requests: 0, consumers: {} } },
  '/plan': { plans: [], draft: null },
};

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
try {
  for (const width of [1600, 1280, 900, 768, 375]) {
    const context = await browser.newContext({ viewport: { width, height: 1000 }, reducedMotion: 'reduce' });
    const errors = [];
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await context.route('**/api/**', route => {
      const pathname = new URL(route.request().url()).pathname.replace('/api/formal', '');
      const task = tasks.find(item => pathname === `/tasks/${item.task_id}`);
      const payload = task ? { task: { ...task, spec: { goal: '在不改变业务流程的前提下，统一页面层级、间距与交互状态。', acceptance: ['主要操作可用', '窄屏无横向溢出'] } } } : pathname === '/api/harnesses/status' ? { catalog: [], local_endpoints: [] } : fixtures[pathname] ?? {};
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
    });
    await page.goto(base);
    await page.locator('.project-empty').waitFor();
    const check = async name => {
      const report = await page.evaluate(contrastReport);
      assert.ok(report.checked > 5, `${name}: must measure actual rendered text`);
      assert.deepEqual(report.failures, [], `${width}px ${name}: contrast`);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      assert.equal(overflow, false, `${width}px ${name}: page overflow`);
      results.push({ width, name, ...report });
    };
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
    await context.close();
  }
  console.log(JSON.stringify({ results, screenshots: output }, null, 2));
  console.log('verify-office-design: ok');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
