// 「刷新之后草稿和『刚才看到哪一页』还在」——这一条的纯规则校验（不需要浏览器）。
//
// 起因是审计里那两类"真相只在浏览器里"的东西：
//   · 表单草稿（`drafts`）只在内存里，整页重载就没了；
//   · 视图状态（筛选/排序/每页/页码/看哪个会话/哪些卡片开着）刷新后回到默认。
// 修法是把两者存进 sessionStorage。这一支钉住三条规则：
//   · **凭据一个字节都不落盘**：字段名像凭据（`userkey-key`）不存，文本里像凭据
//     （有人把 key 贴进群聊）也不存；
//   · 读回来的东西当不可信输入：坏了/认不出的取值一律按没存处理，不抛异常；
//   · 只删该删的：`forgetDraft` 不能顺手把别的格子一起清掉。
import assert from 'node:assert/strict';

// 这个模块摸的是浏览器的 sessionStorage；给它一个内存版替身再 import。
const store = new Map();
globalThis.window = {
  sessionStorage: {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  },
};

const {
  clearDrafts,
  clearViewState,
  forgetDraft,
  isPersistableDraft,
  isSecretDraft,
  looksLikeSecret,
  readDrafts,
  readViewState,
  writeDrafts,
  writeViewState,
} = await import('../src/session-state.js');

const DRAFT_KEY = 'agent-office.drafts.v1';
const VIEW_KEY = 'agent-office.view.v1';

const checks = [];
const check = (name, fn) => {
  fn();
  checks.push(name);
};

check('a field named like a credential is never persistable', () => {
  // 用户明确要求的那一条：userkey-key 永远排除。
  assert.equal(isSecretDraft('userkey-key'), true);
  for (const name of ['api-token', 'openai_api_key', 'password', 'client-secret', 'authorization', 'cookie', 'accessKey']) {
    assert.equal(isSecretDraft(name), true, name);
  }
  // 不是凭据的不要误伤。
  for (const name of ['userkey-label', 'userkey-base', 'userkey-models', 'userkey-protocol', 'chat', 'assistant-question', 'keyword']) {
    assert.equal(isSecretDraft(name), false, name);
  }
});

check('a value shaped like a credential is not persisted even in a plain field', () => {
  // 有人在群聊里贴 key 是常事，那不该被存下来。
  assert.equal(looksLikeSecret('sk-proj-abcdefgh12345678'), true);
  assert.equal(looksLikeSecret('ghp_abcdefghijklmnopqrstuvwxyz0123456789'), true);
  assert.equal(looksLikeSecret('AKIAIOSFODNN7EXAMPLE'), true);
  assert.equal(looksLikeSecret('-----BEGIN RSA PRIVATE KEY-----'), true);
  assert.equal(looksLikeSecret('Authorization: Bearer abcdefghijklmnopqrstu'), true);
  assert.equal(looksLikeSecret('明天十点开会，记得带笔记本'), false);
  assert.equal(looksLikeSecret(''), false);
  assert.equal(isPersistableDraft('chat', 'sk-proj-abcdefgh12345678'), false);
  assert.equal(isPersistableDraft('chat', '这条活的验收标准是什么？'), true);
});

check('writing keeps the non-credential drafts and drops the key', () => {
  clearDrafts();
  assert.equal(
    writeDrafts({
      'userkey-label': '我的 OpenAI',
      'userkey-base': 'https://api.openai.com/v1',
      'userkey-key': 'placeholder-value',
      chat: '群里说一句',
    }),
    true,
  );
  const kept = readDrafts();
  assert.equal(kept['userkey-label'], '我的 OpenAI');
  assert.equal(kept['userkey-base'], 'https://api.openai.com/v1');
  assert.equal(kept.chat, '群里说一句');
  // 关键断言：凭据那一格在存储里**根本不存在**。
  assert.equal('userkey-key' in kept, false);
  assert.equal(store.get(DRAFT_KEY).includes('placeholder-value'), false);
});

check('a tampered store cannot smuggle a credential back in', () => {
  store.set(DRAFT_KEY, JSON.stringify({ chat: 'ok', 'userkey-key': 'leaked' }));
  const kept = readDrafts();
  assert.deepEqual(kept, { chat: 'ok' });
});

check('forgetting one draft leaves the others alone', () => {
  clearDrafts();
  writeDrafts({ chat: '发出去的话', 'userkey-label': '名字' });
  forgetDraft('chat');
  const kept = readDrafts();
  assert.equal('chat' in kept, false);
  assert.equal(kept['userkey-label'], '名字');
});

check('when nothing is persistable the key is removed, not left as an empty shell', () => {
  clearDrafts();
  writeDrafts({ 'userkey-key': 'placeholder-value' });
  assert.equal(store.has(DRAFT_KEY), false);
  assert.deepEqual(readDrafts(), {});
});

check('broken draft cache reads as no cache', () => {
  store.set(DRAFT_KEY, '{not json');
  assert.deepEqual(readDrafts(), {});
  store.set(DRAFT_KEY, JSON.stringify(['a', 'b']));
  assert.deepEqual(readDrafts(), {});
});

check('view state round-trips', () => {
  clearViewState();
  const view = {
    filter: 'waiting',
    sort: 'attempts',
    groupBy: 'time',
    pageSize: 50,
    page: 3,
    view: { kind: 'window', id: 'codex-1' },
    openFolds: ['pool', 'installs'],
    closedFolds: ['members'],
  };
  writeViewState(view);
  assert.deepEqual(readViewState(), view);
});

check('unknown or out-of-range values fall back to the caller default', () => {
  clearViewState();
  writeViewState({
    filter: 'nonsense',
    sort: 'random',
    groupBy: 'galaxy',
    pageSize: 7,
    page: -2,
    view: { kind: 'nowhere', id: null },
  });
  // 全是认不出的取值 ⇒ 一个字段都不返回，调用方原样保留自己的默认值。
  assert.deepEqual(readViewState(), {});
});

check('a window view without an id is not restored', () => {
  clearViewState();
  writeViewState({ view: { kind: 'window', id: null } });
  assert.deepEqual(readViewState(), {});
  writeViewState({ view: { kind: 'window', id: 'codex-1' } });
  assert.deepEqual(readViewState(), { view: { kind: 'window', id: 'codex-1' } });
});

check('fold keys are de-duplicated, string-only and bounded', () => {
  clearViewState();
  const many = Array.from({ length: 400 }, (_, index) => `fold-${index}`);
  writeViewState({ openFolds: ['pool', 'pool', 42, '', null, ...many] });
  const kept = readViewState().openFolds;
  assert.ok(kept.includes('pool'));
  assert.equal(kept.filter((item) => item === 'pool').length, 1);
  assert.equal(kept.every((item) => typeof item === 'string' && item), true);
  assert.ok(kept.length <= 200, String(kept.length));
});

check('broken view cache reads as no cache', () => {
  store.set(VIEW_KEY, 'not json at all');
  assert.deepEqual(readViewState(), {});
  store.set(VIEW_KEY, JSON.stringify({ filter: 'all', sort: 'updated' }));
  assert.deepEqual(readViewState(), { filter: 'all', sort: 'updated' });
});

check('page one is a value worth restoring (it is what a person chose)', () => {
  // page=1 是默认值，但它也是人点了「上一页」之后的状态 —— 不能当成"没存"丢掉，
  // 否则回到第 1 页再刷新会跳到别的页（第 2 页）上去。
  clearViewState();
  writeViewState({ page: 1 });
  assert.deepEqual(readViewState(), { page: 1 });
});

console.log(`session state: ${checks.length}/${checks.length} PASS`);
