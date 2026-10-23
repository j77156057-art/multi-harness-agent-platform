import assert from 'node:assert/strict';
import { createRoutedMessage, routedRecipient, ROUTED_MESSAGE_VERSION } from '../src/routed-message.js';

const admin = routedRecipient('manager', 'Mira', 'admin');
const member = routedRecipient('codex', 'Codex');

const completion = createRoutedMessage({
  sender: 'codex', recipients: [admin], kind: 'task_completion', taskId: 'task-01',
  requestId: 'task-01:completion:1', subject: '任务完成，等待审核', body: '测试与交付文档已附上。', action: 'review',
});
assert.equal(completion.version, ROUTED_MESSAGE_VERSION);
assert.deepEqual(completion.recipient_instance_ids, ['manager']);
assert.match(completion.prompt, /^给管理员Mira的：\n任务完成，等待审核\n/);
assert.equal(completion.task_id, 'task-01');

const review = createRoutedMessage({
  sender: 'manager', recipients: [member], kind: 'review_result', taskId: 'task-01',
  subject: '审核结果', body: '已通过初审，请查看整合意见。', action: 'continue',
});
assert.match(review.prompt, /^给Codex的：\n审核结果\n/);

const broadcast = createRoutedMessage({
  sender: 'manager', recipients: [member, routedRecipient('opencode', 'OpenCode')],
  kind: 'plan', subject: '任务表更新', body: '请阅读新一轮约束。',
});
assert.match(broadcast.prompt, /^给Codex的：、给OpenCode的：\n/);

assert.throws(() => createRoutedMessage({ sender: 'codex', recipients: [], subject: 'x', body: 'y' }), /missing_recipients/);
assert.throws(() => createRoutedMessage({ sender: 'codex', recipients: [admin], subject: '', body: 'y' }), /invalid_message/);

console.log('routed messages: 5/5 PASS');
