// 人自己的动作：办公室里的那几条按钮。
//
// 为什么单独一个模块：这些状态集合是**后端的守卫**（`backend/app/human/service.py`），
// 前端只是照抄一份，好让"这个状态没有这个按钮"这件事能被机械断言，而不是靠人眼。
// 后端仍然是唯一裁判 —— 前端给了按钮而状态已经变了，平台会回 409，界面照实显示。
//
// 与旧人操作面（已随 Vue 控制台一起删掉的 `frontend/src/components/TaskPane.vue`）
// 的差别只有一处：那里还有 `adjudicate`（受理升级 → awaiting_human）。
// 这里**不加**：R10 §39 把"传阅与裁决"划给管理员面，`docs/tasks/notes/OFFICE-UI-01.md`
// 也明写"裁决…界面不代点"。`human/adjudicate` 仍然可以在网关与 CLI 上走到。
//
// R10 §8：预算用完的任务，普通的 release 会被平台的预算守卫拒绝，
// 必须显式带上 `attempt_budget_override`。所以放行按钮要显式问一次，
// 而不是让人从 409 里猜（R10 §8 / R8-08）。

/** 可以"回答并恢复"的状态（R10 §10：human.resume）。 */
export const RESUME_STATES = new Set(['awaiting_human', 'plan_drift', 'impl_drift']);

/** 可以"重新打开"的状态（R10 §10：human.reopen / reopen）。 */
export const REOPEN_STATES = new Set([
  'awaiting_human',
  'plan_drift',
  'impl_drift',
  'abandoned',
]);

/**
 * 可以"取消"的状态。
 * 与 `backend/app/human/service.py` 的 `_CANCEL_STATES ∪ _HUMAN_CANCEL_STATES` 一致；
 * `escalated` 刻意不在里面 —— 升级不是取消（那是 release / adjudicate 的事）。
 */
export const CANCEL_STATES = new Set([
  'queued',
  'draft',
  'ready',
  'claimed',
  'in_progress',
  'verifying',
  'needs_help',
  'blocked',
  'changes_requested',
  'plan_drift',
  'impl_drift',
  'awaiting_review',
  'approved',
  'merging',
  'stale',
  'reclaiming',
  'awaiting_human',
]);

/**
 * 按钮上的字。
 *
 * 写的是**人话**，不是平台的动作名：平台那边叫 `human.release`，人只知道
 * "放回去重新找人做"。工具提示（title）里保留一点内部说法，是给要查日志的人。
 */
export const ACTION_LABEL = {
  resume: '我来回答',
  reopen: '重新打开',
  release: '放回去重找',
  cancel: '不做了',
};

/** 工具提示：说清楚这个按钮按下之后平台会发生什么。 */
export const ACTION_HINT = {
  resume: '你的回答会记在平台上，干活的人能看到，任务回到他手里继续做。',
  reopen: '这条活重新打开：清掉现在的负责人，回到「等人接」，谁都能接。',
  release: '这条活放回「等人接」的列表，下一轮重新找人来做。',
  cancel: '取消就是结束：这条活不会再有人做。',
};

/**
 * 这条任务现在能被人做哪些动作。
 * @param {{status?: string}} task
 * @returns {string[]} 动作名，空数组表示"这一步没有人的动作"
 */
export function humanDecisionActions(task) {
  const status = task && task.status;
  if (!status) return [];
  const actions = [];
  if (RESUME_STATES.has(status)) actions.push('resume');
  if (REOPEN_STATES.has(status)) actions.push('reopen');
  if (status === 'escalated') actions.push('release');
  if (CANCEL_STATES.has(status)) actions.push('cancel');
  return actions;
}

/**
 * 放行要不要显式带上预算豁免。
 *
 * R10 §8：一旦 `attempt_no` 到了 `max_attempts`，普通的 release 会把任务放回队列，
 * 而下一次 claim 又会被同一个预算守卫拒绝 —— 那就是"放回去了但没人能领"。
 * 人必须显式说"再给一次"。已经带过豁免的任务（`budget_override_by` 有值）不用再问。
 */
export function releaseNeedsOverride(task) {
  if (!task || task.status !== 'escalated') return false;
  if (task.budget_override_by !== null && task.budget_override_by !== undefined) return false;
  const limit = Number(task.max_attempts || 0);
  const used = Number(task.attempt_no || 0);
  return limit > 0 && used >= limit;
}
