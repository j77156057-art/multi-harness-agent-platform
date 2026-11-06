import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { createFormalGateway } from '../server/formal-dispatch-gateway.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const office = path.resolve(here, '..');
const dist = path.join(office, 'dist');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'office-project-requests-live-'));
const backendPort = 8877;
const officePort = 4177;
const backendUrl = `http://127.0.0.1:${backendPort}`;
const officeUrl = `http://127.0.0.1:${officePort}`;
const output = path.join(root, 'artifacts', 'project-request-approval');
fs.mkdirSync(output, { recursive: true });
assert.ok(fs.existsSync(path.join(dist, 'index.html')), '先运行 npm run build');

const setupSource = String.raw`
import json
from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.auth.tokens import TokenService
from app.db.connection import Database
from app.tasks.service import SchedulerService

db = Database()
db.initialize()
service = SchedulerService(db)
service.create_project(project_id='P-source', name='真实审批来源项目')
service.create_group(project_id='P-source', group_id='G-source', name='真实审批来源群')
HarnessRegistry(db).register(HarnessRegistration(
    harness_id='agent-window', name='申请 Agent', provider='qoder', adapter='qoder',
    adapter_version='1', capabilities=('implementation',), registration_version='r1',
))
tokens = TokenService(db)
print(json.dumps({
    'human': tokens.issue(subject_type='human', subject_id='office-operator'),
    'agent': tokens.issue(subject_type='harness', subject_id='agent-window'),
}))
`;
const env = {
  ...process.env,
  AP_DATA_DIR: temp,
  AP_PORT: String(backendPort),
  AP_AUTH_MODE: 'enforce',
  AP_CHECK_SPEC: '0',
  AP_ADMIN_SUBJECTS: '',
};
const setup = spawnSync('python', ['-c', setupSource], {
  cwd: path.join(root, 'backend'), env, encoding: 'utf8',
});
if (setup.status !== 0) throw new Error(`真实 SQLite 初始化失败：${setup.stderr || setup.stdout}`);
const credentials = JSON.parse(setup.stdout.trim().split(/\r?\n/).at(-1));
const tokenFile = path.join(temp, 'operator.key');
fs.writeFileSync(tokenFile, credentials.human);

const backend = spawn('python', ['-m', 'app.main'], {
  cwd: path.join(root, 'backend'), env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
});
let backendErrors = '';
backend.stderr.on('data', chunk => { backendErrors = `${backendErrors}${chunk}`.slice(-4000); });

const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
const gateway = createFormalGateway({
  upstream: backendUrl,
  tokenFile,
  project: 'P-source',
  group: 'G-source',
});
const server = http.createServer(async (req, res) => {
  if (await gateway.handle(req, res)) return;
  const pathname = decodeURIComponent(new URL(req.url, officeUrl).pathname);
  const file = path.resolve(dist, `.${pathname === '/' ? '/index.html' : pathname}`);
  if (!file.startsWith(`${dist}${path.sep}`) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404).end();
    return;
  }
  res.writeHead(200, { 'Content-Type': `${types[path.extname(file)] || 'application/octet-stream'}; charset=utf-8` });
  res.end(fs.readFileSync(file));
});

async function waitForHealth() {
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const response = await fetch(`${backendUrl}/health`);
      if (response.ok) return;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`后端未就绪：${backendErrors}`);
}

