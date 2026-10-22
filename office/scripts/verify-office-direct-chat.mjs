// \u4e0e\u7ba1\u7406\u5458\u7684\u5b9a\u5411\u6d88\u606f\uff08\u79c1\u804a\uff09\u3002
//
// \u5e73\u53f0\u53ea\u6709\u7fa4\u6d88\u606f\u4e00\u79cd\uff0c\u4f46\u5b83\u53ef\u4ee5\u5e26 `direct` \u7b7e\u540d\uff08R10 \u00a719\uff09\uff1a\u8fd9\u6837\u7684\u6d88\u606f\u53ea\u6709
// \u53d1\u9001\u65b9\u4e0e\u6536\u4ef6\u65b9\u770b\u5f97\u5230\uff0c\u5176\u4ed6\u6210\u5458\u8bfb\u4e0d\u5230\u3002
//
// \u65ad\u8a00\uff1a
//   1. \u72ec\u7acb\u79c1\u804a\u9875\u9762\u6253\u5f00\uff0c\u53ea\u5217\u5b9a\u5411\u6d88\u606f\uff08\u7fa4\u6d88\u606f\u4e0d\u6df7\u8fdb\u6765\uff09\uff1b
//   2. \u53d1\u51fa\u53bb\u7684\u6d88\u606f\u5e26 direct \u4e14 mentions \u662f\u7ba1\u7406\u5458\uff08\u5e73\u53f0\u9760\u5b83\u5efa\u6295\u9012\u8bb0\u5f55\uff09\uff1b
//   3. \u529e\u516c\u5ba4\u91cc\u6709\u201c\u5f00\u79c1\u804a\u7a97\u53e3\u201d\u7684\u5165\u53e3\u3002
//
// \u53ea\u8bfb\u811a\u672c\uff1aAPI \u5168\u90e8\u62e6\u622a\uff0c\u4e0d\u5f80\u6b63\u5f0f\u5e73\u53f0\u5199\u4efb\u4f55\u4e1c\u897f\u3002
import { chromium } from 'playwright';
import assert from 'node:assert/strict';

const BASE = process.env.OFFICE_URL || 'http://127.0.0.1:4174';
const ADMIN = 'codex-window-admin';

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
  const errors = [];
  const posted = [];
  page.on('pageerror', (e) => errors.push(e.message));

  const messages = [
    { message_id: 'm1', sender_type: 'human', sender_id: 'operator', body: '\u53ea\u7ed9\u4f60\u770b\u7684\u90a3\u53e5', created_seq: 10, created_at: '2026-10-22T10:00:00', refs: ['direct'] },
    { message_id: 'm2', sender_type: 'harness', sender_id: ADMIN, body: '\u6536\u5230\u4e86\uff0c\u6211\u770b\u4e00\u4e0b', created_seq: 11, created_at: '2026-10-22T10:01:00', refs: ['direct'] },
    { message_id: 'm3', sender_type: 'human', sender_id: 'operator', body: '\u7fa4\u91cc\u7684\u8bdd\uff0c\u4e0d\u5e94\u8be5\u51fa\u73b0\u5728\u8fd9\u91cc', created_seq: 12, created_at: '2026-10-22T10:02:00', refs: [] },
  ];

  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    if (url.pathname === '/api/formal/status') {
      return json({
        configured: true, connected: true, project_id: '112', group_id: 'g', operator: 'operator',
        admin: { configured: true, identity: ADMIN, error: null },
        project_members: [],
        harnesses: [
          { harness_id: ADMIN, name: 'Codex \u00b7 \u7ba1\u7406\u5458', status: 'online', capabilities: ['coding'] },
        ],
        base_adapters: [],
      });
    }
    if (url.pathname === '/api/formal/messages' && route.request().method() === 'POST') {
      posted.push(route.request().postDataJSON());
      return json({ message_id: 'm-new', seq: 20, mentions: [ADMIN], refs: ['direct'] });
    }
    if (url.pathname === '/api/formal/messages') return json({ messages });
    if (url.pathname === '/api/formal/events') return json({ events: [] });
    if (url.pathname === '/api/formal/tasks') return json({ tasks: [] });
    if (url.pathname === '/api/formal/availability') return json({ availability: [] });
    return json({});
  });

  // 1. \u72ec\u7acb\u79c1\u804a\u9875\u9762
  await page.goto(`${BASE}/direct.html`);
  await page.waitForSelector('.direct-shell', { timeout: 20000 });
  await page.waitForTimeout(800);
  const text = await page.locator('.direct-shell').innerText();
  assert.match(text, /Codex \u00b7 \u7ba1\u7406\u5458/, '\u6807\u9898\u91cc\u5199\u7740\u7ba1\u7406\u5458\u662f\u8c01');
  assert.match(text, /\u53ea\u6709\u4f60\u548c\u7ba1\u7406\u5458\u770b\u5f97\u5230/, '\u9875\u9762\u8bf4\u660e\u4e86\u9690\u79c1\u8303\u56f4');
  assert.match(text, /\u53ea\u7ed9\u4f60\u770b\u7684\u90a3\u53e5/, '\u5b9a\u5411\u6d88\u606f\u5728\u5217\u8868\u91cc');
  assert.match(text, /\u6536\u5230\u4e86/, '\u7ba1\u7406\u5458\u7684\u56de\u8bdd\u5728\u5217\u8868\u91cc');
  assert.doesNotMatch(text, /\u7fa4\u91cc\u7684\u8bdd/, '\u666e\u901a\u7fa4\u6d88\u606f\u4e0d\u5f97\u6df7\u8fdb\u6765');

  // 2. \u53d1\u51fa\u53bb\u7684\u6d88\u606f\u5e26 direct + mentions
  await page.locator('#directInput').fill('\u8fd9\u53e5\u53ea\u7ed9\u4f60\u770b');
  await page.locator('#directForm button[type="submit"]').click();
  await page.waitForTimeout(1000);
  assert.equal(posted.length, 1, '\u53d1\u51fa\u4e86\u4e00\u6761');
  assert.equal(posted[0].direct, true, '\u5b9a\u5411\u6d88\u606f\u5e26 direct');
  assert.deepEqual(posted[0].mentions, [ADMIN], '\u6536\u4ef6\u4eba\u662f\u7ba1\u7406\u5458');
  assert.ok(posted[0].refs.includes('direct'), 'refs \u91cc\u5e26 direct');

  // 3. \u529e\u516c\u5ba4\u91cc\u7684\u5165\u53e3
  await page.addInitScript(() => {
    const project = { id: 'direct-regression', index: '73', name: '回归', createdAt: '2026/10/22 00:00:00', phase: 'scene', source: 'platform', members: [], taskIds: [] };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });
  await page.goto(BASE);
  await page.waitForSelector('.project-card-open', { timeout: 20000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoWorkspace .flat-head', { timeout: 20000 });
  await page.waitForTimeout(1000);
  assert.ok(await page.locator('#aoWorkspace [data-act="open-direct-window"]').count(), '\u529e\u516c\u5ba4\u91cc\u6709\u201c\u5f00\u79c1\u804a\u7a97\u53e3\u201d');

  assert.deepEqual(errors, [], '\u9875\u9762\u62a5\u9519');
  console.log('verify-office-direct-chat: ok');
} finally {
  await browser.close();
}
