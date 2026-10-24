# 认领：OFFICE-UI-20

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-20` |
| Harness | Mavis |
| 认领时间 | 2026-10-24 21:40 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-20/a1` |
| 状态 | doing |

目标：额度池的清单**按 harness 折叠**，并且**没定档的模型能当场定档**。

为什么：仓库主人看 UI-19 的截图后说：「截图里这个也太长了吧，怎么没有按照对应的 harness
进行折叠，而且付费额度大把，不只有这点啊」。

两个问题，事实核对：

1. **清单是按档排的**（免费 14 个连着排、付费 11 个连着排）—— 所以 qoder 的两条被埋在
   后面看不见（上一轮已经因此被误读成「没探全」）。按 **harness** 排才是一回事：谁家的
   额度、谁家的模型。
2. **付费额度确实远不止 11 个**：平台上只提供**声明过**的那些（codex 8 + claude-provider
   1 + opencode 2），而各家 CLI 自己说它还能跑的一大批（codebuddy 20 个、opencode 44 个）
   都在**名册**里、没有档位 —— 平台上给不了、界面上也只是一句「没给档位」。

范围：

- `backend/app/pool/sources.py`：写声明的接口（把某个名册里的模型定成免费/收费），
  声明要**沿用同一家的调用约定**（argv 契约照抄那家现有的槽）。
- `backend/app/pool/routes.py`：`POST /pool/declaration`（人可用；写完清缓存重探）。
- `office/src/flat-office.js`：清单按 harness 分组、每组默认折叠；名册里没定档的模型
  旁边给「标成免费 / 标成收费」两个按钮。
- `backend/tests/test_pool_cli_enumerate.py` + `office/scripts/verify-office-conversations.mjs`：
  断言。
- `docs/spec/0.3-R10.md`：§38.16 R10-fix-21（与实现同一次提交）。

**不碰** §10 状态机、§37.7 事件 allowlist（不新增事件名/状态/迁移边）、数据库 schema
（池子的声明是 `data/pool/config.json`，不是表 → 不需要迁移）。

## 怎么做到不出错

1. **只承认 CLI 自己说的**：定档只能给「名册里有的」模型 —— 平台不替 CLI 发明模型。
2. **沿用同一家的调用约定**：写声明时 argv 契约照抄那家现有的槽（不然新槽跑不起来）。
3. **档位仍是人的决定**：免费/收费由人点，平台不猜；标成收费的照样要点头才能用。
4. **写完立刻生效**：写声明 → 清发现缓存与名册缓存 → 重新探 → 界面上它就多了一个带档位
   的可选项。
