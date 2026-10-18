"""管理员 Harness（admin-harness）：把群主的自然语言要求细化成任务规格。

角色模型（本部署的约定，卡内记录）：

* **群主/用户**：只用自然语言提要求，不填表单、不点分派、不点领取审核。
* **管理员 harness**：读要求 -> 用**自己的模型通道**细化成结构化任务规格 ->
  分派 -> 领取审核 -> 给逐条意见 -> 提交审核。

平台只有 ``human``/``harness`` 两种主体，所以"管理员"是一个受配置白名单
（``AP_ADMIN_SUBJECTS``）约束的 harness 主体，而不是第三种主体类型。
"""

from app.admin.service import AdminHarnessError, AdminService, RefinedTask

__all__ = ["AdminHarnessError", "AdminService", "RefinedTask"]
