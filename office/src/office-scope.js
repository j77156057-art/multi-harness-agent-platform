/**
 * 这个界面现在说的是**哪个群**。
 *
 * 背景：项目现在真的在平台上（见 docs/notes/project-lifecycle.md），而一个 office 实例
 * 启动时只绑一个群。要让"切到另一个项目"真的生效，每个请求都得告诉网关它问的是哪个群 ——
 * 就是这一个头：`X-Office-Group`。
 *
 * 两条口径：
 *
 * 1. **没有就什么也不加**。老项目（本地对象，没有 `platformGroupId`）照旧由网关用启动时
 *    那个群 —— 默认行为一点不变，不给已经跑着的部署添麻烦。
 * 2. **只报 id，不带任何判断**。这个群能不能用，由网关去平台上核（存在、且项目不在
 *    回收站）。界面自己判断等于让浏览器决定权限。
 */

const PROJECTS_KEY = 'agent-office.projects.v1';
const ACTIVE_KEY = 'agent-office.active-project.v1';

/** 当前项目在平台上的群 id；没有（或读不到）就返回空串。 */
export function currentGroupId() {
  try {
    const active = localStorage.getItem(ACTIVE_KEY);
    if (!active) return '';
    const list = JSON.parse(localStorage.getItem(PROJECTS_KEY) || '[]');
    if (!Array.isArray(list)) return '';
    const hit = list.find((item) => item && item.id === active);
    return String((hit && hit.platformGroupId) || '').trim();
  } catch {
    return '';
  }
}

/** 把这个头并进已有的 headers（没有群就原样返回，不多加一个空头）。 */
export function withGroup(headers = {}) {
  const group = currentGroupId();
  return group ? { ...headers, 'X-Office-Group': group } : headers;
}
