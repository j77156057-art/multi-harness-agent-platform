# OFFICE-UI-14

### 统一任务卡

任务编号：OFFICE-UI-14
负责 Harness：Mavis
开始时间：2026-10-24 16:30 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-12（左边改成会话列表）
真相源引用：`office/src/flat-office.js`（办公室界面）、`office/src/flat-office.css`（三栏布局）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done（实现完成，待合入 main）
commit：`32b3618`
验证结果：见文末「验证结果」表。
test_id：MVP-UI-029

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-12 |
| test_id | `MVP-UI-029` |
| 允许修改 | `office/src/flat-office.js`、`office/src/flat-office.css`、`office/scripts/verify-office-*.mjs`、`docs/claims/mavis--OFFICE-UI-14.md`、`docs/tasks/notes/OFFICE-UI-14.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/**`、`office/contributions/**`、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

**撤掉办公室底下那条群聊条。** 群聊已经是左栏的一个会话（「项目群聊」那一行还带着最后一条
消息的摘要），同一份消息不该摆两处。

## 为什么

仓库主人：「项目群聊左侧边栏已经有了，底下那一栏就不用了吧」。

## 怎么做到不出错

1. **不放松判据**：四条冒烟原来查的是「底条在不在 / 底条里有没有混进定向消息 / 底条内容越没越界」。
   入口换了，检查对象跟着换：先点开「项目群聊」会话，再查同一件事 —— 检查的**内容**没变弱。
2. **布局不留空行**：网格从「头 + 正文 + 底条」改成「头 + 正文」，正文区多出一整行高度
   （实测 700px → 837px），不是留下一个空壳。
3. **中间那栏里的群聊要像一张卡**：跟任务表/详情一样白底带边框。

## 完成标准

1. 办公室底下没有群聊条；正文区填满多出来的空间。
2. 从左栏点「项目群聊」→ 中间那栏铺开群消息并能发言。
3. 四条既有冒烟跟着改完且**判据的意图不变**；办公室全部冒烟无回归；构建通过；
   `./scripts.sh check` 全绿。
4. `docs/traceability.md` 登记 `MVP-UI-029`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-office-conversations.mjs` | ok |
| 真跑（8849 真平台 + 4174 预览） | 底部群聊条 0 个；正文区 837px；点左栏「项目群聊」→ 中间铺开 186 条群消息，输入框在位 |
| `office/` 构建 | ok（29 个模块，main 196.21 kB JS） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、ruff 全绿 |
| `pytest`（全量） | `PYTEST_EXIT=0`（本轮**没碰 `backend/**`**，后端与 main 逐字节相同） |
