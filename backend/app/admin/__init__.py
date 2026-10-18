"""管理员 Harness（admin-harness）：把群主的自然语言要求细化成任务规格。

角色模型（本部署的约定，卡内记录）：

* **群主/用户**：只用自然语言提要求，不填表单、不点分派、不点领取审核。
* **管理员 harness**：读要求 -> 用**自己的模型通道**细化成结构化任务规格 ->
  分派 -> 领取审核 -> 给逐条意见 -> 提交审核。
* **管理员也可以接活写代码，只是不推荐**（群主 2026-10-18 口径）：审核多个成员交上来
  的成果本身要时间，管理员再自己写会拖慢进度。平台**不拦截**——`task:claim` 本来就
  对所有 harness 开放，任何"管理员不能 claim"的守卫都算回归。

平台只有 ``human``/``harness`` 两种主体，所以"管理员"是一个受配置白名单
（``AP_ADMIN_SUBJECTS``）约束的 harness 主体，而不是第三种主体类型。

管理员**必须是窗口身份**（``<base>-member|window-<slot>``）：细化通道由这个身份解析
（``AdminService._model_channel``），解析不出 CLI 的身份会 fail closed。``office-admin``
这类模型身份因此不能当管理员——它是平台 Agent（界面叫「平台助手」）的模型身份，
不占 harness 槽位（见 ``docs/prep/PLATFORM-AGENT-MODEL-IDENTITY/R10-FIX-15-DRAFT.md``）。
"""

from app.admin.service import AdminHarnessError, AdminService, RefinedTask

__all__ = ["AdminHarnessError", "AdminService", "RefinedTask"]
