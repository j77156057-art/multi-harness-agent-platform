/* 办公室拟人动画原型 —— 数据与编排层
 *
 * 刻意与渲染层（render.js）分开：本文件只产「谁在什么状态、该说什么」，
 * 不碰 Canvas。理由见 docs/claims/opencode--office-anim-proto.md：
 * Agentshire 的教训是「不要在逻辑层用 setTimeout 猜动画播了多久」。
 * 所以这里只推进**状态**，动画时长由渲染层自己决定并在结束时回传。
 */

export const CHARACTERS = [
  { key: 'owner',       name: '群主',    role: 'owner',       accent: '#f2b632', hair: '#3b3326', vibe: 'leader' },
  { key: 'coordinator', name: '管理员',  role: 'coordinator', accent: '#f2b632', hair: '#2a2f45', vibe: 'star' },
  { key: 'opencode',    name: 'OpenCode',    accent: '#37c2a8', hair: '#1f4d47', vibe: 'hoodie' },
  { key: 'workbuddy',   name: 'WorkBuddy',   accent: '#e8a33d', hair: '#4a3a1e', vibe: 'glasses' },
  { key: 'qoder',       name: 'Qoder',       accent: '#a78bfa', hair: '#3b2f5c', vibe: 'hoodie' },
  { key: 'trae',        name: 'TRAE',        accent: '#fb7185', hair: '#5c2130', vibe: 'cool' },
  { key: 'codex',       name: 'Codex',       accent: '#a3e635', hair: '#3f4d16', vibe: 'sleepy' },
  { key: 'claudecode',  name: 'Claude Code', accent: '#818cf8', hair: '#2c2f52', vibe: 'bun' },
];

/** 工位布局：index 对应落座顺序。第 0、1 位是群主和管理员（靠前）。 */
export const DESKS = CHARACTERS.map((c, i) => ({
  ...c,
  index: i,
  x: 0.10 + (i % 4) * 0.225,
  y: 0.42 + Math.floor(i / 4) * 0.34,
}));

/** 任务表的一份内容。round 表示这是第几轮细化后的版本。 */
export function makeTaskTable(round, comments) {
  return {
    round,
    title: '把 mergeguard 的续跑判据从「标签」换成「事实」',
    items: [
      { id: 'T1', text: `_classify_pending_merge 增加第二条证据：MERGE_HEAD == rev-parse(请求分支)`, owner: null },
      { id: 'T2', text: `为「别人的待提交现场」补三条拒绝用例`, owner: null },
      { id: 'T3', text: `_run_gate 摘要取门禁自己说的「结果:」行`, owner: 'opencode', state: 'doing' },
      { id: 'T4', text: `docstate --verbose 的 UTF-8 崩溃固化进代码`, owner: 'opencode', state: 'done' },
      { id: 'T5', text: `仓库根 ruff 全量口径的 2 条既有问题`, owner: null },
    ].map((it) => (comments[it.id] ? { ...it, text: comments[it.id] } : it)),
  };
}

/** 群聊消息。kind 决定右侧手机里怎么渲染这条。 */
export function makeScript(rounds) {
  const m = [];
  const push = (from, text, kind = 'text') => m.push({ from, text, kind });

  push('群主', '新任务：mergeguard 续跑会认错人，把别人的合并当成自己的提交掉。优先级最高。', 'dispatch');
  push('管理员', '收到。我先细化成任务表，然后轮传给各位看。', 'text');

  for (let r = 1; r <= rounds; r += 1) {
    push('管理员', `第 ${r} 轮：任务表如下，请大家对照群主的原始需求提意见。`, 'table');
    push('OpenCode', r === 1 ? 'T2 的用例要覆盖「分支 tip 已前进」这个形态，否则过期现场漏掉。' : '这轮没问题。', 'comment');
    push('Qoder', r === 1 ? 'T5 是我之前踩的 ruff 口径问题，能并进来就一起做。' : '同意。', 'comment');
    push('TRAE', r === 1 ? 'T3 的摘要是绿灯的证据，建议单独一条，别混在 T2 里。' : '过。', 'comment');
    if (r < rounds) push('管理员', `第 ${r} 轮意见已收到，我改一版再传。`, 'text');
  }

  push('管理员', `轮完 ${rounds} 轮，任务表定稿。各位可以认领。`, 'publish');
  push('OpenCode', '我领 T3、T4。', 'claim');
  push('Qoder', '我领 T5。', 'claim');
  push('TRAE', 'T2 给我。', 'claim');
  push('OpenCode', 'T4 完成：UTF-8 固化进代码，四道门禁零环境变量全绿。', 'done');
  push('Qoder', 'T5 完成：ruff 全量口径 2 条既有问题清掉了。', 'done');
  push('OpenCode', 'T3 有点卡——摘要在结果行之后被吃掉的场景比想的多。', 'stuck');
  push('TRAE', '我来。', 'help');
  push('TRAE', '搞定，根因是门禁可能在结果后打印任何东西。', 'done');
  push('管理员', '全部完成，我来做代码审核。', 'review');
  push('管理员', '审核通过。本次合并落地。', 'pass');
  return m;
}

/**
 * 把一份脚本推进成「某一时刻的场景快照」。
 *
 * 这是本文件唯一出口：给定已播到第几条消息，返回每个角色的状态。
 * 渲染层每帧问一次，不持有时间轴 —— 这样「动画播多久」由渲染层说了算。
 */
export function sceneAt(script, upto) {
  const shown = script.slice(0, upto);
  const st = {};
  for (const c of CHARACTERS) st[c.key] = { state: 'idle', label: '' };

  // 从后往前找每个角色最后一条相关消息，那一条决定它当前的状态
  const lastOf = (who) => [...shown].reverse().find((x) => x.from === who);

  for (const c of CHARACTERS) {
    const mine = lastOf(c.key);
    if (!mine) continue;
    if (mine.kind === 'claim') { st[c.key] = { state: 'typing', label: '干活中' }; }
    else if (mine.kind === 'done') { st[c.key] = { state: 'done', label: '任务完成' }; }
    else if (mine.kind === 'stuck') { st[c.key] = { state: 'stuck', label: '卡住了' }; }
    else if (mine.kind === 'help') { st[c.key] = { state: 'helping', label: '赶去帮忙' }; }
    else if (mine.kind === 'comment') { st[c.key] = { state: 'idle', label: '看任务表' }; }
  }

  // 管理员最后一条决定审核态
  const coord = lastOf('coordinator');
  if (coord) {
    if (coord.kind === 'review') st.coordinator = { state: 'reviewing', label: '代码审核' };
    else if (coord.kind === 'table' || coord.kind === 'publish') st.coordinator = { state: 'typing', label: '细化任务表' };
  }

  // 摸鱼是随机点缀：只在有人 idle 且没有正事时偶尔出现
  for (const c of CHARACTERS) {
    if (st[c.key].state === 'idle' && Math.random() < 0.22) {
      const slack = Math.random();
      const kind = slack < 0.34 ? 'snack' : slack < 0.67 ? 'snoop' : 'sleep';
      st[c.key] = { state: kind, label: kind === 'snack' ? '偷吃零食' : kind === 'snoop' ? '偷看别的屏幕' : '偷偷睡觉' };
    }
  }
  return st;
}
