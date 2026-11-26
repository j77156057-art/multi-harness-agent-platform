# 项目隔离现状与处置（2026-11-26）

这一页回答一个问题：**两个项目之间的历史会不会互相串进来。**
结论先说：数据层不串；界面层曾经串（已修）；交付树与 CLI 历史是"共享但有边界"，
边界在哪写在下面，以及每一条怎么复核。

## 0. 结论表

| 面 | 现状 | 处置 |
|---|---|---|
| 数据库（任务/方案/轮次/消息/分配/评审） | 按 `group_id` 分区，跨组引用 0 条 | 无需改动 |
| 界面状态（详情、筛选、草稿） | 曾经串项目（已实测复现） | 已修：切项目重置 + 存储带 `__scope` |
| 交付树 `canonical.git` | 所有项目共用一条 `integration` | 分支按群命名；worktree 改为按群分目录 |
| CLI 会话与记忆 | 按工作目录/项目分；无全局指令文件 | 无需改动（见 §2 的证据） |
| 方案导出 `exports/plans/` | 曾经全项目平铺 | 已改：按群分目录 |
| CLI **配置**（provider/凭据） | 全局共享 | **刻意如此**：池子的上游就是从这些配置里发现的 |

## 1. 交付树（canonical.git）

- 75 个 ref，全部形如 `agent/<group>/<harness>/<task>/<attempt>` —— 分支名自带群标识。
- 只有一条集成线 `integration`；平台自己的代码与各项目的产物在同一棵树里
  （`portfolio/` 就是任务产物）。**这是设计**：一个仓库一条集成线，任务之间靠
  `exclusive_paths` 划界，不靠目录。
- 2026-11-26 起工作树放在 `wt/<group>/<id>`（原来是 `wt/<id>` 平铺二十几个）。
  物理 id 仍是 4 位，长任务号不会把路径撑长（`test_long_task_ids_do_not_lengthen_the_physical_path`）。
- 连带修好两处：孤儿扫描要认"分组目录"这一层；回收时 gitdir 跟工作树是**兄弟**
  （都在 `wt/<group>/` 下），按根目录拼会漏删。

若要按项目彻底分开（每个项目一棵裸库）：把 `CanonicalRepo.from_settings()` 这一处
收口改成按 project 选库，claim / publish / merge / runtime 跟着走。这是大改，
等"要接第二个真实客户 / 要单项目交付"时再做。

## 2. CLI 会话与记忆

查了什么、结论是什么：

| 检查 | 结果 |
|---|---|
| `~/.claude/CLAUDE.md`（全局指令） | 不存在 |
| `~/.codex/AGENTS.md` | 存在但 0 字节 |
| `~/.config/opencode/AGENTS.md`、`~/.qoder/AGENTS.md` | 不存在 |
| claude 会话目录 | `~/.claude/projects/<cwd-slug>/`，当前 130 个 —— **按工作目录分** |
| opencode 存储 | `~/.local/share/opencode/`（`opencode.db` + `repos/`），按 project 分 |
| 各 adapter 是否续用旧会话 | `claude-code` / `opencode` / `qoder` / `codebuddy` / `codex` 都没有 `--continue` / `--resume` 一类参数 |

已有两处显式隔离：`PiAdapter`（`PI_CODING_AGENT_DIR` 指到 per-attempt 临时目录，
用完删除）、`CodexAdapter`（`process_environment`）。

**没有改代码**。理由：会话本来就按工作目录分，而工作树路径（`wt/<group>/<id>`）
已经是项目唯一的；再加一层"每 attempt 复制一份配置"会把凭据拷到临时目录 ——
那是新开一个泄露面，换来的收益为零。

**配置共享是刻意的**：池子的上游（`opencode:1`、`opencode:minimax`、`claude-provider`…）
就是从这些 CLI 自己的配置文件里发现的。隔离掉配置 = 池子看不见上游。

## 3. 方案导出

`backend/app/review_doc.py`：`exports/plans/<group_id>/plan-<plan_id>-r<N>.md|.html`。

- 旧记录仍是旧路径（DB 存绝对路径），不用迁移；新方案进新目录。
- 群标识过一遍白名单（`_safe_dir_name`），防止手填群名带 `..` 写到 exports 之外。

## 4. 复核命令

```bash
# 跨组引用是否为零（应在 7 条关系上全部 0）
python - <<'PY'
import sqlite3
c = sqlite3.connect('data/state.db')
print(c.execute("""
 SELECT count(*) FROM tasks t
 JOIN plan_snapshots s ON s.snapshot_id = t.plan_snapshot_id
 JOIN plans p ON p.plan_id = s.plan_id
 WHERE t.group_id <> p.group_id""").fetchone()[0])
PY

# 工作树是否按群分目录
ls wt/

# 新导出的方案是否进群目录
ls data/exports/plans/collab-big-g1/ | head

# 界面隔离（需要 office 预览在跑）
#   进项目 A 点一条任务 → 切项目 B：详情应为空、输入框应为空
```
