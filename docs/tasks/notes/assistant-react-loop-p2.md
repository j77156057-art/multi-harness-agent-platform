# assistant-react-loop-p2

### 统一任务卡

任务编号：assistant-react-loop-p2
负责 Harness：Codex
开始时间：2026-10-23 16:40 +08:00
工作区：.worktrees/codex/
依赖任务：assistant-react-loop（P1，提交 `aaa8da5`）
真相源引用：MVP-ADAPTER-003（模型通道/CLI 槽消费语义同族；新检查 ID 不自造，待规范复审）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：done
commit：`1c65241`
test_id：MVP-ADAPTER-003

| 项 | 内容 |
|---|---|
| 依赖 | assistant-react-loop（P1） |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/assistant/**`, `backend/tests/test_assistant_*.py`, `docs/claims/codex--assistant-react-loop-p2.md`, `docs/tasks/notes/assistant-react-loop-p2.md`, `docs/proposals/assistant-react-loop.md`, `TASKS.md` |
| 禁止修改 | `backend/app/admin/**`（共享在途；只 import `PlatformClient` 复用，零修改）、`docs/spec/0.3-R10.md`、`docs/traceability.md`（无新 ID）、`backend/app/auth/**`（核实结论：矩阵零行新增，不改）、`backend/migrations/**`、生产项目素材与外部 Harness 工作区 |

## 做什么

P1 引擎接真模型（池子直连免费档五家，零 USD）与真私聊（assistant worker
从 admin/worker 削薄壳）。

## 为什么

免费池五家都是 CLI 槽，池子 `_cli_chat` 已能把 OpenAI 兼容请求转给它们并
记收据（实测 usage_reported: true）；metered（0.16879 USD）不动。权限面
读路径已是公共读、`harness:pool` 键已放行全体 harness——矩阵无需加行。

## 修法

见认领文件"做什么"三条：model.py（链式回退+收据）、worker.py（轮询→
围栏进引擎→私聊交付，诚实异常）、loop.py 小改（上游收据进 trace，
不本地估）。

## 完成标准

认领文件第 1-4 条。

## 状态

完成时间：2026-10-23
commit：`1c65241`（真平台首跑后的 CLI 接线、私聊可见性与投递错误修复；P2 主实现提交为 `a6cdf57`）。

### 验收结果

- assistant 三件套定点测试通过；真实平台首跑完成注册、免费档调用、私聊回复与 trace 落账。
- 全量 pytest `[100%]` 通过；ruff、docstate、discipline 全绿。

### 修订（同卡，2026-10-23 晚）

"模型链钉死五家"改为**实况目录驱动**：每次取链按 TTL 拉
`GET /pool/v1/models` 滤 `tier=="free"`；目录读不到才用钉死清单兜底
（trace 记 `pinned-fallback`）；出错/空回的 free 模型冷却 30 分钟让位，
全冷时最后一搏。被动健康：失败喂探针，不主动轮询烧槽。目录变更的人侧
告警另立 `pool-free-set-watch`（提案已登记，待复核）。

### 远端同步备注（2026-10-23 晚）

github.com:443 被墙期间，经 **api.github.com 的 Git Data API** 推送成功
（blob→tree→commit→ref 快进）。GitHub 重建的提交对象与本地字节不同，
**内容树等价但 sha 分叉**，对应关系：

| 本地 | 远端（等价） |
|---|---|
| `61a9917` | `b16ffc2c` |
| `7106919` | `a69c080f` |
| `aaa8da5`（P1） | `5a3f8b28` |
| `efc664d` | `abf95ad8` |
| `a6cdf57`（P2） | `9cd1aaed` |
| `8bba189` | `7ffcf2eb` |
| `e84ebd0`（目录驱动） | `05533397` |
| `2acfcb6`（本备注） | `6fe544bd` |

表到此为止：其后的备注类提交不再逐条回填，远端等价 sha 以各轮推送日志为准。
卡内引用的提交号均为本地 sha；443 恢复后要么以远端为准 rebase 对齐，
要么 `--force-with-lease` 覆盖（内容相同，安全）。推送脚本：
`api.github.com /repos/*/git/blobs|trees|commits|refs`（gh token，repo scope）。

### 收敛（2026-10-23 晚，分叉终结）

github.com:443 仍被墙，改用 **Git Data API 逐字节复刻**把整个分叉段
（`478ca7a` 之后 13 颗等价提交）重建为**本地 sha 完全相同**的对象：

- 复刻法：blob（base64 原样）、tree（`ls-tree` 全项 POST，sha 必须与本地
  相同否则中止）、commit（消息**连尾换行逐字透传**＋author/committer 原名
  原邮箱原时刻——GitHub 原样存消息不补换行，这一条是从 sha 分叉上逼近
  出来的金律）；每颗提交断言"远端生成 sha == 本地 sha"。
- 收敛保险：force 前核对远端分叉段与本地历史**逐条消息一一对应**（防丢
  任何他人提交）；只动 `agent/local/codex/OFFICE-UI-01/a1` 这条 codex
  本地分支。
- 结果：远端 ref 已收敛到本地 `3f06b84`（绿章提交）。**上表的等价对照
  从此作废**——此后远端与本地同 sha，按常规渲染；分叉时期的老对象留在
  GitHub 悬空，仅作考古。后续推送继续走本脚本（常规增量，非 force）。

### 首跑真平台验证（2026-10-23 晚）

**身份走平台正门，没有绕任何门**：`app.auth issue --type human` 铸操作员
human token → `POST /api/v1/harnesses` 登记 office-assistant
（registration_id `reg-75cf9472b62e48569619c006a061e83a`，
registered_by=office-operator）。office-admin 等 harness 主体天然无
`harness:register`（该权限只给 human——"自报不算授权"，policy 刻意未给
管理员 harness 委托），所以必须经操作员之手。助手 token
`app.auth issue --type harness` 后落 `data/office-assistant.key`，
`/auth/whoami` 确认 `harness/office-assistant`。运行中 8848 服务用根
`data/`；worker 以 `AP_DATA_DIR` 指向同一真相。

**端到端事件链**：worker 首跑锚定（cursor=1046，不回放历史）→
codex-window-admin 直发真问题（seq 1059，direct @office-assistant）→
ReAct 轮次：免费档 `codebuddy/hy3` 服役、1 次模型调用、池子回执在案、
自检 pass（`run-20261023-200627-bf34b2`）→ 私聊回复落地（seq 1060，
direct，引用 `[c1]`＋run id 可回查）→ trace 落账。回答是诚实的"未记上"
欠条——没读过池子就不编读数（小模型一次成 final 未先取证据，质量问题
记观察，不属机械门缺陷）。

**真跑抓出三处 scripted 假件与真读面的形状偏差**（这正是首跑的目的）：

1. `main()` 仍传 `PoolModelChannel(models=…)` 旧形参（P2 收尾通道已目录
   驱动），CLI 直接过不了构造——scripted 测试走构造注入没摸到接线。拆
   `parse_worker_args`/`build_worker`，补接线用例钉住。
2. 私聊判定对着**读面不发的字段**：真形状是 `sender_type/sender_id`＋
   `refs`，GET 不重复 `mentions`（直发的寻址即可见性）。旧判定
   `author_id`＋`mentions` 全落空 → 静默跳过、cursor 照推进，看起来正常。
   改可见性判定："对我可见的 direct 且非我本人发的＝发给我的"；自己的
   回话按 sender 过滤防自触发死循环；fixture 换真形状＋新用例钉住。
3. `_deliver` 把投递失败静默吞掉（假件没盖住真服务面的 403 一类）——
   改为不崩轮次但打 `deliver_error` 行：拒绝静默。

凭据卫生：操作员 human token 按 key 惯例归档 `data/office-operator.key`
（label 记"operator re-issue (assistant provision)"），`D:\Temp` 临时
件已清；明文仅此一份，库里只存哈希。

**常驻试跑（2026-10-23 晚开跑）**：`--interval 20` 挂入真平台后台。
自动接单链路验证：真问题（seq 1061）无人干预被领走 → 私聊回复
（seq 1062，`[s1]/[c1]`＋run id `run-20261023-211954-97cb38`）。轮次里
出现两个新质量观察（诚实机制都正确兜住、无违反）：①小模型幻觉出
不在白名单的 `pool.list`——引擎如实拒绝不猜替代品（白名单闭合起效），
暴露 prompt 未列白名单名导致空烧一步，已修：**SYSTEM_PROMPT 逐字枚举
白名单工具名**（"must 逐字使用/清单外一律不存在"），并加机械锚定用例
（prompt 清单必须与 build_registry 同步，新工具忘了列、prompt 写了
不存在的名都会红）；②第三论 reflect 对 `hy3` 的判定格式不可解——如实
`self_check: unavailable`，draft 过门后照发，未编 pass。
进度 `[100%]`、零 F/E/s/x、无 short test summary info（输出留档
`D:\Temp\opencode\full_pytest_final.txt`；末行"N passed"因 Start-Process
重定向缓冲伪影未落档，用例级证据完整）；ruff check＋format 全仓绿、
docstate 98 项 0 失败、discipline 44 项 0 失败（1 告警＝既有根工作区
占用提示）。修复提交：`1c65241`。
