// Opening-entry regression for the self-loop.
//
// The owner creates a project, states one goal, and picks the administrator +
// members. Confirming must post one `project-goal` message (the administrator
// worker's input), not N task dispatches. The browser must never author task
// content, exclusive paths or acceptance.
import { chromium } from 'playwright';
import { requireOffice } from './lib/office-url.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';

const base = await requireOffice('verify-opening-host.mjs', process.env.OFFICE_PREVIEW_URL);
const output = 'screenshots/opening-host';
await fs.mkdir(output, { recursive: true });

const ADMIN = 'codex-window-admin';
const MEMBERS = [
  { harness_id: 'member-1', name: '成员一', status: 'online', capabilities: ['coding'], registration_version: 'v1' },
  { harness_id: 'member-2', name: '成员二', status: 'online', capabilities: ['coding'], registration_version: 'v1' },
];
const formalStatus = {
  connected: true,
  project_id: '112',
  group_id: 'office-formal-2',
  admin: { identity: ADMIN },
  harnesses: [
    { harness_id: ADMIN, name: 'Codex 管理员窗', status: 'online', capabilities: ['coding'], registration_version: 'v1' },
    ...MEMBERS,
  ],
  project_members: [],
};

const browser = await chromium.launch({ headless: true });
let failure;
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  const errors = [];
  const httpErrors = [];
  const messages = [];
  const dispatches = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.status() >= 400) httpErrors.push(`${response.status()} ${response.url()}`);
  });
  const json = (route, body, status = 200) => route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/formal/status') return json(route, formalStatus);
    if (url.pathname === '/api/formal/tasks') return json(route, { tasks: [] });
    if (url.pathname === '/api/formal/messages' && route.request().method() === 'POST') {
      messages.push(route.request().postDataJSON());
      return json(route, { message_id: 'm-1', seq: 1 });
    }
    if (url.pathname === '/api/formal/messages') return json(route, { messages: [] });
    if (url.pathname === '/api/formal/dispatch') {
      dispatches.push(route.request().postDataJSON());
      return json(route, { task_id: 'FD-should-not-exist', status: 'ready' });
    }
    return json(route, {});
  });

  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.locator('[data-new-project]').click();
  await page.waitForSelector('.project-create-dialog[open]');
  assert.equal(await page.locator('[data-project-paths]').count(), 0);
  assert.equal(await page.locator('[data-project-acceptance]').count(), 0);
  assert.equal(await page.locator('[data-project-goal]').count(), 0);

  await page.locator('[data-project-name]').fill('自循环入口回归');
  await page.locator('[data-project-source]').selectOption('platform');
  const boxes = page.locator('[data-project-members] input[type="checkbox"]');
  assert.equal(await boxes.count(), MEMBERS.length);
  for (let index = 0; index < MEMBERS.length; index += 1) await boxes.nth(index).check();
  await page.locator('[data-confirm-create]').click();
  await page.locator('#openingDialog[open]').waitFor({ state: 'visible' });

  const dialog = page.locator('#openingDialog');
  assert.equal(await dialog.locator('[data-field="goal"]').count(), 1, 'goal is the one owner input');
  assert.equal(await dialog.locator('[data-field="exclusive_paths"]').count(), 0);
  assert.equal(await dialog.locator('[data-field="acceptance"]').count(), 0);
  assert.equal(await dialog.locator('[data-field="title"]').count(), 0);

  await dialog.locator(`[data-admin="${ADMIN}"]`).click();
  for (const member of MEMBERS) {
    const chip = dialog.locator(`[data-worker="${member.harness_id}"]`);
    const className = (await chip.getAttribute('class')) || '';
    if (!className.split(/\s+/).includes('on')) await chip.click();
  }
  await dialog.locator('[data-field="goal"]').fill('给项目补一份启动说明，由管理员决定任务拆分。');
  await dialog.locator('[data-field="rounds"]').fill('1');
  await dialog.locator('[data-action="to-summary"]').click();
  try {
    await dialog.locator('.oop-summary').waitFor({ state: 'visible', timeout: 8000 });
  } catch (error) {
    console.error('summary errors:', await dialog.locator('[data-role="errors"]').textContent().catch(() => ''));
    console.error('opening state:', JSON.stringify(await page.evaluate(() => window.officePreviewState?.opening), null, 2));
    throw error;
  }
  const summaryNote = await dialog.locator('.oop-sum-note').textContent();
  assert.match(summaryNote, /仅提交一条项目目标/);
  assert.doesNotMatch(summaryNote, /每个选中的在线 Harness/);
  await dialog.locator('[data-action="confirm"]').click();
  await page.waitForFunction(() => window.officePreviewState?.opening?.readyCount === 1, {}, { timeout: 30000 });

  assert.equal(dispatches.length, 0, 'UI must not dispatch tasks');
  assert.equal(messages.length, 1, 'UI posts one project-goal message');
  const message = messages[0];
  assert.equal(typeof message.body, 'string');
  assert.match(message.body, /项目目标/);
  assert.ok(Array.isArray(message.refs));
  assert.ok(message.refs.includes('project-goal'));
  const envelopeRef = message.refs.find(ref => String(ref).startsWith('project-goal-json:'));
  assert.ok(envelopeRef, 'machine-readable goal envelope rides in refs');
  const envelope = JSON.parse(String(envelopeRef).slice('project-goal-json:'.length));
  assert.equal(envelope.project_name, '自循环入口回归');
  assert.deepEqual(envelope.members, MEMBERS.map(member => member.harness_id));
  assert.equal(envelope.admin, ADMIN);
  assert.ok(envelope.goal.includes('启动说明'));

  assert.equal(await page.evaluate(() => document.body.dataset.projectPhase), 'scene');
  assert.deepEqual(errors, []);
  assert.deepEqual(httpErrors, []);
  await page.screenshot({ path: `${output}/self-loop-entry.png`, fullPage: true });
  console.log(JSON.stringify({
    result: 'PASS',
    checks: 13,
    dispatchedTasks: dispatches.length,
    goalMessages: messages.length,
    admin: envelope.admin,
    members: envelope.members,
  }));
} catch (error) {
  failure = error.stack || String(error);
  process.exitCode = 1;
  console.error(failure);
} finally {
  await fs.writeFile(
    `${output}/verification.json`,
    JSON.stringify({ result: failure ? 'failed' : 'passed', failure }, null, 2),
  );
  await browser.close();
}
