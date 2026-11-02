// 转接页的「写一封信」：谁在写、发给谁、写不进去时说什么。
//
// 这一层不连平台（自带静态服务器 + 拦全部 /api/formal/relay/*），验的是界面口径：
//   1. 收件人**按人/窗口分组**，每组标题写明几个；
//   2. 作者写的是**当前这个人**，界面上没有"以哪个窗口的身份发"；
//   3. 发送按钮旁写明**投递是异步的**（对方下次读信箱才看得到）；
//   4. 没选人 / 没写正文时按钮不可按；选好写好之后按一次发一个请求，
//      带的是选中的那几个收件人与正文；
//   5. 平台不认收件人时，把**原因**与**可以写谁**一起显示（门槛 + 回灌），
//      而不是只给一句错误码；
//   6. 成功后说清"已写进发件箱"，并把草稿清掉，免得下一次误发两遍。
//
// 真界面上"点一下真的写进 relay/outbox"那一步由后端的 pytest 与一次真跑覆盖，
// 这里不假装验证了信箱。
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

const OPERATOR = 'office-operator';
const THREAD = 'thr-1a2b3c4d';

const OVERVIEW = {
  running: true,
  heartbeat_at: new Date().toISOString(),
  root: 'relay',
  threads: [
    {
      thread_id: THREAD,
      hops: 2,
      participants: ['codex', 'opencode'],
      updated_at: '2026-11-02T02:00:00+00:00',
      unreadable: '',
    },
  ],
  returned: [],
  pending: 0,
  project: { name: '转接示例', created_at: '2026-11-02T01:00:00Z', updated_at: '2026-11-02T01:00:00Z' },
  project_note: '',
  note: '',
};

const ROSTER = {
  enabled: true,
  author: OPERATOR,
  people: [{ id: OPERATOR, kind: 'human' }],
  windows: [
    { id: 'codex', kind: 'harness' },
    { id: 'opencode', kind: 'harness' },
  ],
};

