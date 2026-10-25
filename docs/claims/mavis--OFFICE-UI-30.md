# 认领：OFFICE-UI-30

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-30` |
| Harness | Mavis |
| 认领时间 | 2026-10-25 14:30 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-30/a1` |
| 状态 | doing |

目标：**平台助手能做「新 harness 的适配体检」，并给出声明草案。**

为什么：仓库主人圈了「助手的适配体检 + 声明草案」。上一张卡（OFFICE-UI-28）把
声明表接进办公室之后，**人**已经能照着接一个新 CLI；但「这个新 CLI 到底缺什么、
声明该怎么写」仍然散在代码与文档里 —— 助手是那个天天在看平台事实的角色，
让它能回答这件事，比让人自己去翻代码强。

范围：

- `backend/app/api/routes.py`：新读面 `GET /harnesses/bases`（**read 路由，无鉴权**，
  与其它读面同例）—— 声明出来的基座各带一份**缺口清单**（缺什么、为什么缺），
  外加内置那几家的名字。
- `backend/app/adapter/harnesses/generic.py`：缺口清单的算法（声明侧 + 池子侧，
  都只读、不 spawn CLI）。
- `backend/app/assistant/tools.py`：新工具 `harness.bases`（只读）。
- `backend/app/assistant/prompts.py`：工具清单 + 这件事的姿势与红线。

**不碰** `docs/spec/**`、不动 `backend/app/auth/**`（权限矩阵不动）、不写声明文件、
不动其他 Harness 的工作区与分支。

## 怎么做到不出错

1. **声明仍是受信输入**：助手**只出草案**，不写、不装声明文件（`generic.py` 的模块头
   写着「operator-owned and never written by the platform」）。它也不知道这个 CLI
   在**人那台机器**上装没装 —— 那一条必须说「未记上」。
2. **缺口清单只讲事实**：缺 `review_args`、缺 `tier`、池子侧没槽位 —— 都是能查到的，
   查不到就说查不到。
3. **H-8 已查**：codex 在途改 `backend/app/api/routes.py` 的第 11/188/345/407/720/863 行；
   本卡的新读面放在 read 区块（2000 行之后）→ 不重叠。
