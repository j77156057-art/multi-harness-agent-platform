import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.resolve(here, '../dist');
const admin = 'agent-window';
const stamp = '2026-11-03T12:00:00Z';
const own = [
  { request_id: 'PCR-' + '1'.repeat(32), project_id: 'P-pending', project_name: '待批项目', status: 'pending', purpose: '待批用途', scope: '待批范围', expected_output: '待批产出', decision_reason: null, created_at: stamp },
  { request_id: 'PCR-' + '2'.repeat(32), project_id: 'P-approved', project_name: '已批项目', group_id: 'G-approved', status: 'approved', purpose: '已批用途', scope: '已批范围', expected_output: '已批产出', decision_reason: '同意创建', created_at: stamp },
  { request_id: 'PCR-' + '3'.repeat(32), project_id: 'P-rejected', project_name: '已拒项目', status: 'rejected', purpose: '已拒用途', scope: '已拒范围', expected_output: '已拒产出', decision_reason: '范围过大', created_at: stamp },
];
let rows = own.map(row => ({ ...row, requested_by_type: 'harness', requested_by_id: admin }));
let failWrite = true;
let failRead = false;
const writes = [];
const project = { id: 'agent-apply', index: '01', name: 'Agent 申请项目', createdAt: stamp, phase: 'scene', source: 'platform', members: [admin], taskIds: [], platformGroupId: 'G-agent' };
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
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
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(value => {
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([value]));
    localStorage.setItem('agent-office.active-project.v1', value.id);
  }, project);
  await page.route('**/api/**', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname.replace('/api/formal', '');
    const json = (status, payload) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(payload) });
    if (pathname === '/agent/project-creation-requests' && request.method() === 'POST') {
      const body = request.postDataJSON();
      writes.push(body);
      if (failWrite) return json(500, { error: '模拟写入失败' });
      const row = {
        request_id: `PCR-${String(writes.length).repeat(32).slice(0, 32)}`,
        ...body, requested_by_type: 'harness', requested_by_id: admin,
        status: 'pending', decision_reason: null, created_at: stamp,
      };
      rows = [row, ...rows];
      return json(200, { request: row });
    }
    if (pathname === '/agent/project-creation-requests') {
      if (failRead) return json(503, { error: '模拟读取失败' });
      return json(200, { requests: rows });
    }
    if (pathname === '/status') {
      return json(200, {
        configured: true, connected: true, project_id: 'agent-apply', group_id: 'G-agent', operator: 'office-operator', operator_type: 'human',
        admin: { configured: true, identity: admin }, applicant: { configured: true, identity: admin }, project_members: [admin],
        harnesses: [{ harness_id: admin, name: '申请 Agent', provider: 'qoder', status: 'online', registration_version: 'r1', credential_count: 1, capabilities: ['coding'], running_tasks: 0 }],
        base_adapters: [],
      });
    }
    if (pathname === '/availability') return json(200, { availability: [{ harness_id: admin, status: 'online', adapter: 'cli', model: 'Auto', usable: true, reasons: [], warnings: [] }] });
    if (pathname === '/tasks') return json(200, { tasks: [] });
    if (pathname === '/events') return json(200, { events: [], next_seq: 0, has_more: false });
    if (pathname === '/messages') return json(200, { messages: [], next_seq: 0, has_more: false });
    if (pathname === '/pool') return json(200, { upstreams: [], models: [], usage: { requests: 0, consumers: {} } });
    if (pathname === '/plan') return json(200, { plans: [], draft: null });
    return json(200, {});
  });
  await page.goto(base);
  await page.locator('.project-card-open').click();
  await page.locator('.flat-conv[data-harness="agent-window"]').click();
  await page.locator('[data-agent-applications]').waitFor();
  const history = page.locator('.flat-agent-application-list');
  await history.waitFor();
  assert.match(await history.innerText(), /待批准/);
  assert.match(await history.innerText(), /已批准/);
  assert.match(await history.innerText(), /已拒绝/);
  assert.match(await history.innerText(), /范围过大/);
  assert.match(await history.innerText(), /G-approved/);
  assert.equal(await page.locator('[data-agent-applications] [data-act="open-approve-request"]').count(), 0);
  assert.equal(await page.locator('[data-agent-applications] [data-act="open-reject-request"]').count(), 0);

  const values = {
    project_id: 'P-agent-new', project_name: 'Agent 新项目', group_id: 'G-agent-new', group_name: 'Agent 新主群',
    purpose: '验证 Agent 自己发起申请', scope: '只验证申请，不自行批准', expected_output: '进入人工待批队列',
  };
  for (const [name, value] of Object.entries(values)) await page.locator(`[data-agent-field="${name}"]`).fill(value);
  await page.locator('[data-act="submit-agent-application"]').click();
  await page.locator('[data-agent-application-error]').getByText('模拟写入失败').waitFor();
  for (const [name, value] of Object.entries(values)) {
    assert.equal(await page.locator(`[data-agent-field="${name}"]`).inputValue(), value, `失败后保留 ${name}`);
  }
  assert.equal(writes.length, 1);

  failWrite = false;
  await page.locator('[data-act="submit-agent-application"]').click();
  await history.locator('text=Agent 新项目').waitFor();
  assert.equal(writes.length, 2);
  assert.equal(writes[1].requested_by, undefined, '浏览器不能指定申请身份');
  assert.match(await history.innerText(), /验证 Agent 自己发起申请/);
  for (const name of Object.keys(values)) assert.equal(await page.locator(`[data-agent-field="${name}"]`).inputValue(), '');

  // 提交已成功但刷新失败：旧历史和当前输入都保留；重试成功后才清错误。
  const second = { ...values, project_id: 'P-agent-second', project_name: '第二次申请', group_id: 'G-agent-second' };
  for (const [name, value] of Object.entries(second)) await page.locator(`[data-agent-field="${name}"]`).fill(value);
  failRead = true;
  await page.locator('[data-act="submit-agent-application"]').click();
  await page.locator('[data-agent-application-error]').getByText('申请已提交，但列表刷新失败').waitFor();
  assert.equal(await page.locator('[data-agent-field="project_name"]').inputValue(), '第二次申请');
  assert.ok(await history.locator('text=Agent 新项目').count(), '刷新失败仍保留旧历史');
  failRead = false;
  await page.locator('[data-act="retry-agent-applications"]').click();
  await history.locator('text=第二次申请').waitFor();
  assert.equal(await page.locator('[data-agent-application-error]').getByText('模拟读取失败').count(), 0);

  for (const width of [1600, 1440, 1280, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, `${width}px 横向溢出`);
  }
  assert.deepEqual(errors, [], '浏览器脚本错误');
  console.log(JSON.stringify({ ok: true, writes: writes.length, statuses: ['pending', 'approved', 'rejected'], widths: [1600, 1440, 1280, 1024] }, null, 2));
  console.log('verify-office-agent-applications: ok');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