const serve = async () => {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const file = path.join(dist, url.pathname === '/' ? 'index.html' : url.pathname);
    if (!file.startsWith(dist) || !fs.existsSync(file)) {
      res.writeHead(404);
      res.end('not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
};

const { server, base } = await serve();
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const writes = [];
  // 第一次发送让平台"拒绝"，用来验回灌；之后一律成功。
  let nextReply = null;

  await page.route('**/api/formal/relay/**', (route) => {
    const url = new URL(route.request().url());
    const route_ = url.pathname.replace('/api/formal', '');
    const json = (body, status = 200) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    if (route.request().method() === 'POST' && route_ === '/relay/letters') {
      writes.push(route.request().postDataJSON());
      if (nextReply) {
        const reply = nextReply;
        nextReply = null;
        return json(reply.body, reply.status);
      }
      return json({ letter: '20261102-120000-office-operator.md', author: OPERATOR, recipients: ['codex'] });
    }
    if (route_ === '/relay/overview') return json(OVERVIEW);
    if (route_ === '/relay/roster') return json(ROSTER);
    return json({});
  });

  await page.addInitScript(() => {
    const project = {
      id: 'verify-relay-compose',
      index: '98',
      name: '转接示例',
      createdAt: '2026/11/02 00:00:00',
      phase: 'scene',
      source: 'platform',
      mode: 'relay',
      members: [],
      taskIds: [],
    };
    localStorage.setItem('agent-office.projects.v1', JSON.stringify([project]));
    localStorage.setItem('agent-office.active-project.v1', project.id);
  });

  await page.goto(base);
  await page.waitForSelector('.project-card-open', { timeout: 30000 });
  await page.locator('.project-card-open').first().click();
  await page.waitForSelector('#aoRelay .relay-compose', { timeout: 30000 });

  // 1. 收件人按人/窗口分组，标题写明几个。
  const titles = await page.locator('#aoRelay .relay-pick-title').allInnerTexts();
  assert.equal(titles.length, 2, `应有两组（人 / 窗口），实际：${titles.join(' | ')}`);
  assert.ok(titles[0].includes('人'), `第一组是人，实际：${titles[0]}`);
  assert.ok(titles[1].includes('窗口'), `第二组是窗口，实际：${titles[1]}`);
  assert.ok(titles[0].includes('1 个'), `人那组要说清几个，实际：${titles[0]}`);
  assert.ok(titles[1].includes('2 个'), `窗口那组要说清几个，实际：${titles[1]}`);

  // 2. 作者是当前这个人，且界面上没有"以窗口身份发"的入口。
  const composeText = await page.locator('#aoRelay .relay-compose').innerText();
  assert.ok(composeText.includes(OPERATOR), `要说清这封信的作者是当前这个人：${composeText.slice(0, 120)}`);
  assert.ok(composeText.includes('由平台写死'), '要说清作者行不是随便填的');
  // 收件人是勾选框；身份选择器一个都不该有（单选、下拉、输入框都不行）。
  assert.equal(
    await page.locator('#aoRelay .relay-compose input[type="radio"], #aoRelay .relay-compose select:not([data-reply-to])').count(),
    0,
    '没有"以某个窗口的身份发"这种选择器',
  );

  // 3. 异步提示就在按钮旁边。
  const asyncNote = await page.locator('#aoRelay .relay-async').innerText();
  assert.ok(asyncNote.includes('下次读信箱'), `异步提示要说清什么时候看得到：${asyncNote}`);

  // 4. 空着的时候按钮不可按。
  const send = page.locator('#aoRelay [data-relay-send]');
  assert.equal(await send.isDisabled(), true, '没选人、没写正文时不该能按');

  // 选一个窗口 + 写正文 → 可按。
  await page.locator('#aoRelay input[data-recipient="codex"]').check();
  await page.locator('#aoRelay textarea[data-body]').fill('这件事你来定方向。');
  assert.equal(await send.isEnabled(), true, '选好写好之后要能按');

  // 5. 平台不认收件人：原因与"可以写谁"一起显示（回灌）。
  nextReply = {
    status: 400,
    body: {
      error: {
        code: 'relay.unknown_recipient',
        message: '这些收件人不认识：nobody',
        details: { unknown: ['nobody'], known: ['codex', 'opencode', OPERATOR] },
      },
    },
  };
  await send.click();
  await page.waitForSelector('#aoRelay .relay-feedback-bad', { timeout: 15000 });
  const bad = await page.locator('#aoRelay .relay-feedback-bad').innerText();
  assert.ok(bad.includes('不认识'), `要说出为什么：${bad}`);
  assert.ok(bad.includes('codex') && bad.includes('opencode'), `要把可选的名单给回来：${bad}`);

  // 6. 成功一次：请求体对、界面说清已写进发件箱、草稿清掉。
  await send.click();
  await page.waitForSelector('#aoRelay .relay-feedback-ok', { timeout: 15000 });
  const sent = writes[writes.length - 1];
  assert.deepEqual(sent.recipients, ['codex'], `带的是选中的收件人：${JSON.stringify(sent)}`);
  assert.equal(sent.body, '这件事你来定方向。', '带的是正文原文');
  assert.equal(sent.reply_to, '', '默认是新的一封，不是回信');
  const ok = await page.locator('#aoRelay .relay-feedback-ok').innerText();
  assert.ok(ok.includes('已写进发件箱'), `成功之后要说清写到哪了：${ok}`);
  assert.ok(ok.includes('下次读信箱'), '再说一次投递是异步的，别让人以为对方已经看了');
  assert.equal(await page.locator('#aoRelay textarea[data-body]').inputValue(), '', '发完要把草稿清掉');
  assert.equal(await page.locator('#aoRelay [data-relay-send]').isDisabled(), true, '清空之后按钮回到不可按');

  // 7. 回信：选择框里列出线程，并带上它现在几跳。
  await page.waitForTimeout(500);
  const options = await page.locator('#aoRelay select[data-reply-to] option').allInnerTexts();
  assert.ok(options.some((text) => text.includes('新起一封')), `默认是新的一封：${options.join(' | ')}`);
  assert.ok(
    options.some((text) => text.includes('codex') && text.includes('2 跳')),
    `线程选项要说清现在几跳：${options.join(' | ')}`,
  );

  assert.deepEqual(errors, [], `页面不该报错：${errors.join(' | ')}`);
  console.log('relay compose: ok');
} finally {
  await browser.close();
  server.close();
}
