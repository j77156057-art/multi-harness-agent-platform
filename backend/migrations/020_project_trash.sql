-- 项目与群：**早就在平台上**，界面却一直把它们当浏览器本地对象。
--
-- 这次把两边接上，顺手补三件此前没有的事：
--
--   1. 每个项目有自己的 id（界面新建时随机生成），名字只是给人看的标签 ——
--      改名不动 id，所以改名不会断掉任务、群、事件与已合入的提交。
--   2. 移除不是删除：`trashed_at` 记下"什么时候进的回收站"，一周内可以找回。
--   3. 到期之后由清扫把它彻底删掉（删到哪一层见 `app/projects/service.py`，
--      那里写清了边界：事件流是 append-only，不做物理删除）。
--
-- 只加列，不改既有行：老项目 `trashed_at` 为 NULL = 不在回收站。
-- 不新增事件名、状态、状态迁移边 → 事件 schema_version 不动；表的 schema 版本
-- 由本迁移的编号（020）承载。
ALTER TABLE projects ADD COLUMN trashed_at TEXT;

-- 回收站那一列会被清扫按时间扫，给它一个索引；`WHERE trashed_at IS NOT NULL`
-- 只命中很小一部分行，但清扫是定期跑的，不该每轮全表扫。
CREATE INDEX IF NOT EXISTS idx_projects_trashed_at ON projects(trashed_at);
