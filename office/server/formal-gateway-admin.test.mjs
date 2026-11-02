// The gateway's administrator identity: regression tests for the shapes we
// actually hit while wiring Stage C.
//
//   1. `call()` used to take its credential as a default parameter, so an
//      explicit admin token at the call site could be shadowed by the operator
//      token — or by a file path where a token was expected.
//   2. The two actors must stay separate: `as: 0` is the administrator harness
//      (admin token), `as: 1` is the human operator (operator token).
//   3. `review.cross_provider_required` is a platform rule, not a gateway bug:
//      it must reach the browser verbatim so the panel can explain it.
//   4. Without an admin credential the admin routes refuse; they never quietly
//      borrow the operator's authority.
//
// Run: node --test server/formal-gateway-admin.test.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { createFormalGateway } from './formal-dispatch-gateway.mjs';

const OPERATOR = 'apt_' + '1'.repeat(64);
const ADMIN = 'apt_' + '2'.repeat(64);

const tempFile = (contents) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'formal-gw-'));
  const file = path.join(dir, 'token.key');
  fs.writeFileSync(file, contents);
  return file;
};

// The gateway calls `fetch(urlString, init)`, so the stub must read the path
// from the first argument and the headers from the init object.
const responder = (handlers, adminId = 'office-admin') => async (target, init = {}) => {
  const path = new URL(String(target)).pathname;
  const bearer = init.headers?.Authorization;
  const json = (status, body) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  if (path === '/api/v1/auth/whoami') {
    const admin = bearer === `Bearer ${ADMIN}`;
    return json(200, {
      mode: 'enforce',
      subject: { type: admin ? 'harness' : 'human', id: admin ? adminId : 'office-operator' },
    });
  }
  if (path === '/api/v1/groups') {
    return json(200, { groups: [{ group_id: 'g', project_id: 'p' }] });
  }
  if (path === '/api/v1/tasks/T1') return json(200, { task_id: 'T1', group_id: 'g' });
  const handler = handlers[path];
  if (handler) return handler(bearer, json, init);
  return json(404, { error: { code: 'not_found' } });
};

const exchange = async (gateway, route, body) => {
  let status = null;
  let payload = null;
  const request = {
    url: `/api/formal${route}`,
    method: 'POST',
    socket: { localPort: 4999, remoteAddress: '127.0.0.1' },
    headers: {
      host: '127.0.0.1:4999',
      origin: 'http://127.0.0.1:4999',
      'x-office-formal': '1',
      'content-type': 'application/json',
    },
    async *[Symbol.asyncIterator]() {
      yield Buffer.from(JSON.stringify(body));
    },
  };
  const response = {
    writeHead: (code) => {
      status = code;
    },
    end: (text) => {
      payload = JSON.parse(String(text));
    },
  };
  assert.equal(await gateway.handle(request, response), true);
  return { status, payload };
};

const gatewayFor = (overrides = {}) =>
  createFormalGateway({
    upstream: 'http://127.0.0.1:8848',
    tokenFile: tempFile(OPERATOR),
    project: 'p',
    group: 'g',
    ...overrides,
  });

test('the two credentials never cross routes', async (t) => {
  // The gateway reads the global `fetch` at call time; the child process that
  // `node --test` spawns gets this stub.
  globalThis.fetch = responder({
    '/api/v1/review/claim': (bearer, json) =>
      bearer === `Bearer ${ADMIN}`
        ? json(200, { review_id: 'rv-claim' })
        : json(403, { error: { code: 'auth.permission_denied' } }),
    '/api/v1/human/review/claim': (_bearer, json) => json(200, { review_id: 'rv-human' }),
    '/api/v1/human/review/submit': (_bearer, json) => json(200, { review_id: 'rv-human' }),
    '/api/v1/review/submit': (bearer, json) =>
      bearer === `Bearer ${ADMIN}`
        ? json(200, { review_id: 'rv-admin' })
        : json(403, { error: { code: 'auth.permission_denied' } }),
  });

  const gateway = gatewayFor({ adminTokenFile: tempFile(ADMIN) });

  const adminClaim = await exchange(gateway, '/review/claim', {
    task_id: 'T1',
    attempt_id: 'a1',
    as: 0,
  });
  assert.equal(adminClaim.status, 200);
  assert.deepEqual(adminClaim.payload, { review_id: 'rv-claim' });

  const humanClaim = await exchange(gateway, '/review/claim', {
    task_id: 'T1',
    attempt_id: 'a1',
    as: 1,
  });
  assert.equal(humanClaim.status, 200);
  assert.deepEqual(humanClaim.payload, { review_id: 'rv-human' });
});

