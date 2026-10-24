# OFFICE-UI-20

### 统一任务卡

任务编号：OFFICE-UI-20
负责 Harness：Mavis
开始时间：2026-10-24 21:40 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-19（收费要点头、清单折叠、探针、名册）
真相源引用：`backend/app/pool/sources.py`（额度槽的声明与名册）、
`backend/app/pool/routes.py`（`/pool/status`、`/pool/probe`）、
`backend/app/pool/inventory.py`（档位的中文说法）、`office/src/flat-office.js`（窗口页的
模型选择）；R10 §38（R10-fix 流程）；`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`9d1ca13`（清单按额度属主分组折叠 + 名册里没定档的能当场定档；R10 §38.16 R10-fix-21）
验证结果：见文末「验证结果」表。
test_id：MVP-UI-035

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-19 |
| test_id | `MVP-UI-035` |
| 允许修改 | `backend/app/pool/sources.py`、`backend/app/pool/routes.py`、`backend/app/pool/service.py`、`backend/tests/test_pool_cli_enumerate.py`、`backend/tests/test_harness_model_selection.py`、`office/src/flat-office.js`、`office/src/flat-office.css`、`office/scripts/verify-office-conversations.mjs`、`office/server/formal-dispatch-gateway.mjs`、`docs/claims/mavis--OFFICE-UI-20.md`、`docs/tasks/notes/OFFICE-UI-20.md`、`docs/traceability.md`、`docs/spec/0.3-R10.md`（§38.16 R10-fix-21）、`TASKS.md` |
| 禁止修改 | `backend/migrations/**`、`backend/app/tasks/**`、`backend/app/admin/worker.py`、`docs/dev-convention.md`、`tools/**`、其他 Harness 的工作区与分支 |

## 做什么

| 要什么 | 怎么做 |
|---|---|
| 清单按 harness 折叠 | 清单按**额度属主**分组（opencode-cli / codebuddy / qoder / codex / claude-provider …），每组默认收起，标题写「这组有几个可用 · 它还能用几个」 |
| 付费额度不止这点 | 名册里没定档的模型也摆出来（标明「没定档」），旁边给「标成免费 / 标成收费」两个按钮 |
| 定档要真能用 | 平台把声明写进 `data/pool/config.json`（沿用同一家的 argv 契约）→ 清缓存重探 → 它就多了一个带档位的可选项；标成收费的照样要点头 |

## 为什么

仓库主人：「截图里这个也太长了吧，怎么没有按照对应的 harness 进行折叠，而且付费额度大把，
不只有这点啊」。

## 怎么做到不出错

1. **只承认 CLI 自己说的**：定档只能给名册里有的模型。
2. **沿用同一家的调用约定**：argv 契约照抄那家现有的槽。
3. **档位仍是人的决定**：平台不猜；标成收费的照样要点头。
4. **写完立刻生效**：清缓存 → 重探 → 界面上立刻多一个带档位的可选项。

## 完成标准

1. 清单按 harness 分组、每组默认折叠，标题写清每组几个。
2. 名册里没定档的模型列出来，旁边能「标成免费 / 标成收费」。
3. 定档后平台真的提供了这个模型（免费档/付费档各一例），付费的仍要点头。
4. 断言通过；办公室冒烟无回归；构建通过；`./scripts.sh check` 全绿。
5. `docs/traceability.md` 登记 `MVP-UI-035`；R10 §38.16 R10-fix-21 与实现同一次提交。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_pool_cli_enumerate.py` | ok（12 条：定档只认名册里的模型 / 沿用同一家的调用约定 / 档位不认（只有 free·reserve·metered）/ 写声明落在**数据目录**而不是历史相对路径） |
| `office/scripts/verify-office-conversations.mjs` | ok（清单按额度属主分组、每组默认收起、qoder 的模型在自己那组、名册里没定档的列出来并能定档） |
| 真跑（8849 真平台 + 4174 预览） | 清单标题「9 家 · 28 个可用 · 50 个还没定档」；分组：codex（8）/ claude-provider（1）/ opencode（2）/ minimax（1）/ opencode-cli（9 · 34 个没定档）/ codebuddy（3 · 16 个没定档）/ qoder（2）/ mmx（1）/ 你自己的 key（1）；点「标成收费」→ 那组从「6 个可用」变成「7 个可用」，新模型标「付费」；页面无报错 |
| 办公室全部冒烟 | ok（30/33；3 条恒失败是环境依赖：Godot dev server、本机 ollama `qwen3:4b`、在册凭据窗口 7≠9） |
| `office/` 构建 | ok（29 个模块，main 209.17 kB JS） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、ruff 全绿 |
| `pytest`（全量） | `PYTEST_EXIT=0` |
