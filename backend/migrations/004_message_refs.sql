-- MVP-08 验收遗留 3b：消息 refs 落库。
--
-- 背景：docs/prep/MVP-08/openapi.yaml 的 post_message 请求体与 Message schema
-- 都声明 refs: string[]，但 001 的 task_messages 没有该列 —— POST 接收后
-- 只在响应里回显，从不持久化，GET 永远读不回，引用信息静默丢失。
--
-- 为什么直接 ALTER ADD COLUMN：只是加一列，SQLite 支持且无需重建表；
-- DEFAULT '[]' 让存量消息立即得到合法的空数组，与「未带 refs 的新消息」
-- 不可区分地一致。存储为 JSON TEXT，沿用 payload_json / content_json 惯例。

ALTER TABLE task_messages ADD COLUMN refs TEXT NOT NULL DEFAULT '[]';
