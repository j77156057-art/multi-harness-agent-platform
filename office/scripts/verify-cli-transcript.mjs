// 从 CLI 的机器日志里抽人话。
//
// 三家 CLI 的格式都要认（查过文档，别猜）：
//   - Claude Code --output-format stream-json
//   - Codex exec --json
//   - OpenCode 的会话 JSON
// 认不出来就原样返回，绝不猜。
import assert from 'node:assert/strict';

import { headlineText, parseTranscript } from '../src/cli-transcript.js';

const checks = [];
const check = (name, fn) => {
  fn();
  checks.push(name);
};

check('plain text is not treated as JSON', () => {
  const parsed = parseTranscript('npm test\n> 12 passed\n');
  assert.equal(parsed.isJson, false);
  assert.equal(parsed.texts.length, 0);
  assert.match(parsed.plain, /12 passed/);
});

check("Claude Code: the assistant's text blocks are picked out", () => {
  const raw = [
    JSON.stringify({ type: 'user', message: { content: '把登录页改一下' } }),
    JSON.stringify({
      type: 'assistant',
      message: { content: [{ type: 'thinking', text: '先看文件' }, { type: 'text', text: '我先读了 login.vue。' }] },
    }),
    JSON.stringify({ type: 'tool_use', tool: 'Read', input: { file_path: '/x/login.vue' } }),
    JSON.stringify({ type: 'assistant', message: { content: [{ type: 'text', text: '改完了，测试通过。' }] } }),
  ].join('\n');
  const parsed = parseTranscript(raw);
  assert.equal(parsed.isJson, true);
  assert.deepEqual(parsed.texts.map((item) => item.text), ['我先读了 login.vue。', '改完了，测试通过。']);
  assert.equal(headlineText(parsed), '改完了，测试通过。');
  assert.equal(parsed.tools.length, 1);
  // Claude Code 的 tool_use 把 input 放在顶层。
  assert.equal(parsed.tools[0].label, '读取文件：login.vue');
});

check('Claude Code: the final result line is the conclusion', () => {
  const raw = [
    JSON.stringify({ type: 'assistant', message: { content: [{ type: 'text', text: '中间过程' }] } }),
    JSON.stringify({ type: 'result', subtype: 'success', result: '最后一句结论', is_error: false }),
  ].join('\n');
  const parsed = parseTranscript(raw);
  assert.equal(parsed.texts.length, 2);
  assert.equal(parsed.texts[parsed.texts.length - 1].kind, '结论');
  assert.equal(headlineText(parsed), '最后一句结论');
});

check('Codex: agent_message items are the words', () => {
  const raw = [
    JSON.stringify({ type: 'thread.started', thread_id: 'x' }),
    JSON.stringify({ type: 'item.started', item: { id: 'i0', type: 'command_execution', command: 'bash -lc ls', status: 'in_progress' } }),
    JSON.stringify({ type: 'item.completed', item: { id: 'i1', type: 'agent_message', text: '仓库里有 docs、sdk、examples。' } }),
    JSON.stringify({ type: 'turn.completed', usage: { input_tokens: 10 } }),
  ].join('\n');
  const parsed = parseTranscript(raw);
  assert.equal(parsed.texts.length, 1);
  assert.equal(parsed.texts[0].text, '仓库里有 docs、sdk、examples。');
  assert.equal(parsed.tools.length, 1);
  assert.match(parsed.tools[0].label, /bash -lc ls/);
});

check('Codex: the old item_type name still works', () => {
  const raw = JSON.stringify({
    type: 'item.completed',
    item: { id: 'i1', type: 'assistant_message', text: '老版本的字段名' },
  });
  const parsed = parseTranscript(raw);
  assert.equal(parsed.texts.length, 1);
  assert.equal(parsed.texts[0].text, '老版本的字段名');
});

check('OpenCode: text and tool events are picked out', () => {
  const raw = [
    JSON.stringify({ type: 'step_start' }),
    JSON.stringify({ type: 'text', timestamp: 1, sessionID: 's', part: { partID: 'p1', type: 'text', text: '我改了 fuzzy.ts。' } }),
    JSON.stringify({ type: 'tool', tool: 'shell', state: { status: 'completed', input: { command: 'npm test' } } }),
    JSON.stringify({ type: 'step_finish' }),
  ].join('\n');
  const parsed = parseTranscript(raw);
  assert.equal(parsed.texts.length, 1);
  assert.equal(parsed.texts[0].text, '我改了 fuzzy.ts。');
  assert.equal(parsed.tools.length, 1);
  assert.equal(parsed.tools[0].label, '执行命令：npm test');
});