test('a platform refusal reaches the browser verbatim', async (t) => {
  globalThis.fetch = responder({
    '/api/v1/review/claim': (_request, json) =>
      json(409, { error: { code: 'review.cross_provider_required' } }),
  });
  const gateway = gatewayFor({ adminTokenFile: tempFile(ADMIN) });
  const result = await exchange(gateway, '/review/claim', {
    task_id: 'T1',
    attempt_id: 'a1',
    as: 0,
  });
  assert.equal(result.status, 409);
  assert.equal(result.payload.error, 'review.cross_provider_required');
});

test('an unknown actor value is refused before any backend call', async (t) => {
  globalThis.fetch = responder({});
  const gateway = gatewayFor({ adminTokenFile: tempFile(ADMIN) });
  const result = await exchange(gateway, '/review/claim', {
    task_id: 'T1',
    attempt_id: 'a1',
    as: 7,
  });
  assert.equal(result.status, 400);
  assert.equal(result.payload.error, 'invalid_review_fields');
});

test('a second administrator action still sends the harness id, not the cached token', async () => {
  // Regression for the live failure at audit seq 386: the identity cache returned
  // the cached *credential* on the second call in a process, so the platform saw
  // `reviewer_harness_id: "apt_..."` and refused with `identity_mismatch`.
  // Claim then submit is exactly the real sequence, so both are exercised.
  const seen = [];
  let whoamiCalls = 0;
  const base = responder({
    '/api/v1/review/claim': (bearer, json, init) => {
      seen.push({ path: 'claim', body: JSON.parse(init.body) });
      return json(200, { review_id: 'rv-1', reviewed_commit: 'a'.repeat(40) });
    },
    '/api/v1/review/submit': (bearer, json, init) => {
      seen.push({ path: 'submit', body: JSON.parse(init.body) });
      return json(200, { review_id: 'rv-1' });
    },
  });
  globalThis.fetch = async (target, init) => {
    if (
      new URL(String(target)).pathname === '/api/v1/auth/whoami' &&
      init?.headers?.Authorization === `Bearer ${ADMIN}`
    ) {
      whoamiCalls += 1;
    }
    return base(target, init);
  };

  const gateway = gatewayFor({ adminTokenFile: tempFile(ADMIN) });
  const claimed = await exchange(gateway, '/review/claim', {
    task_id: 'T1',
    attempt_id: 'a1',
    as: 0,
  });
  const submitted = await exchange(gateway, '/review/submit', {
    task_id: 'T1',
    attempt_id: 'a1',
    review_id: 'rv-1',
    verdict: 'request_changes',
    reviewed_commit: 'a'.repeat(40),
    review_revision: 1,
    findings: [],
    as: 0,
  });

  assert.equal(claimed.status, 200);
  assert.equal(submitted.status, 200);
  assert.equal(whoamiCalls, 1, 'the credential is verified once and cached');
  assert.deepEqual(
    seen.map((entry) => entry.body.reviewer_harness_id),
    ['office-admin', 'office-admin'],
    'the declared reviewer must be the harness id on every call, never the token',
  );
  assert.ok(
    seen.every((entry) => !String(entry.body.reviewer_harness_id).startsWith('apt_')),
    'a cached credential must never become a declared identity',
  );
});

test('without an admin credential the admin routes refuse instead of borrowing the operator', async (t) => {
  globalThis.fetch = responder({
    // The operator route would happily succeed; the admin route must not reach
    // the backend at all.
    '/api/v1/human/review/claim': (_bearer, json) => json(200, { review_id: 'rv-human' }),
    '/api/v1/review/claim': (_bearer, json) => json(500, { error: { code: 'must_not_be_called' } }),
  });
  const gateway = gatewayFor();
  const result = await exchange(gateway, '/review/claim', {
    task_id: 'T1',
    attempt_id: 'a1',
    as: 0,
  });
  assert.equal(result.status, 503);
  assert.equal(result.payload.error, 'formal_admin_not_configured');
});

