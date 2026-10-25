"""The single decision function for API-layer permissions (R10 §17, auth-layer).

Every refusal goes through :func:`decide`, so a denial carries one stable,
attributable ``resource:action`` key -- the reason ``permission_denied`` exists
(R10-fix-12). The table below mirrors R10 §17 row by row; where v1 does not
model an R10 role as a token subject, the fold is written down here rather
than left implicit:

* **Planner** folds into ``human`` (v1 has no planner subject; ``plan:submit``
  is the row that matters).
* **Verify Worker / Merge Worker** are process-side (sweeper family, runtime
  driver, mergeguard) and do not pass through the HTTP layer at all.
* **协作者** is a harness acting on the help face; its scope limit is the
  ``help-<n>`` branch rule (§14 naming), enforced at publish.
* Rows with no HTTP face in v1 (pin/unpin, delete_worktree, merge/integration
  write, read_secret) are deliberately absent -- no invented endpoints; the
  card records them as 出范围.

The approval/deny table maps 1:1 onto the matrix rows that DO have an HTTP
face; ``review:submit`` carries R10's "自己的 review lease" scope, which stays
a fact check inside the service (lease owner), not a second policy table.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.auth.tokens import Principal
from app.config import settings

HARNESS = "harness"
HUMAN = "human"

#: permission key -> subject types allowed to call it.
POLICY: dict[str, frozenset[str]] = {
    # --- R10 §17 adapter face (harness token) ---
    "harness:probe": frozenset({HARNESS}),
    "harness:capabilities": frozenset({HARNESS}),
    # 平台额度池：harness 拿着自己的 token 来借模型额度（自己的 provider 挂了的时候，
    # 从池子里换一份还活着的额度）。这是**加性**的平台面，2026-10-21 由平台所有者
    # 批准；它只发模型请求，不领活、不改任务归属、不代表任何 harness 说话。
    "harness:pool": frozenset({HARNESS}),
    "task:list_ready": frozenset({HARNESS}),
    "task:context": frozenset({HARNESS}),
    # claim/heartbeat: allowed for every harness role (§17 row).
    "task:claim": frozenset({HARNESS}),
    "task:heartbeat": frozenset({HARNESS}),
    # submit_result: 开发 Harness 自己的 attempt -- the "own attempt" scope is
    # the owner binding at the API layer plus the existing fencing facts.
    "task:submit_result": frozenset({HARNESS}),
    "task:publish_branch": frozenset({HARNESS}),
    "task:request_help": frozenset({HARNESS}),
    "task:ask_human": frozenset({HARNESS}),
    "task:report_blocked": frozenset({HARNESS}),
    "task:accept_help": frozenset({HARNESS}),
    "task:reject_help": frozenset({HARNESS}),
    "task:split": frozenset({HARNESS}),
    "task:release": frozenset({HARNESS}),
    "task:abandon": frozenset({HARNESS}),
    # --- R10 §17 review face (Reviewer is a harness subject) ---
    "review:claim": frozenset({HARNESS}),
    "review:submit": frozenset({HARNESS}),
    # R10 §17 also permits the human administrator/owner to submit reviews.
    "review:human_claim": frozenset({HUMAN}),
    "review:human_submit": frozenset({HUMAN}),
    # --- R10 §17 human face (人类管理员/负责人; Planner folded in) ---
    "plan:submit": frozenset({HUMAN}),
    "plan:approve": frozenset({HUMAN}),
    "plan:reject": frozenset({HUMAN}),
    "harness:register": frozenset({HUMAN}),
    "project:create": frozenset({HUMAN}),
    "group:create": frozenset({HUMAN}),
    "task:resume": frozenset({HUMAN}),
    "task:reopen": frozenset({HUMAN}),
    # 给一条尝试预算用完的活**再给一次机会**（R10 §8 的"人工豁免"）。
    # 这条边只有人能走：平台不给自己豁免，管理员窗口也不行。
    # 一次豁免只多给一次尝试（一条活只能带一次），并记在任务上（谁给的、什么时候）。
    # 决定行锚在任务最后一个事件上 —— 读历史的人能看到"在这个时间点上，人给了一次机会"。
    "task:attempt_budget_override": frozenset({HUMAN}),
    # release_escalated in §17 == /human/release here.
    "task:release_escalated": frozenset({HUMAN}),
    "task:adjudicate": frozenset({HUMAN}),
    "task:cancel": frozenset({HUMAN}),
    # --- R10 §17 分配与传阅面（R10-fix-13）---
    # 传阅（方案互审）。`circulation:start` 是 §39 的创建边（管理员发布草案+名单+轮数）。
    "circulation:start": frozenset({HUMAN}),
    "circulation:close": frozenset({HUMAN}),
    "circulation:revise": frozenset({HUMAN}),
    "circulation:complete": frozenset({HUMAN}),
    "circulation:cancel": frozenset({HUMAN}),
    "circulation:review": frozenset({HARNESS}),
    # 分配（谁来做）。申请与材料补充是候选本人动作；开轮/冻结/裁决是管理员动作。
    # `allocation:submit` 覆盖同一条边的三次本人动作：**提交 / 修改 / 撤回**自己的申请
    # （申请是"一个人一轮一份"，改口是改同一份而不是交第二份，所以三者同权）。
    "allocation:open": frozenset({HUMAN}),
    "allocation:close": frozenset({HUMAN}),
    "allocation:submit": frozenset({HARNESS}),
    "allocation:material": frozenset({HUMAN, HARNESS}),
    "allocation:decide": frozenset({HUMAN}),
    # 接单/拒绝是本人动作；撤销是管理员动作（且只覆盖尚未 Claim 的分配）。
    "assignment:accept": frozenset({HARNESS}),
    "assignment:decline": frozenset({HARNESS}),
    "assignment:revoke": frozenset({HUMAN}),
    # Claim 授权：矩阵写"仅当存在本人、该任务、已 accepted 且未失效的分配"。这一行是
    # §39 硬规则 2 的权限面 —— 语义事实仍在服务层查（有没有这份分配），这里只回答
    # "谁能问这个键"。
    "claim:authorized": frozenset({HUMAN, HARNESS}),
    # 用户**自己**的 API key：只有人能加、能点头、能删。
    #
    # 这是加性的一行（2026-10-23）：平台替用户保管他自己在办公室里加的
    # provider key，但平台**不自己花**它 —— 要用得用户点头。harness 主体
    # 拿不到这个面：借别人的钱必须是人的决定。
    "pool:user_keys": frozenset({HUMAN}),
    # 平台的"翻译"：把机器写的文本（失败原因、事件名、绝对路径、方案原文）
    # 翻成人话。只给**人** —— 让机器自己给自己的日志翻成好话，那是给日志化妆。
    # 纯加性的一行（2026-10-23）。
    "wording:read": frozenset({HUMAN}),
}

#: 已知、但**任何主体类型都拿不到**的键（管理员 harness 私有面，admin-harness）。
#:
#: 为什么单独一张表而不是在 POLICY 里放一个空集合：POLICY 是 §17 权限矩阵的逐行
#: 镜像，而矩阵里每一行至少有一个"允许"。放空集合会让镜像失去这个不变量
#: （`test_auth_matrix.py::test_policy_table_is_resource_action_shaped` 钉的就是它），
#: 而且"某个键谁都拿不到"和"某个键可以给某一类主体"是两种不同的声明，混在一起
#: 会让矩阵读起来像漏填。
#:
#: `admin:refine` = 需求细化：把群主的一句自然语言要求，经管理员 harness 自己的
#: 模型通道细化成结构化任务规格。它不写业务状态（写状态仍走 `plan:approve`）。
ADMIN_ONLY_KEYS: frozenset[str] = frozenset({"admin:refine"})

#: §17 把这几项给了"人类管理员/负责人"。部署里管理员是一个 **harness 主体**
#: （平台没有第三种主体类型），所以另开一张加性表：键必须同时满足
#: ①主体类型是 harness ②主体 id 在 settings.admin_subjects 白名单里。
#: 这是刻意的窄口子：默认白名单为空时，普通成员 harness 一个都拿不到。
ADMIN_HARNESS_KEYS: dict[str, frozenset[str]] = {
    # §17 的审核两行原本只给 harness；管理员 harness 就是一个 harness 主体，
    # 这里登记它，让“管理员领取审核 / 提交审核”在权限层成立。真正决定谁能审
    # 某条任务的是平台自己的 `review_policy`（默认 cross_provider），不是这张表：
    # 加这一行不会让管理员审同提供方成员的活，也不会让成员审别人的活。
    "review:claim": frozenset({HARNESS}),
    "review:submit": frozenset({HARNESS}),
    # 分派 = 批准计划并生成任务（§17 plan:approve）。
    "plan:approve": frozenset({HARNESS}),
    # 驳回计划与"分派"是同一次判断的两个方向（§17 第 426 行：`plan.reject` 由
    # "人类驳回并记录原因"）。只放行 approve、不放行 reject，会让管理员只能批不能
    # 退：一张不合要求的计划除了晾着没有别的出路。这条不新增权限键，只是把 §17
    # 里已经属于同一个人（人类管理员/负责人，见列头）的那两行都给到管理员 harness。
    "plan:reject": frozenset({HARNESS}),
    # 分派前必须先细化；细化与分派同属管理员动作，共用同一白名单。
    "admin:refine": frozenset({HARNESS}),
    # §17 里标着"拒绝（管理员 harness 经 AP_ADMIN_SUBJECTS 委托允许）"的分配面。
    # 逐条对应：传阅四步、分配开轮/冻结、裁决、撤销。
    "circulation:start": frozenset({HARNESS}),
    "circulation:close": frozenset({HARNESS}),
    "circulation:revise": frozenset({HARNESS}),
    "circulation:complete": frozenset({HARNESS}),
    "circulation:cancel": frozenset({HARNESS}),
    "allocation:open": frozenset({HARNESS}),
    "allocation:close": frozenset({HARNESS}),
    # `allocation:decide` 同时覆盖两条管理员动作用同一个键：**裁决**与**暂缓**
    # （`allocation.decision_deferred`）—— 两者都是"管理员对冻结后的候选集合做处置"，
    # 拆成两个键会让矩阵多出一行同权限的行。**改派邀请**（`assignment.invited`）也走
    # 这个键：邀请一个没申请的人同样是管理员挑人，回避要求由服务层查。
    "allocation:decide": frozenset({HARNESS}),
    "assignment:revoke": frozenset({HARNESS}),
    # 取消一条**还没有产出**的任务（`ready` / `queued` 等，见 human
    # `_CANCEL_STATES`）。§17 把 `task:cancel` 只写给人类管理员；现实里管理员是
    # 一个 harness 主体，没有这条委托，管理员既不能取消重复派发，也不能收口
    # “指派给同 provider、必然无法审核”的任务——平台助手只能把线索列出来。
    # 路由 `/tasks/{id}/cancel` 用 `_actor_type(principal)` 记录真实主体，
    # 所以管理员取消会记成 `harness/<管理员id>`，不会伪装成人类。
    "task:cancel": frozenset({HARNESS}),
}


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    key: str
    reason: str | None = None


def decide(principal: Principal, key: str) -> Decision:
    """The one place permission is granted or refused.

    Fail closed: an unknown key is a denial (``unknown_permission``), not an
    accidental allow -- a route wired to a key that was never registered must
    break loudly in tests, not silently in production.

    Three tables, checked in order:

    1. ``POLICY`` -- the §17 matrix by subject type;
    2. ``ADMIN_ONLY_KEYS`` -- known keys no subject type may reach by type;
    3. ``ADMIN_HARNESS_KEYS`` -- the additive admin-harness allowlist (harness
       subject *and* a configured subject id).

    A key in none of them is an unknown permission: the admin tables only widen
    keys the matrix already knows.
    """
    roles = POLICY.get(key)
    if roles is None and key not in ADMIN_ONLY_KEYS:
        return Decision(False, key, "unknown_permission")
    if roles is not None and principal.subject_type in roles:
        return Decision(True, key, None)
    admin_roles = ADMIN_HARNESS_KEYS.get(key)
    if (
        admin_roles is not None
        and principal.subject_type in admin_roles
        and principal.subject_id in settings.admin_subjects
    ):
        return Decision(True, key, None)
    return Decision(False, key, "role_not_allowed")
