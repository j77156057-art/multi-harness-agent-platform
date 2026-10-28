// 注册表里同时有成员窗口和管理员窗口。管理员窗口的 instance_id 目前统一以
// `-admin` 结尾，显示名也带“管理员”；`office-admin` 还是平台助手的模型身份，
// 不是群主可选的外部管理员窗口。成员候选必须先排掉它们，避免同一个窗口既当
// 管理员又当普通成员。
export const PLATFORM_ASSISTANT_HARNESS_ID = 'office-admin';

export function isAdministratorHarness(item, adminIdentity = null) {
  const id = String(item?.harness_id || item?.id || '').trim();
  if (!id) return false;
  if (adminIdentity && id === adminIdentity) return true;
  if (/-admin$/i.test(id)) return true;
  return String(item?.name || '').includes('管理员');
}

export function isPlatformAssistantHarness(item) {
  return String(item?.harness_id || item?.id || '') === PLATFORM_ASSISTANT_HARNESS_ID;
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