check('OpenCode: the text may also be at the top level', () => {
  const raw = JSON.stringify({ type: 'text', text: '顶层的文字' });
  const parsed = parseTranscript(raw);
  assert.equal(parsed.texts.length, 1);
  assert.equal(parsed.texts[0].text, '顶层的文字');
});

check('OpenCode: the tool details live in part, not at the top level', () => {
  // 真数据实测：{"type":"tool_use","part":{"type":"tool","tool":"read","state":{...}}}
  const raw = JSON.stringify({
    type: 'tool_use',
    timestamp: 1792652780009,
    sessionID: 'ses_1',
    part: {
      partID: 'prt_1',
      type: 'tool',
      id: 'call_1',
      tool: 'read',
      state: { status: 'completed', input: { path: 'D:/x/login.vue', limit: 25 } },
    },
  });
  const parsed = parseTranscript(raw);
  assert.equal(parsed.tools.length, 1);
  assert.equal(parsed.tools[0].label, '读取文件：login.vue');
  assert.equal(parsed.tools[0].status, 'completed');
});

check('the tool steps are said in plain words', () => {
  const raw = [
    JSON.stringify({ type: 'tool_use', part: { type: 'tool', tool: 'shell', state: { input: { command: 'npm test' } } } }),
    JSON.stringify({ type: 'tool_use', part: { type: 'tool', tool: 'write', state: { input: { path: 'D:/tmp/commit-msg.txt', content: 'x' } } } }),
    JSON.stringify({ type: 'tool_use', part: { type: 'tool', tool: 'grep', state: { input: { pattern: 'TODO' } } } }),
    JSON.stringify({ type: 'tool_use', part: { type: 'tool', tool: 'some_unknown_tool', state: { input: { foo: 'bar' } } } }),
  ].join('\n');
  const parsed = parseTranscript(raw);
  assert.deepEqual(parsed.tools.map((t) => t.label), [
    '执行命令：npm test',
    '写入文件：commit-msg.txt',
    '搜索代码：TODO',
    // 认不出的工具只报名字：挑一个字段来解释它是猜，而猜错比不解释更糟。
    'some_unknown_tool',
  ]);
});

check('an absolute path is shortened and the command says where it ran', () => {
  // 仓库主人：「输出页那行绝对路径压缩成「在 wt\\69d9 中执行了 git log」」。
  const worktree = 'D:\\study\\tools\\agent\\new agent\\wt\\69d9';
  const raw = [
    JSON.stringify({
      type: 'tool_use',
      name: 'Bash',
      input: { command: `cd ${worktree} && git log --oneline -5` },
    }),
    JSON.stringify({
      type: 'item.completed',
      item: { type: 'command_execution', command: `git -C ${worktree} log`, status: 'completed' },
    }),
    JSON.stringify({
      type: 'tool_use',
      name: 'Read',
      input: { file_path: `${worktree}\\src\\main.py` },
    }),
  ].join('\n');
  const parsed = parseTranscript(raw);
  assert.equal(parsed.tools[0].label, '在 wt\\69d9 中执行了 git log --oneline -5');
  assert.equal(parsed.tools[1].label, '在 wt\\69d9 中执行了 git log');
  assert.match(parsed.tools[2].label, /wt\\69d9\\src\\main\.py/);
  // 压短之后不该再出现整条绝对路径。
  assert.ok(!parsed.tools.some((tool) => tool.label.includes('D:\\study')));
});

check('a JSON array in one line also works', () => {
  const raw = JSON.stringify([{ type: 'text', text: '数组里的一段话' }]);
  const parsed = parseTranscript(raw);
  assert.equal(parsed.isJson, true);
  assert.equal(parsed.texts[0].text, '数组里的一段话');
});

check('broken lines do not break the rest', () => {
  const raw = ['{不是 json', JSON.stringify({ type: 'text', text: '好的那行' }), 'tail'].join('\n');
  const parsed = parseTranscript(raw);
  assert.equal(parsed.isJson, true);
  assert.equal(parsed.texts.length, 1);
  assert.equal(parsed.texts[0].text, '好的那行');
});

check('the reasoning noise is not treated as something it said', () => {
  const raw = JSON.stringify({ type: 'reasoning', text: '内部思考' });
  const parsed = parseTranscript(raw);
  assert.equal(parsed.texts.length, 0);
});

console.log(`cli transcript: ${checks.length}/${checks.length} PASS`);
