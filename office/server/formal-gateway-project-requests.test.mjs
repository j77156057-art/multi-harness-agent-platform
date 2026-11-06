import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { createFormalGateway } from './formal-dispatch-gateway.mjs';

const OPERATOR = 'apt_' + '1'.repeat(64);
const AGENT_TOKEN = 'apt_' + '2'.repeat(64);
const REQUEST_ID = 'PCR-' + 'a'.repeat(32);

function tokenFile(value = OPERATOR) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'formal-project-requests-'));
  const file = path.join(dir, 'operator.key');
  fs.writeFileSync(file, value);
  return file;
}

function gateway() {
  return createFormalGateway({
    upstream: 'http://127.0.0.1:8848',
    tokenFile: tokenFile(),
    project: 'p',
    group: 'g',
  });
}

function agentGateway() {
  return createFormalGateway({
    upstream: 'http://127.0.0.1:8848',
    tokenFile: tokenFile(),
    adminTokenFile: tokenFile(AGENT_TOKEN),
    project: 'p',
    group: 'g',
  });
}

function memberGateway(memberId = 'agent-window') {
  return createFormalGateway({
    upstream: 'http://127.0.0.1:8848',
    tokenFile: tokenFile(),
    memberTokenFile: tokenFile(AGENT_TOKEN),
    memberId,
    project: 'p',
    group: 'g',
  });
}

async function exchange(instance, route, { method = 'GET', body, query = '' } = {}) {
  let status = null;
  let payload = null;
  const request = {
    url: `/api/formal${route}${query}`,
    method,
    socket: { localPort: 4999, remoteAddress: '127.0.0.1' },
    headers: {
      host: '127.0.0.1:4999',
      origin: 'http://127.0.0.1:4999',
      'x-office-formal': '1',
      'content-type': 'application/json',
    },
    async *[Symbol.asyncIterator]() {
      if (body !== undefined) yield Buffer.from(JSON.stringify(body));
    },
  };
  const response = {
    writeHead: code => { status = code; },
    end: text => { payload = JSON.parse(String(text)); },
  };
  assert.equal(await instance.handle(request, response), true);
  return { status, payload };
}

function stubUpstream(handlers, subject = { type: 'human', id: 'office-operator' }) {
  const calls = [];
  globalThis.fetch = async (target, init = {}) => {
    const url = new URL(String(target));
    calls.push({ url, init });
    const json = (status, body) => new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
    if (url.pathname === '/api/v1/auth/whoami') {
      const actual = subject.type === 'harness' && init.headers?.Authorization === `Bearer ${OPERATOR}`
        ? { type: 'human', id: 'office-operator' }
        : subject;
      return json(200, { mode: 'enforce', subject: actual });
    }
    if (url.pathname === '/api/v1/groups') {
      return json(200, { groups: [{ group_id: 'g', project_id: 'p' }] });
    }
    const handler = handlers[url.pathname];
    if (handler) return handler(json, url, init);
    return json(404, { error: { code: 'not_found' } });
  };
  return calls;
}

test('request list preserves real status filter and payload', async () => {
  const row = { request_id: REQUEST_ID, status: 'pending', purpose: '隔离交付' };
  const calls = stubUpstream({
    '/api/v1/project-creation-requests': json => json(200, { requests: [row] }),
  });

  const result = await exchange(gateway(), '/project-creation-requests', { query: '?status=pending' });

  assert.equal(result.status, 200);
  assert.deepEqual(result.payload, { requests: [row] });
  assert.equal(calls.at(-1).url.search, '?status=pending');
});

test('approval uses the authenticated operator and forwards the decision note', async () => {
  const calls = stubUpstream({
    [`/api/v1/project-creation-requests/${REQUEST_ID}/approve`]: (json, _url, init) => {
      const body = JSON.parse(init.body);
      assert.deepEqual(body, { decided_by: 'office-operator', reason: '范围明确' });
      return json(200, { request: { request_id: REQUEST_ID, status: 'approved' } });
    },
  });

  const result = await exchange(gateway(), `/project-creation-requests/${REQUEST_ID}/approve`, {
    method: 'POST',
    body: { decided_by: 'somebody-else', reason: '范围明确' },
  });

  assert.equal(result.status, 200);
  assert.equal(result.payload.request.status, 'approved');
  assert.equal(calls.at(-1).init.method, 'POST');
});