// 办公室不提供**人工派单**。派单是管理员 Harness 的动作（`/admin/dispatch`，
// 用管理员凭据）；这条以人类身份调用平台派单接口的路由没有任何前端调用者，
// 却是"一个目标 × 9 个窗口"那批历史任务的来源。
test('the human dispatch route is gone: only the administrator dispatches', async (t) => {
  globalThis.fetch = responder({
    '/api/v1/groups/g/dispatch': (_bearer, json) => json(200, { task_id: 'T-new' }),
    '/api/v1/tasks/T-new': (_bearer, json) => json(200, { task_id: 'T-new', group_id: 'g' }),
  });
  const gateway = gatewayFor();

  const result = await exchange(gateway, '/dispatch', {
    request_id: 'r1',
    title: '做一个游戏',
    goal: '做一个游戏',
    harness_id: 'codex',
    exclusive_paths: ['src'],
    acceptance: [],
  });

  assert.equal(result.status, 404);
  assert.equal(result.payload.error, 'formal_route_not_allowed');
});

// The harness registry is global: every project shares the same registered
// windows, and `harnesses[].running_tasks` counts leases of *all* groups. If
// `/status` handed the browser that global list as "this project's members",
// opening a second project would fold the other project's load into the patrol
// summary. The only trustworthy membership is what this group's tasks mention.
test('a declared base is offered and registered with the platform\'s own template', async (t) => {
  // 声明出来的基座（`adapters/cli.json`）：平台知道它，办公室本地照着登记 ——
  // 模板由平台给（与平台造注册表的是同一份），本地不自己拼。
  let registered = null;
  globalThis.fetch = responder({
    '/api/v1/harnesses': (bearer, json, init) => {
      if (init?.method === 'POST') {
        registered = JSON.parse(String(init.body));
        return json(200, { harness_id: registered.harness_id, registration_version: registered.registration_version });
      }
      return json(200, {
        harnesses: [],
        base_adapters: [
          {
            harness_id: 'newcli',
            name: 'New CLI',
            provider: 'newcli',
            adapter: 'newcli-print',
            adapter_version: '1.0.0',
            capabilities: ['coding', 'review'],
            registration_version: 'reg-001',
          },
        ],
      });
    },
    '/api/v1/groups/g/tasks': (_bearer, json) => json(200, { tasks: [] }),
  });
  const gateway = gatewayFor({ adminTokenFile: tempFile(ADMIN) });

  // 1. 它出现在「可登记的基座」列表里。
  let status = null;
  let payload = null;
  await gateway.handle(
    {
      url: '/api/formal/status',
      method: 'GET',
      socket: { localPort: 4999, remoteAddress: '127.0.0.1' },
      headers: { host: '127.0.0.1:4999', origin: 'http://127.0.0.1:4999', 'x-office-formal': '1' },
    },
    {
      writeHead: (code) => {
        status = code;
      },
      end: (text) => {
        payload = JSON.parse(String(text));
      },
    },
  );
  assert.equal(status, 200);
  const declaredBase = payload.base_adapters.find((item) => item.harness_id === 'newcli');
  assert.ok(declaredBase, 'the declared base is offered, not just the built-ins');
  assert.equal(declaredBase.adapter, 'newcli-print');
  assert.equal(declaredBase.name, 'New CLI');

  // 「平台自己的工具」也是可以开窗口的基座：模型用不了工具时改用它。
  // 这一条守的是**人能选到它**，不是它能不能跑（那由后端测试守）。
  const platformBase = payload.base_adapters.find((item) => item.harness_id === 'platform');
  assert.ok(platformBase, 'the platform-tools base is offered next to the CLIs');
  assert.equal(platformBase.name, '平台工具');
  assert.equal(platformBase.adapter, 'platform-tools');

  // 2. 登记它时，用的是**平台给的那份**模板。
  const { status: regStatus } = await exchange(gateway, '/base-harnesses', { harness_id: 'newcli' });
  assert.equal(regStatus, 200);
  assert.ok(registered, 'the base was registered on the platform');
  assert.equal(registered.harness_id, 'newcli');
  assert.equal(registered.provider, 'newcli');
  assert.equal(registered.adapter, 'newcli-print');
  assert.equal(registered.adapter_version, '1.0.0');
  assert.deepEqual(registered.capabilities, ['coding', 'review']);
  assert.equal(registered.registration_version, 'reg-001');
});

