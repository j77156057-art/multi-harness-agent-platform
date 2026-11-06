// 开工向导「管理员」候选的取数规则（P2-7）。
//
// 一句话：候选来自**平台注册表**，在每次打开向导时现读一次；不是页面加载时的
// 快照，也不是 project.members。
//
// 为什么不能用快照：`main.js` 在模块求值时只读一次 `/api/formal/status`。页面打开
// 之后才注册的窗口（2026-11-05 human-sim 里的 qoder 就是这样）不会进这份快照，
// 于是向导里只剩「网关配置的 ADMIN 身份 + 已有项目成员」，人看着像是新窗口根本没
// 注册成功。注册表是唯一权威来源，打开向导前必须再读一遍。
//
// 为什么不能用 project.members：那是「开工时定下的成员」，不是「平台上现在有
// 哪些窗口」。新窗口还没进任何项目，但它完全可以当这个项目的管理员
// （R10 里写明：左栏列的是平台上在册的全部窗口）。
//
// 保留条件（四条全满足）：
//   1. `registration_version` 非空 —— 是注册出来的窗口，不是网关内置的适配器模板；
//   2. `capabilities` 含 `coding` —— 管理员要能跑细化 CLI，不能跑的就别放进候选；
//   3. `credential_count` 不为 0 —— 登记在册但没有平台凭据的窗口，细化会失败、
//      派出去的活会永远停在 ready（网关把它算成"不能真的领活"）。
//      字段缺失按"有凭据"处理，兼容不返回该字段的旧载荷；显式 0 才排除。
//   4. 不是平台助手（office-admin）—— 它是模型身份，不是可以替项目做规划的外部窗口。
//
// 注意：id 以 `-admin` 结尾、或名字里带「管理员」的窗口**不排除**。它们就是外部
// 管理员窗口，挡在候选之外会让人没法换管理员。这一点与成员候选（`memberCandidates`）
// 的规则不同：管理员窗口不干活，当成员确实要排掉。
//
// 离线窗口**保留在列表里**：它是团队的一员，人需要看见它现在不在，而不是让它
// 从名单里凭空消失。
import { isPlatformAssistantHarness } from './harness-roles.js';

export function openingCandidateWindows(harnesses) {
  const seen = new Set();
  const windows = [];
  for (const item of Array.isArray(harnesses) ? harnesses : []) {
    const id = String(item?.harness_id || '').trim();
    if (!id || seen.has(id)) continue;
    if (!item.registration_version) continue;
    const capabilities = Array.isArray(item.capabilities) ? item.capabilities : [];
    if (!capabilities.includes('coding')) continue;
    if (Number(item.credential_count ?? 1) <= 0) continue;
    if (isPlatformAssistantHarness(item)) continue;
    seen.add(id);
    windows.push({ harness_id: id, name: item.name || id });
  }
  return windows;
}
