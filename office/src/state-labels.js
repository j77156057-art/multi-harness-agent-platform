// 状态词：**给人看的说法**，不是平台内部的状态名。
//
// 平台那边叫 draft/awaiting_review/escalated 是一回事，但第一次用这个页面的人
// 不知道这些词是什么意思。所以这里换成"现在轮到谁"的说法 —— 想看内部名字的
// 人可以去 docs/spec/0.3-R10.md。
//
// 办公室（flat-office.js）和「各窗口说了什么」（outputs.js）共用这一份，
// 两个页面的说法不会各说各话。
export const STATE_LABEL = {
  draft: '还没开始',
  ready: '等人接',
  queued: '排队中',
  claimed: '已有人接',
  in_progress: '正在做',
  verifying: '在检查',
  awaiting_review: '等着审',
  changes_requested: '要返工',
  approved: '审过了',
  merging: '正在合并',
  merged: '已完成',
  abandoned: '已放弃',
  stale: '卡住了',
  cancelled: '已取消',
  escalated: '等你处理',
  awaiting_human: '等你回答',
  reclaiming: '重新找人做',
  plan_drift: '需求变了',
  impl_drift: '做偏了',
  blocked: '遇到问题',
  needs_help: '在求助',
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
  claim: '有人领了这条活',
  claim_for_revision: '领回去返工',
  start: '开工了',
  submit_result: '交上去了',
  abandon: '放弃了',
  release: '放开了',
  cancel: '取消了',
  reassign: '换人做了',
  'human.resume': '你回答了',
  'human.release': '你放行了',
  'human.reopen': '你重新打开了',
  'human.cancel': '你取消了',
  'human.escalate': '你把它交上来了',
  'plan.approve': '方案批准了',
  'plan.reject': '方案被驳回了',
  'review.claim': '有人领了审查',
  'review.submit': '审查交上去了',
  request_changes: '要求返工',
  reopen_for_changes: '打回去返工',
  verify_passed: '校验通过了',
  merge_success: '合进主分支了',
  merge_conflict: '合并撞车了',
  merge_conflict_needs_owner: '合并撞车了，要你定',
  permission_denied: '有人被拒绝了',
  'task.created': '活建好了',
  'task.dispatched': '派下去了',
};

/** 事件名 + 兜底。 */
export const eventLabel = (name) => EVENT_LABEL[name] || String(name || '');
