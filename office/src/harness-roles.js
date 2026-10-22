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