test('blank rejection is refused before touching upstream', async () => {
  const calls = stubUpstream({});

  const result = await exchange(gateway(), `/project-creation-requests/${REQUEST_ID}/reject`, {
    method: 'POST',
    body: { reason: ' ' },
  });

  assert.equal(result.status, 400);
  assert.equal(result.payload.error, 'rejection_reason_required');
  assert.equal(calls.length, 2, 'only identity and group scope checks ran');
});

test('agent application binds the verified harness identity and cannot spoof it', async () => {
  const subject = { type: 'harness', id: 'agent-window' };
  stubUpstream({
    '/api/v1/groups/g/project-creation-requests': (json, _url, init) => {
      assert.equal(init.headers.Authorization, `Bearer ${AGENT_TOKEN}`);
      const body = JSON.parse(init.body);
      assert.equal(body.requested_kind, 'harness');
      assert.equal(body.requested_by, 'agent-window');
      assert.equal(body.purpose, '隔离验证');
      return json(200, { request: { request_id: REQUEST_ID, status: 'pending' } });
    },
  }, subject);

  const result = await exchange(memberGateway(), '/agent/project-creation-requests', {
    method: 'POST',
    body: {
      project_id: 'P-agent', project_name: 'Agent 项目', group_id: 'G-agent', group_name: 'Agent 主群',
      purpose: '隔离验证', scope: '只读验证', expected_output: '待批申请',
      requested_by: 'somebody-else', requested_kind: 'human',
    },
  });

  assert.equal(result.status, 200);
  assert.equal(result.payload.request.status, 'pending');
});

test('agent request list contains only that agents own decisions', async () => {
  const subject = { type: 'harness', id: 'agent-window' };
  stubUpstream({
    '/api/v1/project-creation-requests': json => json(200, { requests: [
      { request_id: REQUEST_ID, requested_by_type: 'harness', requested_by_id: 'agent-window', status: 'rejected', decision_reason: '范围过大' },
      { request_id: 'PCR-' + 'b'.repeat(32), requested_by_type: 'harness', requested_by_id: 'other-agent', status: 'pending' },
      { request_id: 'PCR-' + 'c'.repeat(32), requested_by_type: 'human', requested_by_id: 'agent-window', status: 'pending' },
    ] }),
  }, subject);

  const result = await exchange(agentGateway(), '/agent/project-creation-requests');

  assert.equal(result.status, 200);
  assert.equal(result.payload.requests.length, 1);
  assert.equal(result.payload.requests[0].status, 'rejected');
  assert.equal(result.payload.requests[0].decision_reason, '范围过大');
});

test('configured member id must match the platform credential identity', async () => {
  stubUpstream({}, { type: 'harness', id: 'agent-window' });

  const result = await exchange(memberGateway('somebody-else'), '/agent/project-creation-requests');

  assert.equal(result.status, 503);
  assert.equal(result.payload.error, 'formal_member_identity_mismatch');
});

test('member status exposes only the configured applicant identity', async () => {
  stubUpstream({
    '/api/v1/harnesses': json => json(200, { harnesses: [] }),
    '/api/v1/groups/g/tasks': json => json(200, { tasks: [] }),
  }, { type: 'harness', id: 'agent-window' });

  const result = await exchange(memberGateway(), '/status');

  assert.equal(result.status, 200);
  assert.equal(result.payload.applicant.identity, 'agent-window');
  assert.equal(result.payload.applicant.configured, true);
});

test('status exposes that this office face is the human operator', async () => {
  stubUpstream({
    '/api/v1/harnesses': json => json(200, { harnesses: [] }),
    '/api/v1/groups/g/tasks': json => json(200, { tasks: [] }),
  });

  const result = await exchange(gateway(), '/status');

  assert.equal(result.status, 200);
  assert.equal(result.payload.operator, 'office-operator');
  assert.equal(result.payload.operator_type, 'human');
});