test('the status roster is scoped to this project, not the global registry', async (t) => {
  globalThis.fetch = responder({
    '/api/v1/harnesses': (_bearer, json) =>
      json(200, {
        harnesses: [
          { harness_id: 'codex', name: 'codex', registration_version: 'reg-001', running_tasks: 2 },
          { harness_id: 'qoder-member-1', name: 'qoder-1', registration_version: 'reg-001', running_tasks: 1 },
          { harness_id: 'pi', name: 'pi', registration_version: 'reg-001', running_tasks: 0 },
        ],
      }),
    '/api/v1/groups/g/tasks': (_bearer, json) =>
      json(200, {
        // The real backend returns the raw row, where the targets live in
        // `assigned_harness_ids_json`, not in a parsed array.
        tasks: [
          { task_id: 'FD-00000000000000aa', group_id: 'g', status: 'awaiting_review', owner: 'codex', assigned_harness_ids_json: '["codex"]' },
          { task_id: 'FD-00000000000000bb', group_id: 'g', status: 'ready', assigned_harness_ids_json: '["qoder-member-1"]' },
        ],
      }),
  });
  const gateway = gatewayFor({ adminTokenFile: tempFile(ADMIN) });

  const response = await (async () => {
    let status = null;
    let payload = null;
    const request = {
      url: '/api/formal/status',
      method: 'GET',
      socket: { localPort: 4999, remoteAddress: '127.0.0.1' },
      headers: { host: '127.0.0.1:4999', origin: 'http://127.0.0.1:4999', 'x-office-formal': '1' },
    };
    const res = {
      writeHead: (code) => {
        status = code;
      },
      end: (text) => {
        payload = JSON.parse(String(text));
      },
    };
    await gateway.handle(request, res);
    return { status, payload };
  })();

  assert.equal(response.status, 200);
  assert.deepEqual(
    response.payload.project_members,
    ['codex', 'qoder-member-1'],
    'only harnesses this project actually mentions are members',
  );
  assert.ok(
    !response.payload.project_members.includes('pi'),
    'a globally registered window with no task in this group must not be a member',
  );
  // The full roster is still returned so the panel can say what exists without
  // folding it into the numbers.
  assert.deepEqual(
    response.payload.harnesses.map((item) => item.harness_id),
    ['codex', 'qoder-member-1', 'pi'],
  );
});

test('a task from another group would fail the status scope check loudly', async (t) => {
  globalThis.fetch = responder({
    '/api/v1/harnesses': (_bearer, json) => json(200, { harnesses: [] }),
    '/api/v1/groups/g/tasks': (_bearer, json) =>
      json(200, { tasks: [{ task_id: 'FD-00000000000000cc', group_id: 'other', status: 'ready' }] }),
  });
  const gateway = gatewayFor({ adminTokenFile: tempFile(ADMIN) });
  let status = null;
  let payload = null;
  await gateway.handle(
    {
      url: '/api/formal/status',
      method: 'GET',
      socket: { localPort: 4999, remoteAddress: '127.0.0.1' },
      headers: { host: '127.0.0.1:4999', origin: 'http://127.0.0.1:4999', 'x-office-formal': '1' },
    },
    {
      writeHead: (code) => {
        status = code;
      },
      end: (text) => {
        payload = JSON.parse(String(text));
      },
    },
  );
  assert.equal(status, 502);
  assert.equal(payload.error, 'task_scope_mismatch');
});

// GET /status has no body and no write headers; `exchange` is POST-only.
const get = async (gateway, route) => {
  let status = null;
  let payload = null;
  await gateway.handle(
    {
      url: `/api/formal${route}`,
      method: 'GET',
      socket: { localPort: 4999, remoteAddress: '127.0.0.1' },
      headers: { host: '127.0.0.1:4999' },
    },
    {
      writeHead: (code) => {
        status = code;
      },
      end: (text) => {
        payload = JSON.parse(String(text));
      },
    },
  );
  return { status, payload };
};

