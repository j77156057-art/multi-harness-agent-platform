### permission-matrix：权限矩阵设计输入（✅ done）

~~~text
任务编号：permission-matrix
负责人 Harness：Qoder
开始时间：2026-10-08 07:27
依赖任务：paperclip-buzz-source（✅ 553ee0f；机制核读报告）；
          paperclip-buzz-adr（✅ 8b6d98f；借鉴边界 ADR-DEV-003）
工作区：.worktrees/qoder/
真相源引用：R10 §17（权威权限矩阵本体：7 角色 × 11 操作 + 两条硬禁令，
            文档检查 ID = DOC-AUTH-001；"所有 403 或 ACL 拒绝都写审计事件"）；
            §14（help-<n> 分支命名与权限矩阵共享一套口径）；ADR-DEV-002
            （单账户 ACL 降级 = 现状）；学习机制出处：Paperclip 21
            `PERMISSION_KEYS`（`shared/src/constants.ts:1003-1026`）+
            `principal_permission_grants` schema + `access.ts` 决策入口 +
            `authorization.ts` 决策核心——已核读，代码零复制（本卡不改代码）
范围声明：
  - **本切片（纯文档，零代码）**：`docs/reports/permission-matrix-design-input.md`——
    ①三张表对位（R10 权威矩阵=验收尺 / Paperclip 键表=机制与词汇参考 / 本仓
    受控面清单）；②R10 矩阵逐行落点（每操作 → 我方 HTTP 面/进程面/无面，
    如实标注）；③Paperclip 机制三条可借鉴（`resource:action` 键命名法、
    grant=(principal×key×scope) 三元组、单点决策函数）与三条不照搬
    （company/board 概念、21 键清单、"审批流"形态）；④**缺口清单**（主体模型/
    403 审计事件名在 88 项白名单里不存在→认证任务需 R10-fix / OS ACL 恢复与
    ADR-DEV-002 的关系 / help 协作者面的分支口径 / read_secret 最小授权）；
    ⑤建议的认证任务切片（A 主体与令牌 / B API 层矩阵执行 / C OS ACL 恢复 /
    D 协作者与 secret 最小面），验收尺=DOC-AUTH-001 逐格。
  - **显式出范围**（记录不静默）：不实现任何认证代码（token/403/审计事件/
    ACL 全留给认证任务）；不写 ADR（决策属于认证任务开工时，参照
    ADR-DEV-004 的"实现卡内落 ADR"模式）；不改 R10 正文（审计事件名的
    R10-fix 由认证任务走复审流程）。
  - **零迁移、零代码、零新事件**。
允许修改目录：docs/reports/**（新报告）、docs/traceability.md（DOC-AUTH-001
            行右栏证据指针更新）、TASKS.md、
            docs/tasks/notes/permission-matrix.md、
            docs/claims/qoder--permission-matrix.md
禁止修改目录：backend/**（零代码）、frontend/**、docs/spec/**、tools/**
交付分支：agent/local/qoder/permission-matrix/a1
完成标准：
  1. 报告三层结构齐全：R10 矩阵逐行有"我方落点"；Paperclip 21 键逐键有
     映射或 N/A 判定；受控面清单与 `grep @adapter/@human/@review/@read/
     @management` 的实际路由表一一对应（不凭记忆写）
  2. 缺口清单每条可验收：①403 审计事件名在 R10 M/O/R 88 项白名单中不存在
     （已用 store.py 同款解析验证——`secret_scan_blocked` 是唯一近亲）；
     ②`insecure_single_account` 与 R10 §17 OS ACL 条款的差距写明；
     ③"单点决策函数"建议给出落点（现有 `guarded_write`/route 层）
  3. traceability 的 DOC-AUTH-001 行右栏更新为指向本报告（保持 ⚠️ 仅声明
     ——没有机械断言），并在报告尾部声明"未逐行核读 Paperclip 打分器"
  4. `PYTHONUTF8=1 ./scripts.sh check` 全绿（docstate + discipline；本卡无
     代码，pytest 数不变）
test_id：DOC-AUTH-001（traceability 已有行；本卡为其设计输入）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 07:42
commit：`a968ac7`（实现提交——本卡为纯文档交付；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（118 files already formatted）
  pytest      : 443 passed（本卡零代码，计数不变），exit 0
  关键证据     : ①R10 矩阵 7×11 逐行落点（`grep @adapter/@human/@review/@read/
                @management` 实测路由表逐一对应；pin/delete_worktree/read_secret
                三行**核实为无面**——grep 无对应端点，如实标注不硬造映射）；
                ② **403 审计事件名缺口**用 store.py 同款解析器验证：R10 M/O/R
                三块 88 项白名单中无权限类事件，唯一近亲 secret_scan_blocked
                （认证任务需先走 R10-fix）；③Paperclip 21 键照录
                （constants.ts:1003-1026 逐项核对）＋ grants schema 与决策
                入口核读；④受控面清单（adapter 16、human 5+ask_human、review 2、
                read 全族、management 5、进程面 5 族）与代码一一对应。
  声明         : Paperclip 打分器（authorization.ts 核心裁决）未逐行读——
                报告内已如实声明；本报告不构成 ADR。
已知问题：
  - 本卡为**设计输入**，不含任何认证实现（token/403/审计事件/OS ACL 全留
    认证任务）；实现后 DOC-AUTH-001 升"✅ 有断言"。
  - 报告 §4 缺口 5 的"进程面主体口径"是**建议**（进程面=调度中心主体、
    人类工具=人类主体），最终结论留给认证任务。
~~~
