-- buzz-bridge（ADR-DEV-005）：出站发布游标。
--
-- 形状直接抄 `alert_cursors`（009）——同一个「某条流推进到哪了」的模式，
-- 不发明新形状。stream 键 = 'buzz:<channel>'（预算频道级隔离；一个频道一行，
-- 将来多频道各自水位互不干扰）。
--
-- 语义（ADR-DEV-005 决策 6）：游标只在整批发布成功后推进；推进与发布之间
-- 失败 = 下一轮重发同一批 —— 至少一次投递，Nostr 事件 id（内容哈希）在
-- relay 侧天然去重，两者合起来≈恰好一次。此表不被任何权威写路径触碰。

CREATE TABLE IF NOT EXISTS buzz_cursors (
    stream TEXT PRIMARY KEY,
    cursor_seq INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
