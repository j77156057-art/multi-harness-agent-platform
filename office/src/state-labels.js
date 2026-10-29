// 状态词：**给人看的说法**，不是平台内部的状态名。
//
// 平台那边叫 draft/awaiting_review/escalated 是一回事，但第一次用这个页面的人
// 不知道这些词是什么意思。所以这里换成"现在轮到谁"的说法 —— 想看内部名字的
// 人可以去 docs/spec/0.3-R10.md。
//
// 办公室（flat-office.js）和「各窗口说了什么」（outputs.js）共用这一份，
// 两个页面的说法不会各说各话。
export const STATE_LABEL = {
  draft: '尚未开始',
  draft_rejected: '方案已驳回',
  ready: '待领取',
  queued: '排队中',
  claimed: '已领取',
  in_progress: '执行中',
  verifying: '校验中',
  awaiting_review: '待审核',
  changes_requested: '需返工',
  approved: '审核通过',
  merging: '合并中',
  merged: '已完成',
  abandoned: '已放弃',
  stale: '已停滞',
  cancelled: '已取消',
  escalated: '待你处理',
  awaiting_human: '待你回答',
  reclaiming: '重新分派',
  plan_drift: '需求变更',
  impl_drift: '实现偏离',
  blocked: '已阻塞',
  needs_help: '待协助',
};

/**
 * 「做完了」与「没做成」是**活**的状态，不是某一轮的状态。
 *
 * 真数据实测踩过的坑：平台的 attempt 状态列在提交之后就停在 verifying 了 ——
 * 一条已经 merged 的活，它的 attempt 状态还是 verifying。按 attempt 状态筛
 * "做完了的"会一条都筛不出来。活的状态才是人理解的那个意思。
 */
export const DONE_STATES = new Set(['awaiting_review', 'approved', 'merged']);

/** 明确没做成的（放弃 / 卡住 / 被取消）。 */
export const FAILED_STATES = new Set(['abandoned', 'stale', 'cancelled']);

/** 状态词 + 兜底（平台给了不认识的状态就照实显示它，不编）。 */
export const stateLabel = (status) => STATE_LABEL[status] || String(status || '');

/**
 * 事件名说人话。
 *
 * 平台的 event_name 是机器名（`claim` / `submit_result` / `plan.approve`…）。
 * 事件列表是给人看"这条活经历了什么"的，印机器名等于让人自己翻译。
 * 不认识的事件名照实显示（宁可原样，也不要编一个错的动作）。
 */
export const EVENT_LABEL = {
  claim: '已有人领取该任务',
  claim_for_revision: '已领回返工',
  start: '开始执行',
  submit_result: '已提交',
  abandon: '已放弃',
  release: '已释放',
  cancel: '已取消',
  reassign: '已改派',
  'human.resume': '你已回答',
  'human.release': '你已放行',
  'human.reopen': '你已重新打开',
  'human.cancel': '你已取消',
  'human.escalate': '已提交给你',
  'plan.approve': '方案已批准',
  'plan.reject': '方案已驳回',
  'review.claim': '已有人领取审查',
  'review.submit': '审查已提交',
  request_changes: '已要求返工',
  reopen_for_changes: '已退回返工',
  verify_passed: '校验已通过',
  merge_success: '已合入主分支',
  merge_conflict: '合并冲突',
  merge_conflict_needs_owner: '合并冲突，需你决定',
  permission_denied: '权限不足，已拒绝',
  'task.created': '任务已创建',
  'task.dispatched': '已分派',
  'approve': '已批准',
  'reopen': '已重新打开',
  'human.adjudicate': '你已裁决',
  'human_wait_timeout': '等待回答超时',
  'merge_start': '开始合并',
  'merge_reverted': '合并已回退',
  'merge_post_commit_failure_escalated': '合并后发生异常，已提交给你',
  'lease_expire': '工位已过期',
  'attempt_budget_exhausted': '尝试次数已用尽',
  'stuck_threshold_reached': '已触及停滞阈值',
  'escalation_reminder': '已提醒管理员',
  'secret_scan_blocked': '已被密钥扫描拦截',
  'idempotency_conflict': '重复提交',
};

/** 事件名 + 兜底。 */
export const eventLabel = (name) => EVENT_LABEL[name] || String(name || '');

/**
 * 平台给的「这一轮结果」是人话，但偏口语（交上去了 / 放开了）。
 *
 * 界面上统一成书面说法；**比较仍然用平台原值** —— 拿显示词去比数据会错。
 */
export const OUTCOME_LABEL = {
  '交上去了': '已提交',
  '交了东西': '已提交',
  '放开了': '已释放',
  '租约被放开了（这条活还等着有人接）': '租约已释放（任务仍待领取）',
  '放弃了': '已放弃',
  '这条活被放弃了': '任务已放弃',
  '这条活被取消了': '任务已取消',
  '跑了': '已执行',
  '开工了': '开始执行',
};

/** 平台结果词 → 书面说法（不认识的值照实显示，不编）。 */
export const outcomeLabel = (value) => OUTCOME_LABEL[String(value || '')] || String(value || '');