test('the administrator identity comes from the authenticated credential, not a constant', async () => {
  // R10-fix-15: `office-admin` used to be hard-coded here, so every other window
  // failed with `formal_admin_identity_mismatch` and `/status` reported the
  // constant instead of who the token really was. Nothing may assume that id.
  globalThis.fetch = responder(
    {
      '/api/v1/harnesses': (_bearer, json) => json(200, { harnesses: [] }),
      '/api/v1/groups/g/tasks': (_bearer, json) => json(200, { tasks: [] }),
    },
    'qoder-window-admin',
  );
  const gateway = gatewayFor({ adminTokenFile: tempFile(ADMIN) });

  const { status, payload } = await get(gateway, '/status');
  assert.equal(status, 200);
  assert.equal(payload.admin.identity, 'qoder-window-admin');
  assert.equal(payload.admin.error, null);
});

test('a broken administrator credential is reported without taking /status down', async () => {
  // The owner face must keep working when the admin key is wrong; the panel then
  // has no administrator and says so instead of guessing one.
  globalThis.fetch = responder({
    '/api/v1/harnesses': (_bearer, json) => json(200, { harnesses: [] }),
    '/api/v1/groups/g/tasks': (_bearer, json) => json(200, { tasks: [] }),
  });
  const gateway = gatewayFor({ adminTokenFile: tempFile('apt_' + '3'.repeat(64)) });

  const { status, payload } = await get(gateway, '/status');
  assert.equal(status, 200);
  assert.equal(payload.connected, true);
  assert.equal(payload.admin.identity, null);
  assert.equal(payload.admin.error, 'formal_admin_authentication_required');
});
// ---------------------------------------------------------------------------
// 撤销一份还没被接走的分配（§39 硬规则 7）
//
// 这一组钉的是**网关自己的判断**：谁能撤、什么时候不该撤、写之前现读修订号。
// 平台那边的规则（invited/accepted 才能撤、claimed_at 非空就拒）不在这里复制，
// 但网关不该把明显撤不掉的东西也发给平台 —— 那会让人看到一段平台内部错误。
// ---------------------------------------------------------------------------

const roundsWith = (assignment) => ({
  rounds: [
    {
      round_id: 'alloc-1',
      group_id: 'g',
      state: 'decided',
      tasks: [{ task_id: 'T1' }],
      applications: [],
      assignments: [
        { allocation_id: 'al-1', task_id: 'T1', harness_id: 'qoder', revision: 3, ...assignment },
      ],
    },
  ],
});

test('an unclaimed assignment is revocable, and the gateway rereads its revision', async () => {
  const writes = [];
  globalThis.fetch = responder({
    '/api/v1/allocation/rounds': (_bearer, json) =>
      json(200, roundsWith({ state: 'accepted', claimed_at: null })),
    '/api/v1/revoke': (bearer, json, init) => {
      // 界面上读到的可能是几十秒前的快照；这里必须是**现读的**修订号（3），
      // 不是浏览器传来的旧值。撤一份授权是危险动作，先看现状再写。
      writes.push(JSON.parse(String(init.body)));
      return json(200, { allocation_id: 'al-1', state: 'revoked' });
    },
  });
  const gateway = gatewayFor();

  const read = await get(gateway, '/allocation/assignment?task_id=T1');
  assert.equal(read.status, 200);
  assert.equal(read.payload.revocable, true);
  assert.equal(read.payload.assignment.allocation_id, 'al-1');
  assert.equal(read.payload.round_id, 'alloc-1');

  const revoked = await exchange(gateway, '/allocation/revoke', {
    task_id: 'T1',
    reason: '定错人了，改给另一个窗口',
    expected_revision: 1,
  });
  assert.equal(revoked.status, 200);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].allocation_id, 'al-1');
  assert.equal(writes[0].expected_revision, 3, '用的是现读的修订号，不是界面传来的旧值');
  assert.equal(writes[0].revoked_by, 'office-operator');
  assert.equal(writes[0].reason, '定错人了，改给另一个窗口');
});

