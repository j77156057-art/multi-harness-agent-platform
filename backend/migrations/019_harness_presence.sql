-- 窗口的**在场**事实：进程还在不在跑。
--
-- 登记表（harness_registrations）里的 status 是**注册那一刻**写下的声明：
-- 窗口自己报「我上线了」，平台把它记下来。它不会因为进程被关掉而变 ——
-- 于是「13 个窗口在线」可能一个都没在跑（2026-10-29 的真实演练就是这样：
-- 注册表 13 个，在跑的 worker 只有 7 个）。
--
-- 这里记的是**另一件事**，两者不能混：
--   · 注册状态 —— 这个窗口**存在**，并且当初声明能干活（登记面）；
--   · 在场     —— 这个窗口的**进程现在还活着**（观察面）。
--
-- worker 定期来说一声「我还在」（POST /adapter/presence）；超过窗口没来
-- 就当它不在线。表只存一行一个窗口，不进事件流。
--
-- 不新增事件名、状态、状态迁移边 → 事件 schema_version 不动。表的 schema
-- 版本由本迁移的编号（019）承载。
CREATE TABLE IF NOT EXISTS harness_presence (
  harness_id TEXT PRIMARY KEY,
  role TEXT,
  started_at TEXT NOT NULL,
  seen_at TEXT NOT NULL
);