async function api(pathname, { token = credentials.human, method = 'GET', body } = {}) {
  const response = await fetch(`${backendUrl}/api/v1${pathname}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(`${method} ${pathname}: ${response.status} ${JSON.stringify(data)}`);
  return data;
}

function application(projectId, groupId, purpose) {
  return {
    project_id: projectId,
    project_name: projectId === 'P-live-approved' ? '真实批准项目' : '真实拒绝项目',
    group_id: groupId,
    group_name: projectId === 'P-live-approved' ? '真实批准主群' : '真实拒绝主群',
    purpose,
    scope: '只验证办公室审批，不修改既有项目',
    expected_output: '由真人决定是否在真实 SQLite 创建项目和主群',
    requested_kind: 'harness',
    requested_by: 'agent-window',
  };
}

let browser;
try {
  await waitForHealth();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(officePort, '127.0.0.1', resolve);
  });
  const approvedApplication = await api('/groups/G-source/project-creation-requests', {
    token: credentials.agent,
    method: 'POST',
    body: application('P-live-approved', 'G-live-approved', '验证真人批准后真实创建'),
  });
  const approvedId = approvedApplication.request.request_id;
  const rejectedApplication = await api('/groups/G-source/project-creation-requests', {
    token: credentials.agent,
    method: 'POST',
    body: application('P-live-rejected', 'G-live-rejected', '验证真人拒绝后不创建'),
  });
  const rejectedId = rejectedApplication.request.request_id;

  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const project = {
      id: 'live-source', index: '01', name: '真实审批来源项目', createdAt: '2026/11/03 12:00:00',
      phase: 'scene', source: 'platform', members: [], taskIds: [], platformGroupId: 'G-source',
    };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });
  await page.goto(officeUrl);
  await page.locator('.project-card-open').first().click();
  await page.locator('.flat-conv', { hasText: '需要你处理' }).click();
  const approvedBlock = page.locator(`[data-request-id="${approvedId}"]`);
  const rejectedBlock = page.locator(`[data-request-id="${rejectedId}"]`);
  await approvedBlock.waitFor();
  await rejectedBlock.waitFor();

  // 真人先把原参数改坏：真实 400 必须留在弹窗且保留输入，再改成最终参数批准。
  await approvedBlock.locator('[data-act="open-approve-request"]').click();
  const dialog = page.locator('#flatProjectRequestDecision');
  await dialog.screenshot({ path: path.join(output, 'revision-before.png'), animations: 'disabled' });
  await page.locator('[data-request-confirm]').check();
  await page.locator('[data-approve-reason]').fill('按实际交付边界修订项目与主群标识');
  await dialog.locator('[data-confirm-field="project_id"]').fill('P invalid id');
  await page.locator('[data-act="confirm-approve-request"]').click();
  await page.waitForFunction(() => document.querySelector('[data-project-request-dialog-error]')?.textContent.includes('invalid project id'));
  assert.equal(await dialog.locator('[data-confirm-field="project_id"]').inputValue(), 'P invalid id', '校验失败保留修订输入');
  assert.equal(await dialog.locator('[data-approve-reason]').inputValue(), '按实际交付边界修订项目与主群标识', '校验失败保留修订理由');
  for (const [field, value] of Object.entries({
    project_id: 'P-live-revised-approved',
    project_name: '真实修订批准项目',
    group_id: 'G-live-revised-approved',
    group_name: '真实修订批准主群',
  })) {
    await dialog.locator(`[data-confirm-field="${field}"]`).fill(value);
  }
  await page.locator('[data-act="confirm-approve-request"]').click();
  await approvedBlock.locator('text=已批准').waitFor();
  assert.match(await approvedBlock.innerText(), /原申请参数[\s\S]*P-live-approved[\s\S]*最终参数[\s\S]*P-live-revised-approved/);
  await approvedBlock.screenshot({ path: path.join(output, 'revision-approved.png'), animations: 'disabled' });

  const projectsAfterApproval = await api('/projects?include_trashed=1');
  assert.ok(projectsAfterApproval.projects.some(row => row.project_id === 'P-live-revised-approved' && row.name === '真实修订批准项目'), '批准后按最终参数创建项目');
  assert.ok(!projectsAfterApproval.projects.some(row => row.project_id === 'P-live-approved'), '原项目 ID 未创建');
  const groupsAfterApproval = await api('/groups?project_id=P-live-revised-approved');
  assert.ok(groupsAfterApproval.groups.some(row => row.group_id === 'G-live-revised-approved' && row.name === '真实修订批准主群'), '批准后按最终参数创建主群');

  // 修订后拒绝仍保存原值/最终值，但两个 ID 都不能创建资源。
  await rejectedBlock.locator('[data-act="open-reject-request"]').click();
  for (const [field, value] of Object.entries({
    project_id: 'P-live-revised-rejected',
    project_name: '真实修订拒绝项目',
    group_id: 'G-live-revised-rejected',
    group_name: '真实修订拒绝主群',
  })) {
    await dialog.locator(`[data-confirm-field="${field}"]`).fill(value);
  }
  await page.locator('[data-reject-reason]').fill('修订参数仅用于审计；本次拒绝，不创建任何资源');
  await page.locator('[data-act="confirm-reject-request"]').click();
  await rejectedBlock.locator('text=已拒绝').waitFor();
  assert.match(await rejectedBlock.innerText(), /原申请参数[\s\S]*P-live-rejected[\s\S]*最终参数[\s\S]*P-live-revised-rejected/);
  await rejectedBlock.screenshot({ path: path.join(output, 'revision-rejected.png'), animations: 'disabled' });

  const projectsAfterRejection = await api('/projects?include_trashed=1');
  for (const projectId of ['P-live-rejected', 'P-live-revised-rejected']) {
    assert.ok(!projectsAfterRejection.projects.some(row => row.project_id === projectId), `拒绝后项目不存在：${projectId}`);
  }
  const allGroups = await api('/groups');
  for (const groupId of ['G-live-rejected', 'G-live-revised-rejected']) {
    assert.ok(!allGroups.groups.some(row => row.group_id === groupId), `拒绝后主群不存在：${groupId}`);
  }
  const requestList = await api('/project-creation-requests', { token: credentials.agent });
  for (const [requestId, originalId, finalId] of [
    [approvedId, 'P-live-approved', 'P-live-revised-approved'],
    [rejectedId, 'P-live-rejected', 'P-live-revised-rejected'],
  ]) {
    const row = requestList.requests.find(item => item.request_id === requestId);
    assert.equal(row.original.project_id, originalId, '申请人可读取原申请参数');
    assert.equal(row.project_id, finalId, '申请人可读取最终参数');
    assert.equal(row.revision.project_id, finalId, '修订审计保存最终参数');
    assert.equal(row.revision.revised_by_id, 'office-operator', '修订审计保存修订人');
    assert.ok(row.revision.reason, '修订审计保存理由');
  }

  const layouts = [];
  for (const width of [1600, 1440, 1280, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    const report = await page.evaluate(() => {
      const overflow = document.documentElement.scrollWidth > innerWidth + 1;
      const selectors = ['.flat-head', '.flat-body', '.flat-rail', '.flat-main'];
      const boxes = selectors.map(selector => {
        const element = document.querySelector(selector);
        if (!element) return null;
        const rect = element.getBoundingClientRect();
        return { selector, element, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
      }).filter(Boolean);
      const overlaps = [];
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i], b = boxes[j];
          if (a.element.contains(b.element) || b.element.contains(a.element)) continue;
          const x = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (x > 2 && y > 2) overlaps.push(`${a.selector}/${b.selector}`);
        }
      }
      return { overflow, overlaps };
    });
    assert.equal(report.overflow, false, `${width}px 横向溢出`);
    assert.deepEqual(report.overlaps, [], `${width}px 区域重叠`);
    layouts.push({ width, ...report });
  }
  assert.deepEqual(errors, [], '浏览器脚本错误');
  console.log(JSON.stringify({
    ok: true,
    database: path.join(temp, 'state.db'),
    approved: { request_id: approvedId, original_project_id: 'P-live-approved', project_id: 'P-live-revised-approved', group_id: 'G-live-revised-approved' },
    rejected: { request_id: rejectedId, original_project_id: 'P-live-rejected', final_project_id: 'P-live-revised-rejected', project_id: null, group_id: null },
    screenshots: output,
    layouts,
  }, null, 2));
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
  backend.kill();
}
