-- R10 §39 窗口的模型选择（R10-fix-19 登记，见规范 §38.14）。
--
-- 人可以给某个窗口指定「用自带模型」还是「用额度池里的哪个模型」。这个选择落在
-- `harnesses`（窗口的登记）上，**不是**落在任务上：任务只管这一轮，窗口的设置是
-- 「这个窗口接下来怎么干活」。
--
-- 两条不变式：
--
--   1. `model_mode` 只有两个取值：`cli`（用这个窗口自己 CLI 配置里的模型，默认）
--      与 `pool`（走额度池）。别的一律不认 —— 平台不会猜，也不会替人选。
--   2. `model_id` 只有在 `model_mode='pool'` 时才有意义；它存的是**额度池里的模型
--      id**（原样存池子的 id，不另编一套号），所以界面能写回「哪家的什么模型」，
--      平台也能对回池子里那一条。
--
-- 「用户自己的 key」那一档（tier=user）**仍然要人点头**：这个选择不授予花钱的权力。
--
-- 不新增事件名、状态、状态迁移边 → 事件 `schema_version` 不动（§37.7 只对新增事件
-- 提升）。表的 schema 版本由本迁移的编号（017）承载。
ALTER TABLE harnesses ADD COLUMN model_mode TEXT NOT NULL DEFAULT 'cli';

ALTER TABLE harnesses ADD COLUMN model_id TEXT;

ALTER TABLE harnesses ADD COLUMN model_updated_at TEXT;
