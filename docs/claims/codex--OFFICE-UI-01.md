# 认领：OFFICE-UI-01

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-01` |
| Harness | Codex |
| 认领时间 | 2026-10-22 16:20 +08:00 |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | done（办公室的数据版界面已收进本仓 `office/`，已合入 main） |

## 做什么

把**办公室的数据版界面**（任务表、任务详情、额度池、群聊、与管理员对话、任务书）
从本机那���独立前端收进本仓 `office/`，并按仓库规矩登记：目录边界、README、冒烟脚本。

## 为什么

仓库此前的记录是"平台仓已无独立前端，UI 统一在 Agent Office（独立仓，4174）"
（`docs/dev-convention.md` 的目录边界与盲区两处）。但那套 UI 长期只存在于本机目录里：
改一次界面、审一次版式都没有版本记录，别的 AI 看不见、评审也复现不了。
前端既然是平台的观察面（任务、方案、额度、事件、群聊都从这里读），把它放在平台仓里
才谈得上"实现与真相源在一起"。

这次收进仓的是**数据版**界面：3D 场景（`docs/prototypes/office-anim/`）与手机外壳
本来就是小样，已经从界面里撤掉，不跟着进仓。

## 边界

- **不改平台行为**：没有新增事件名、状态、schema 或迁移边，因此不走 R10 §38 的
  R10-fix 条目，`schema_version` 不动。
- **办公室仍然是观察面**：界面只呈现平台事实，并提供**人自己的动作**（请求改派、
  请求预算豁免、请管理员派单、在群里说话）。派单、拆解、裁决、合并一律由管理员
  Harness 与成员自己走 API 完成，界面不代点。
- **前端自己的静态检查不进本仓门禁**：`scripts.sh check` 跑的是 docstate、discipline、
  ruff、pytest；`office/scripts/verify-*.mjs` 需要 node + playwright，CI 里没有 node，
  所以它是**本地冒烟**，不是门禁。这一点写进了 `docs/dev-convention.md` 的盲区一节。

## 完成标准（机械可判）

1. `office/` 下有可 `npm ci && npm run build` 的前端，且 `npm run build` 通过；
2. `office/scripts/verify-*.mjs` 在有在线平台时全部可跑（平台不可达的那几条属环境）；
3. `office/` 里不含 3D 场景、手机外壳与旧 mock 的死代码；
4. `docs/dev-convention.md` 的目录边界与盲区两处不再写"本仓不含前端"；
5. `./scripts.sh check` 三道门禁全绿。
