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
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'office-member-applications-'));
const backendPort = 8878;
const officePort = 4178;
const backendUrl = `http://127.0.0.1:${backendPort}`;
const officeUrl = `http://127.0.0.1:${officePort}`;
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
service.create_project(project_id='P-source', name='成员申请来源项目')
service.create_group(project_id='P-source', group_id='G-source', name='成员申请来源群')
registry = HarnessRegistry(db)
for member in ('member-one', 'member-two'):
    registry.register(HarnessRegistration(
        harness_id=member, name=member, provider='qoder', adapter='qoder',
        adapter_version='1', capabilities=('implementation',), registration_version='r1',
    ))
tokens = TokenService(db)
print(json.dumps({
    'human': tokens.issue(subject_type='human', subject_id='office-operator'),
    'member-one': tokens.issue(subject_type='harness', subject_id='member-one'),
    'member-two': tokens.issue(subject_type='harness', subject_id='member-two'),
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
const setup = spawnSync('python', ['-c', setupSource], { cwd: path.join(root, 'backend'), env, encoding: 'utf8' });
if (setup.status !== 0) throw new Error(`SQLite 初始化失败：${setup.stderr || setup.stdout}`);
const credentials = JSON.parse(setup.stdout.trim().split(/\r?\n/).at(-1));
const memberTokenFile = path.join(temp, 'member-one.key');
fs.writeFileSync(memberTokenFile, credentials['member-one']);

const backend = spawn('python', ['-m', 'app.main'], {
  cwd: path.join(root, 'backend'), env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
});
let backendErrors = '';
backend.stderr.on('data', chunk => { backendErrors = `${backendErrors}${chunk}`.slice(-4000); });
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
const gateway = createFormalGateway({
  upstream: backendUrl,
  tokenFile: memberTokenFile.replace('member-one.key', 'operator.key'),
  memberTokenFile,
  memberId: 'member-one',
  project: 'P-source',
  group: 'G-source',
});
// operator.tokenFile 也需要独立的人类凭据；刚才只写成员文件，避免覆盖。
fs.writeFileSync(path.join(temp, 'operator.key'), credentials.human);
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
    try { if ((await fetch(`${backendUrl}/health`)).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`后端未就绪：${backendErrors}`);
}

async function api(pathname, { token = credentials.human, method = 'GET', body } = {}) {
  const response = await fetch(`${backendUrl}/api/v1${pathname}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(`${method} ${pathname}: ${response.status} ${JSON.stringify(data)}`);
  return data;
}

function application(projectId, groupId, member) {
  return {
    project_id: projectId, project_name: projectId,
    group_id: groupId, group_name: groupId,
    purpose: `${member} 的隔离申请`, scope: '只验证本成员可见',
    expected_output: '只返回自己的状态', requested_kind: 'harness', requested_by: member,
  };
}

let browser;
try {
  await waitForHealth();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(officePort, '127.0.0.1', resolve);
  });
  const other = await api('/groups/G-source/project-creation-requests', {
    token: credentials['member-two'], method: 'POST',
    body: application('P-member-two', 'G-member-two', 'member-two'),
  });
  const otherId = other.request.request_id;

  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const project = {
      id: 'member-source', index: '01', name: '成员申请来源项目', createdAt: '2026/11/03 12:00:00',
      phase: 'scene', source: 'platform', members: ['member-one', 'member-two'], taskIds: [], platformGroupId: 'G-source',
    };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });
  await page.goto(officeUrl);
  await page.locator('.project-card-open').first().click();
  await page.locator('.flat-conv[data-harness="member-one"]').click();
  await page.locator('[data-agent-applications]').waitFor();
  assert.equal(await page.locator(`[data-agent-request-id="${otherId}"]`).count(), 0, 'member-one 看不到 member-two 的申请');
  assert.equal(await page.locator('[data-agent-applications] [data-act="open-approve-request"]').count(), 0);
  assert.equal(await page.locator('[data-agent-applications] [data-act="open-reject-request"]').count(), 0);

  const values = {
    project_id: 'P-member-one', project_name: '成员一项目', group_id: 'G-member-one', group_name: '成员一主群',
    purpose: '验证成员一自己的申请身份', scope: '只允许成员一查看', expected_output: '进入人工待批队列',
  };
  for (const [name, value] of Object.entries(values)) await page.locator(`[data-agent-field="${name}"]`).fill(value);
  await page.locator('[data-act="submit-agent-application"]').click();
  try {
    await page.locator('[data-agent-request-id]').filter({ hasText: '成员一项目' }).waitFor({ timeout: 8000 });
  } catch (error) {
    console.error(await page.locator('[data-agent-applications]').innerText());
    console.error(JSON.stringify((await api('/project-creation-requests')).requests));
    throw error;
  }

  const rows = (await api('/project-creation-requests')).requests;
  const created = rows.find(row => row.project_id === 'P-member-one');
  assert.ok(created, '成员申请真实落库');
  assert.equal(created.requested_by_type, 'harness');
  assert.equal(created.requested_by_id, 'member-one');
  assert.equal(created.status, 'pending');
  assert.equal(await page.locator(`[data-agent-request-id="${created.request_id}"]`).count(), 1);

  for (const width of [1600, 1440, 1280, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, `${width}px 横向溢出`);
  }
  assert.deepEqual(errors, [], '浏览器脚本错误');
  console.log(JSON.stringify({
    ok: true,
    applicant: created.requested_by_id,
    status: created.status,
    other_member_visible: false,
    widths: [1600, 1440, 1280, 1024],
    database: path.join(temp, 'state.db'),
  }, null, 2));
} finally {
  if (browser) await browser.close();
  await new Promise(resolve => server.close(resolve));
  backend.kill();
}
