// 人话从平台来：失败原因、事件名、路径都是平台翻的，机器那一份留在 title 里。
//
// 断言：
//   1. 办公室会**去问平台**（POST /wording/translate，请求体里是原文）；
//   2. 界面上显示的是**平台给的人话**，不是原文；
//   3. 机器那一份没丢：事件行的 title 里还有原文与事件编号；
//   4. 平台够不着时（接口 500）界面照常显示，不白屏、不报错 —— 本地兜底。
//
// 自带静态服务器 + 拦截全部 /api/formal/*：不连平台。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const dist = path.resolve(here, '../dist');
assert.ok(fs.existsSync(path.join(dist, 'index.html')), '先构建：npm run build');

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
};

const REASON = 'harness CLI exited 1';
const REASON_TEXT = '窗口的命令退出了（退出码 1）';

const TASKS = [
  {
    task_id: 'FD-8665828938c488fd',
    title: '只读观察回归 · 一件事',
    group_id: 'g',
    status: 'abandoned',
    owner: null,
    attempt_no: 1,
    max_attempts: 3,
    updated_at: '2026-10-18T20:20:08Z',
    last_failure: { reason: REASON },
    state_changed_seq: 9,
  },
];

const EVENTS = [
  {
    seq: 413,
    event_name: 'submit_result',
    actor_type: 'harness',
    actor_id: 'codex-window-admin',
    created_at: '2026-10-18T20:20:08Z',
    payload: { task_id: 'FD-8665828938c488fd' },
  },
];

const STATUS = {
  configured: true,
  connected: true,
  project_id: '112',
  group_id: 'g',
  operator: 'office-operator',
  admin: { configured: true, identity: 'codex-window-admin', error: null },
  project_members: [],
  harnesses: [
    { harness_id: 'codex-window-admin', name: 'Codex 窗口管理员', status: 'online', capabilities: ['coding'], credential_count: 1 },
  ],
  base_adapters: [],
};

// 平台那边的翻译表（真平台的规则一样）。
const TABLE = {
  [REASON]: REASON_TEXT,
  submit_result: '已提交',
};

const start = async (options = {}) => {
  const asked = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname.startsWith('/api/formal')) {
      const route = url.pathname.replace('/api/formal', '');
      const json = (payload) => {
        res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(payload));
      };
      if (route === '/status') return json(STATUS);
      if (route === '/tasks') return json({ tasks: TASKS });
      if (/^\/tasks\/FD-[0-9a-f]{16}$/.test(route)) {
        return json({ task: { ...TASKS[0], spec: {} }, events: EVENTS, diff: { files: [] } });
      }
      if (route === '/events') return json({ events: EVENTS, next_seq: 0, has_more: false });
      if (route === '/messages') return json({ messages: [] });
      if (route === '/availability') return json({ availability: [] });
      if (route === '/pool') return json({ upstreams: [], models: [], usage: { requests: 0 } });
      if (route === '/plan') return json({ plans: [], draft: null });
      if (route === '/user-keys') return json({ keys: [], pending: null });
      if (route === '/wording/translate' && req.method === 'POST') {
        let raw = '';
        req.on('data', (chunk) => (raw += chunk));
        req.on('end', () => {
          if (options.wordingBroken) {
            res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
            res.end(JSON.stringify({ error: 'wording_unavailable' }));
            return;
          }
          const body = JSON.parse(raw || '{}');
          asked.push(...(body.texts || []));
          json({
            texts: (body.texts || []).map((text) => ({
              text: TABLE[text] || text,
              original: text,
              kind: 'auto',
              translated: Boolean(TABLE[text]),
            })),
          });
        });
        return undefined;
      }
      return json({});
    }
    let file = path.join(dist, decodeURIComponent(url.pathname));
    if (url.pathname === '/' || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      file = path.join(dist, 'index.html');
    }
    const body = fs.readFileSync(file);
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, asked, base: `http://127.0.0.1:${server.address().port}` };
};

const browser = await chromium.launch({ headless: true });
try {
  // 1) 平台能翻：界面显示人话，机器那一份在 title 里。
  {
    const { server, asked, base } = await start();
    try {
      const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto(base);
      // 注入一个已经在进行中的项目（不建任务、不发消息）。
      const project = {
        id: 'project-wording-regression',
        index: '99',
        name: '只读观察回归',
        createdAt: '2026/10/22 00:00:00',
        phase: 'scene',
        source: 'platform',
        members: [],
        taskIds: [],
      };
      await page.evaluate(([key, activeKey, value]) => {
        localStorage.setItem(key, JSON.stringify([value]));
        localStorage.setItem(activeKey, value.id);
      }, ['agent-office.projects.v1', 'agent-office.active-project.v1', project]);
      await page.reload();
      // 打开那个项目（办公室是点开项目才mount的）。
      const card = page.locator('.project-card', { hasText: '只读观察回归' }).first();
      await card.waitFor({ state: 'visible', timeout: 20000 });
      await card.locator('.project-card-open').click();
      await page.waitForSelector('.flat-task', { timeout: 20000 });
      await page.waitForFunction(
        () => document.body.innerText.includes('窗口的命令退出了'),
        undefined,
        { timeout: 20000 },
      );

      const taskText = await page.locator('.flat-task').first().innerText();
      assert.match(taskText, /窗口的命令退出了（退出码 1）/, '失败原因显示的是人话');
      assert.doesNotMatch(taskText, /harness CLI exited 1/, '界面上不该再印机器原因');

      assert.ok(asked.includes(REASON), '办公室应该去问平台要翻译');

      // 事件行：人话 + 机器那一份在 title 里。
      await page.locator('.flat-task').first().click();
      await page.waitForSelector('.flat-event', { timeout: 20000 });
      const summary = page.locator('.flat-event summary').first();
      assert.match(await summary.innerText(), /已提交/, '事件名显示的是人话');
      const title = await summary.getAttribute('title');
      assert.match(title || '', /submit_result/, '机器那一份没丢（在 title 里）');
      assert.match(title || '', /413/, '事件编号也在 title 里');

      assert.deepEqual(errors, [], '页面没有报错');
      await page.close();
    } finally {
      server.close();
    }
  }

  // 2) 平台够不着：界面照常显示（本地兜底），不白屏、不报错。
  {
    const { server, base } = await start({ wordingBroken: true });
    try {
      const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto(base);
      await page.evaluate(([key, activeKey, value]) => {
        localStorage.setItem(key, JSON.stringify([value]));
        localStorage.setItem(activeKey, value.id);
      }, [
        'agent-office.projects.v1',
        'agent-office.active-project.v1',
        {
          id: 'project-wording-regression',
          index: '99',
          name: '只读观察回归',
          createdAt: '2026/10/22 00:00:00',
          phase: 'scene',
          source: 'platform',
          members: [],
          taskIds: [],
        },
      ]);
      await page.reload();
      const card = page.locator('.project-card', { hasText: '只读观察回归' }).first();
      await card.waitFor({ state: 'visible', timeout: 20000 });
      await card.locator('.project-card-open').click();
      await page.waitForSelector('.flat-task', { timeout: 20000 });
      await page.waitForTimeout(1500);
      assert.ok(await page.locator('.flat-task').count(), '平台翻不了也要有内容');
      assert.deepEqual(errors, [], '平台翻不了也不能报错');
      await page.close();
    } finally {
      server.close();
    }
  }

  console.log('verify-office-wording: ok');
} finally {
  await browser.close();
}
