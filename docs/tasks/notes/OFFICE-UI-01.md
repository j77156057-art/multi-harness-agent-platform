# OFFICE-UI-01

### 统一任务卡

任务编号：OFFICE-UI-01
负责 Harness：Codex
开始时间：2026-10-22 16:20 +08:00
工作区：.worktrees/codex/
依赖任务：无
真相源引用：R10 §19（群聊与上下文：@mention 是唯一的直达通道）、§22（群主只说目标，管理员细化并分派）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：doing
test_id：MVP-UI-010 DOC-TRACE-001

| 项 | 内容 |
|---|---|
| 依赖 | 无 |
| test_id | `MVP-UI-010` `DOC-TRACE-001` |
| 允许修改 | `office/**`、`docs/claims/codex--OFFICE-UI-01.md`、`docs/tasks/notes/OFFICE-UI-01.md`、`docs/dev-convention.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/migrations/**`、生产项目素材与外部 Harness 工作区 |

## 做什么

把**办公室的数据版界面**从本机那一份独立前端收进本仓 `office/`：任务表、任务详情、
额度池、群聊、与管理员对话、任务书（活账）、成员动态表、成员求助。

同时把仓库里"本仓不含前端"的那句记录改掉，让目录边界与实际一致。

## 为什么

前端此前只存在于本机目录：改一次界面、审一次版式都没有版本记录，别的 AI 看不见，
评审也复现不了。它读的是平台的事实（任务、方案、额度、事件、群聊），放在平台仓里
才谈得上"实现与真相源在一起"。

进来的是**数据版**界面：3D 场景（`docs/prototypes/office-anim/`）与手机外壳本来就是
小样，已经从界面里撤掉，不跟着进仓。

## 边界

- **不改平台行为**：没有新增事件名、状态、schema 或迁移边，因此不走 R10 §38 的
  R10-fix 条目，`schema_version` 不动。
- **办公室仍然是观察面**：界面只呈现平台事实，并提供**人自己的动作**（请求改派、
  请求预算豁免、请管理员派单、在群里说话）。派单、拆解、裁决、合并一律由管理员
  Harness 与成员自己走 API 完成，界面不代点。
- **前端自己的静态检查不进本仓门禁**：`scripts.sh check` 跑的是 docstate、discipline、
  ruff、pytest；`office/scripts/verify-*.mjs` 需要 node + playwright，CI 里没有 node，
  所以它是**本地冒烟**，不是门禁。这一点写进 `docs/dev-convention.md` 的盲区一节。

## 完成标准

1. **能构建**：`office/` 下 `npm ci && npm run build` 通过，产物只有数据版界面用到的
   东西（不含 3D 场景、手机外壳与旧 mock 的死代码）。
2. **冒烟能跑**：`office/scripts/verify-*.mjs` 在有在线平台时全部可跑；其中不依赖平台的
   那几条在任何机器上都能跑（API 全拦截）。
3. **版式不塌**：`verify-office-layout.mjs` 在三档宽度下断言四块面板互不重叠、对话台
   内容不越界、详情文字不横向溢出、页面无横向滚动条。
4. **文档不撒谎**：`docs/dev-convention.md` 的目录边界与盲区两处不再写"本仓不含前端"；
   `office/README.md` 说清它是什么、怎么跑、读哪些接口。
5. **门禁全绿**：`./scripts.sh check`（docstate + discipline + ruff + pytest）通过。

## 验证结果

（待本分支收口）
