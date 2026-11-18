// 注册表里同时有成员窗口和管理员窗口。管理员窗口的 instance_id 目前统一以
// `-admin` 结尾，显示名也带“管理员”。成员候选必须先排掉它们，避免同一个窗口既当
// 管理员又当普通成员。
// 助手在注册表里有两个 id：office-assistant 是 worker 实际登记的 harness id，
// office-admin 是它的模型身份（后端 AdminService 通道用后者）。两个都要排掉，
// 只排一个会把助手算成可执行成员（2026-11-17 真栈走查实测：可执行成员唯一一个是助手）。
const PLATFORM_ASSISTANT_HARNESS_IDS = ['office-admin', 'office-assistant'];

export function isAdministratorHarness(item, adminIdentity = null) {
  const id = String(item?.harness_id || item?.id || '').trim();
  if (!id) return false;
  if (adminIdentity && id === adminIdentity) return true;
  if (/-admin$/i.test(id)) return true;
  return String(item?.name || '').includes('管理员');
}

export function isPlatformAssistantHarness(item) {
  return PLATFORM_ASSISTANT_HARNESS_IDS.includes(String(item?.harness_id || item?.id || ''));
}

// 一个窗口属于哪一家 Harness（品牌）。
//
// 平台把"同一程序开出来的多个窗口"命名成 `<brand>-member-<n>` /
// `<brand>-window-<n>`，品牌本身也能直接当窗口用（这台机器上的 `codex`、
// `opencode` 就是这样）。所以品牌 = 去掉那个窗口后缀之后的名字；
// 没有后缀，它自己就是品牌。
//
// 为什么需要它：开屏的名单要按**品牌**报数量（「Qoder CLI CN ×2」），
// 而不是把 `qoder-member-1`、`qoder-member-2` 当成两个不同的东西摆出来 ——
// 那看起来像"有两个 Qoder"，其实是一家的两个窗口。
export function baseHarnessOf(id) {
  const value = String(id || '');
  const match = /^(.+?)-(?:member|window)-[A-Za-z0-9][A-Za-z0-9_-]*$/.exec(value);
  return match ? match[1] : value;
}

// 这个窗口是不是**自己加的实例**（而不是 CLI 基座本身）。
//
// 基座是 CLI 本身（``claude-code``、``codebuddy``……）；实例是同一个 CLI 再开
// 一个身份（``codebuddy-member-1``、``架构 · Arch``）—— 那是**人自己加的**，
// 加错了就该能删。
//
// 形状与后端 ``app.adapter.formal_worker.base_harness_id`` 接受的一致：前后端
// 对「这是不是一个实例」是同一个答案。
export function isInstanceWindow(id) {
  const value = String(id || '').trim();
  if (!value) return false;
  return /^(.+?)-(?:member|window)-[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/.test(value);
}
