# OFFICE-UI-01

### 统一任务卡

任务编号：OFFICE-UI-01
负责 Harness：Codex
开始时间：2026-10-22 16:20 +08:00
工作区：.worktrees/codex/
依赖任务：无
真相源引用：R10 §19（群聊与上下文：@mention 是唯一的直达通道）、§22（群主只说目标，管理员细化并分派）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：done（已合入 main）
commit：`ae758b4`（office/ 进仓那一笔）；本次合入 main 的 merge commit 待回填
验证结果：见文末「验证结果」表（2026-10-24 在合并后的 main 上真跑复核）。
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

2026-10-24 合并 `agent/local/codex/OFFICE-UI-01/a1` 进 main 时，在**合并后的 main** 上
重新真跑复核（平台 8850 跑合并后的后端，办公室 4202 跑合并后的构建，真数据副本）：

| 门禁 | 结果 |
|---|---|
| `tools/docstate` | 98 项检查，0 失败 |
| `tools/discipline` | 44 项检查，0 失败（合并受控窗口内 rootworktree 一项会红：MERGE_HEAD 悬着而合并锁已释放；mergeguard 持锁续跑时该项自动放行，merge commit 落地后复测全绿） |
| `ruff check` / `ruff format --check` | 全绿（218 个文件已格式化） |
| `pytest`（全量） | 合并后 main 上全量跑：4 条红 —— 3 条 `test_gate_utf8` 是「合并未提交期间 discipline 正红」的连带现象（它断言 discipline 必须退出 0），门禁转绿后复测通过；1 条 `test_generic_adapter` 是 Pi 适配器合进来后通用适配器的基座表少 `pi`（已补 `pi` 并复测全绿）。**两笔合并都落地后的最终树上全量复跑：`PYTEST_EXIT=0`** |
| `office/` 构建 | `vite build` 通过：29 个模块，`main` 187.08 kB JS / 61.56 kB CSS，三个页面（`index.html` / `direct.html` / `outputs.html`）都在 |
| `office/scripts/verify-*.mjs` | **30 条中 27 通过**；3 条失败均为**环境依赖**（要 5197 端口的 Godot dev server；本机 ollama 没有 `qwen3:4b`；平台在册凭据窗口数 7 ≠ 断言的 9） |
| 真跑截图 | 大厅 / 办公室 / 群聊摘要 / 输出页 / **新增的私聊页**，均无 JS 报错；私聊页空状态是人话（「还没有私聊。发一条，只有你和管理员看得到。」） |

前端冒烟脚本**不在本仓门禁内**（CI 里没有 node），它们是本地冒烟；这一条已写进 `docs/dev-convention.md` 的盲区。

### 合入 main 时解的冲突（5 处）

| 文件 | 解法 |
|---|---|
| `backend/app/admin/worker.py` | 两个分支都留：`CANCEL_REF`（取消通知）与 `DIRECT_REF`（私聊自动回复）各带自己的 `continue` —— 冲突块外那行 `continue` 是共享的，只留一边会让另一边**永远不被调用** |
| `backend/app/pool/service.py` | 取 main 侧（`data_dir` 进发现缓存的键），但路径换成 codex 的 `pool_config_path()`：合并后的 import 块里已经没有 `POOL_CONFIG_PATH` 了，照抄 main 侧会 `NameError` |
| `backend/tests/test_api_events_messages.py` | 取 main 侧（带类型注解的 `def bodies(viewer: str \| None) -> list[str]:`） |
| `TASKS.md` | 三处任务表区块**两排都留**（只留 main 侧会丢掉 codex 自己 10 行在途登记） |
| `office/README.md` | 留 main 的大段，标题用 codex 那句更全的「## 与管理员对话：群里的与私聊」 |

另外补了一处**合并才暴露的语义缺口**：`backend/app/adapter/harnesses/generic.py` 的
`BUILTIN_BASES` 里没有 `pi`（codex 的分支基于 Pi 适配器合入之前的基点），而 main 已有
专门的 Pi 适配器类 → `test_generic_adapter.py` 断言「通用适配器要保护每一个自带类适配器的
基座」红。已把 `pi` 补进基座表（**没有放宽断言**），复测全绿。
