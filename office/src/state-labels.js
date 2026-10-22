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
 * 「做完了」的状态：窗口把活干完并交上去了（平台那边是提交/审过/合入）。
 * 剩下的是"还没交"——包括还在做的、卡住的、被退回的。
 */
export const DONE_STATES = new Set(['awaiting_review', 'approved', 'merged']);

/** 状态词 + 兜底（平台给了不认识的状态就照实显示它，不编）。 */
export const stateLabel = (status) => STATE_LABEL[status] || String(status || '');