test('an assignment somebody already claimed is not revocable and nothing is written', async () => {
  let writes = 0;
  globalThis.fetch = responder({
    '/api/v1/allocation/rounds': (_bearer, json) =>
      json(200, roundsWith({ state: 'accepted', claimed_at: '2026-11-02T10:00:00+00:00' })),
    '/api/v1/revoke': (_bearer, json) => {
      writes += 1;
      return json(200, {});
    },
  });
  const gateway = gatewayFor();

  const read = await get(gateway, '/allocation/assignment?task_id=T1');
  assert.equal(read.payload.revocable, false, '已经开工的不能撤');

  const refused = await exchange(gateway, '/allocation/revoke', { task_id: 'T1', reason: '想换人' });
  assert.equal(refused.status, 409);
  assert.equal(refused.payload.error, 'assignment_not_revocable');
  assert.equal(writes, 0, '撤不掉的就别写');
});

test('revoking without a reason is refused before it reaches the platform', async () => {
  let writes = 0;
  globalThis.fetch = responder({
    '/api/v1/allocation/rounds': (_bearer, json) =>
      json(200, roundsWith({ state: 'accepted', claimed_at: null })),
    '/api/v1/revoke': (_bearer, json) => {
      writes += 1;
      return json(200, {});
    },
  });
  const gateway = gatewayFor();

  const refused = await exchange(gateway, '/allocation/revoke', { task_id: 'T1', reason: '   ' });
  assert.equal(refused.status, 400);
  assert.equal(refused.payload.error, 'allocation_reason_required');
  assert.equal(writes, 0);
});

test('a task with no assignment says so instead of inventing one', async () => {
  globalThis.fetch = responder({
    '/api/v1/allocation/rounds': (_bearer, json) =>
      json(200, {
        rounds: [
          {
            round_id: 'alloc-1',
            group_id: 'g',
            state: 'decided',
            tasks: [{ task_id: 'T2' }],
            assignments: [],
          },
        ],
      }),
  });
  const gateway = gatewayFor();

  const read = await get(gateway, '/allocation/assignment?task_id=T1');
  assert.equal(read.status, 200);
  assert.equal(read.payload.assignment, null);
  assert.equal(read.payload.revocable, false);

  const refused = await exchange(gateway, '/allocation/revoke', { task_id: 'T1', reason: '撤掉' });
  assert.equal(refused.status, 409);
  assert.equal(refused.payload.error, 'assignment_not_found');
});
// ---------------------------------------------------------------------------
// 打回返工：approved 之后人推翻一次（R10 §10 的 reopen_for_changes）
//
// 网关自己的判断只有两条：理由必填、身份用 operator。**能不能打回**由平台判
// （只有 approved 能打回），这里不复制它的状态规则。
// ---------------------------------------------------------------------------

test('sending an approved task back needs a reason and posts as the human', async () => {
  const calls = [];
  globalThis.fetch = responder({
    '/api/v1/human/reopen_for_changes': (_bearer, json, init) => {
      calls.push(JSON.parse(String(init.body)));
      return json(200, { task_id: 'T1', status: 'changes_requested', event: 'reopen_for_changes' });
    },
  });
  const gateway = gatewayFor();

  const empty = await exchange(gateway, '/human/reopen_for_changes', { task_id: 'T1', reason: ' ' });
  assert.equal(empty.status, 400);
  assert.equal(empty.payload.error, 'human_reason_required');
  assert.equal(calls.length, 0, '没理由就别发');

  const sent = await exchange(gateway, '/human/reopen_for_changes', {
    task_id: 'T1',
    reason: '第 3 条验收标准没对上',
  });
  assert.equal(sent.status, 200);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].task_id, 'T1');
  assert.equal(calls[0].reason, '第 3 条验收标准没对上');
  assert.equal(calls[0].decided_by, 'office-operator', '记的是人，不是管理员窗口');
});

test('a reopen request cannot smuggle in other fields', async () => {
  let calls = 0;
  globalThis.fetch = responder({
    '/api/v1/human/reopen_for_changes': (_bearer, json) => {
      calls += 1;
      return json(200, {});
    },
  });
  const gateway = gatewayFor();

  const refused = await exchange(gateway, '/human/reopen_for_changes', {
    task_id: 'T1',
    reason: '不同意',
    decided_by: 'someone-else',
  });
  assert.equal(refused.status, 400);
  assert.equal(refused.payload.error, 'invalid_task_action');
  assert.equal(calls, 0);
});
