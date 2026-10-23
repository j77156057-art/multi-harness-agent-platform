// New synthetic adapter sample, separate from frozen contribution fixtures.
import { OFFICE_READ_VERSION } from './office-read-runtime.js';
export function createOfficeReadFixture() {
  return {
    version: OFFICE_READ_VERSION, synthetic: true,
    scope: {project_id: 'SYN-OFFICE', group_id: 'SYN-TEAM'},
    groups_response: {groups: [{project_id: 'SYN-OFFICE', group_id: 'SYN-TEAM', name: '合成协作群'}]},
    tasks_response: {tasks: [
      {task_id: 'SYN-T1', group_id: 'SYN-TEAM', title: '提交读取适配报告', status: 'awaiting_review', owner: 'SYN-CODEX', current_attempt_id: 'SYN-A1', current_epoch: 1},
      {task_id: 'SYN-T2', group_id: 'SYN-TEAM', title: '核对页面展示', status: 'approved', owner: 'SYN-REVIEWER', current_attempt_id: null, current_epoch: 2},
    ]},
    event_pages: [{task_id: 'SYN-T1', since_seq: 0, limit: 200, response: {events: [
      {seq: 1, aggregate_id: 'SYN-T1', aggregate_type: 'task', event_name: 'claim', actor_id: 'SYN-CODEX', payload: {attempt_id: 'SYN-A1'}, created_at: '2026-10-12T00:00:00Z'},
      {seq: 3, aggregate_id: 'SYN-T1', aggregate_type: 'task', event_name: 'submit_result', actor_id: 'SYN-CODEX', payload: {completion: {summary: '合成自述：全部通过'}}, created_at: '2026-10-12T00:01:00Z'},
      {seq: 5, aggregate_id: 'SYN-T1', aggregate_type: 'task', event_name: 'future_extension', actor_id: 'SYN-CODEX', payload: {note: '未知事件原样保留，不推导任务状态'}, created_at: '2026-10-12T00:02:00Z'},
    ], next_seq: 5, has_more: false}},
      {task_id: 'SYN-T2', since_seq: 0, limit: 200, response: {events: [], next_seq: 0, has_more: false}}],
    message_pages: [{group_id: 'SYN-TEAM', since_seq: 0, limit: 100, response: {messages: [
      {message_id: 'SYN-M1', group_id: 'SYN-TEAM', task_id: 'SYN-T1', sender_id: 'SYN-CODEX', body: '给管理员 Codex 的：合成报告已提交，等待独立审核。', created_seq: 2, created_at: '2026-10-12T00:01:00Z'},
      {message_id: 'SYN-M2', group_id: 'SYN-TEAM', task_id: null, sender_id: 'SYN-REVIEWER', body: '本输入仅用于离线展示；平台报告已批准，不代表客户端核验了验收凭据。', created_seq: 4, created_at: '2026-10-12T00:02:00Z'},
    ], next_seq: 4, has_more: false}}],
  };
}
