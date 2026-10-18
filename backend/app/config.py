"""应用配置。

约定：所有配置项都能从环境变量读取，且有安全默认值。
禁止在代码里硬编码路径、端口、账号。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 项目根（含 docs/ tools/ backend/ 的那一层）
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """MVP 阶段配置。字段全部只读，改动需走 PR。"""

    def __init__(self) -> None:
        # --- 服务 ---
        self.host: str = os.environ.get("AP_HOST", "127.0.0.1")
        # R10 §5：v1 REST/WS 默认只绑定 localhost。改0.0.0.0 属于安全边界变更，必须重新评审
        self.port: int = int(os.environ.get("AP_PORT", "8848"))

        # --- 存储 ---
        self.data_dir: Path = Path(os.environ.get("AP_DATA_DIR", str(PROJECT_ROOT / "data")))
        self.db_path: Path = self.data_dir / "state.db"
        # R10 §15：事件 JSONL 异步导出的落点（导出器只写文件，SQLite 仍是唯一事实源）
        self.export_dir: Path = Path(
            os.environ.get("AP_EXPORT_DIR", str(self.data_dir / "exports"))
        )
        # R10 §23：Windows 下建议排除 Defender 对 state.db/WAL/SHM 的实时锁定
        self.sqlite_busy_timeout_ms: int = int(os.environ.get("AP_SQLITE_BUSY_TIMEOUT", "5000"))
        # R10 §23：20GB 触发 PostgreSQL 迁移评估（ADR-007）
        self.sqlite_max_bytes: int = int(os.environ.get("AP_SQLITE_MAX_BYTES", str(20 * 1024**3)))

        # --- 规范 ---
        self.spec_path: Path = Path(
            os.environ.get("AP_SPEC_PATH", str(PROJECT_ROOT / "docs" / "spec" / "0.3-R10.md"))
        )
        # 是否在启动时跑 docstate 检查（R10 §29.3 阶段 0 通过条件）
        self.check_spec_on_startup: bool = _env_bool("AP_CHECK_SPEC", True)

        # --- 安全（MVP 简化，见清单 MVP-05）---
        # R10 §13 完整方案是三账户隔离；MVP 阶段降级为单账户
        self.insecure_single_account: bool = _env_bool("AP_INSECURE_SINGLE_ACCOUNT", True)

        # --- 调度默认值（R10 §13）---
        self.lease_seconds: int = int(os.environ.get("AP_LEASE_SECONDS", "900"))  # 15 min
        self.heartbeat_seconds: int = int(os.environ.get("AP_HEARTBEAT_SECONDS", "300"))  # 5 min
        self.stale_grace_misses: int = int(os.environ.get("AP_STALE_GRACE", "3"))
        self.max_attempts: int = int(os.environ.get("AP_MAX_ATTEMPTS", "3"))
        self.max_review_rounds: int = int(os.environ.get("AP_MAX_REVIEW_ROUNDS", "2"))

        # --- 巡检器家族（docs/proposals/sweeper-family.md §4；参数集中）---
        self.sweeper_enabled: bool = _env_bool("AP_SWEEPER_ENABLED", True)
        self.sweeper_interval_seconds: int = int(os.environ.get("AP_SWEEPER_INTERVAL", "60"))
        self.aging_multiplier: float = float(os.environ.get("AP_AGING_MULTIPLIER", "2"))
        self.block_sla_minutes: int = int(os.environ.get("AP_BLOCK_SLA_MINUTES", "60"))
        self.review_wait_sla_minutes: int = int(os.environ.get("AP_REVIEW_WAIT_SLA_MINUTES", "60"))
        self.reclaim_grace_minutes: int = int(os.environ.get("AP_RECLAIM_GRACE_MINUTES", "10"))
        self.stuck_sweeps: int = int(os.environ.get("AP_STUCK_SWEEPS", "3"))
        self.stuck_human_sweeps: int = int(os.environ.get("AP_STUCK_HUMAN_SWEEPS", "5"))
        self.escalation_backoff_multiplier: float = float(
            os.environ.get("AP_ESCALATION_BACKOFF", "2")
        )
        self.reminder_cap_minutes: int = int(os.environ.get("AP_REMINDER_CAP_MINUTES", "240"))
        # block_timeout 的上游时长求和用：快照里 expected_duration_min 缺省/为 0 时按此计
        self.expected_duration_default_minutes: int = int(
            os.environ.get("AP_EXPECTED_DURATION_DEFAULT", "90")
        )

        # --- 出站告警（alert-webhook；机制参考见 docs/reports/paperclip-buzz-source-review.md，原生实现）---
        # 未配置 URL 时整个子系统显式 no-op；配了就要求 secret 做 HMAC 签名。
        self.alert_webhook_url: str | None = os.environ.get("AP_ALERT_WEBHOOK_URL") or None
        self.alert_webhook_secret: str = os.environ.get("AP_ALERT_WEBHOOK_SECRET", "")
        self.alert_timeout_seconds: int = int(os.environ.get("AP_ALERT_TIMEOUT", "10"))
        self.alert_max_attempts: int = int(os.environ.get("AP_ALERT_MAX_ATTEMPTS", "5"))

        # --- 预算护栏（budget-guard；机制核读自 Paperclip budget-reservations，原生实现）---
        # 任务声明 budget_tokens>0 才启用；未声明（0/缺）不启用——向后兼容。
        # 预留额=单次 attempt 的估算持有量（Paperclip reservationCents 的对应物），
        # 超过任务预算时封顶到预算（避免"估算大于预算 ⇒ 永不可跑"的死角）。
        self.budget_reservation_tokens: int = int(
            os.environ.get("AP_BUDGET_RESERVATION_TOKENS", "50000")
        )
        # committed/limit 达到该百分比即开 warning 事故（含界）。
        self.budget_warn_percent: int = int(os.environ.get("AP_BUDGET_WARN_PERCENT", "80"))

        # --- Buzz 单向桥（ADR-DEV-005；只写不读，Buzz 不参与任何状态判定）---
        # 三项齐全才启用；三项全空=显式 no-op（同 alerts 先例）；**部分配置=拒绝**
        # （改了一半静默退化最危险，桥在运行时报 BuzzConfigError，fail-closed）。
        # key 文件 = 受控凭据（64 位 hex secp256k1 私钥）：不进库/不进事件/不进日志。
        self.buzz_relay_ws: str | None = os.environ.get("AP_BUZZ_RELAY_WS") or None
        self.buzz_channel: str | None = os.environ.get("AP_BUZZ_CHANNEL") or None
        self.buzz_key_file: Path | None = (
            Path(os.environ["AP_BUZZ_KEY_FILE"])
            if os.environ.get("AP_BUZZ_KEY_FILE", "").strip()
            else None
        )
        self.buzz_interval_seconds: int = int(os.environ.get("AP_BUZZ_INTERVAL", "10"))
        self.buzz_timeout_seconds: int = int(os.environ.get("AP_BUZZ_TIMEOUT", "10"))

        # R10 §16
        self.verify_lease_seconds: int = int(os.environ.get("AP_VERIFY_LEASE", "1800"))  # 30 min
        self.review_lease_seconds: int = int(os.environ.get("AP_REVIEW_LEASE", "1200"))  # 20 min
        self.merge_lease_seconds: int = int(os.environ.get("AP_MERGE_LEASE", "1800"))  # 30 min
        self.verify_queue_max: int = int(os.environ.get("AP_VERIFY_QUEUE_MAX", "32"))  # ADR-030
        self.verify_queue_sla_minutes: int = int(os.environ.get("AP_VERIFY_QUEUE_SLA", "30"))
        self.concurrent_verify: int = int(os.environ.get("AP_CONCURRENT_VERIFY", "3"))  # ADR-013
        self.concurrent_review: int = int(os.environ.get("AP_CONCURRENT_REVIEW", "5"))
        self.concurrent_worktree: int = int(os.environ.get("AP_CONCURRENT_WORKTREE", "8"))
        self.max_verify_env_retries: int = int(os.environ.get("AP_VERIFY_ENV_RETRIES", "2"))

        # R10 §21
        self.integration_test_sla_minutes: int = int(
            os.environ.get("AP_INTEGRATION_TEST_SLA", "60")
        )
        self.integration_test_timeout_minutes: int = int(
            os.environ.get("AP_INTEGRATION_TEST_TIMEOUT", "30")
        )
        self.merge_queue_max_length: int = int(os.environ.get("AP_MERGE_QUEUE_MAX", "50"))
        # R10 §21：hotfix 抢占配额
        self.hotfix_preempt_per_30min: int = int(os.environ.get("AP_HOTFIX_PREEMPT", "3"))

        # --- 运行环（runtime-driver；F6 运行环 v1）---
        # 未设置时 RuntimeDriver 用仓库侧 verify-profiles.json（DEFAULT_POLICY_PATH）；
        # 指向一个不存在的文件=拒绝执行（不猜命令，R10 §16）。
        self.runtime_policy_file: Path | None = (
            Path(os.environ["AP_RUNTIME_POLICY_FILE"])
            if os.environ.get("AP_RUNTIME_POLICY_FILE", "").strip()
            else None
        )
        self.runtime_interval_seconds: int = int(os.environ.get("AP_RUNTIME_INTERVAL", "10"))

        # --- Git（R10 §13）---
        self.canonical_repo: Path = Path(
            os.environ.get("AP_CANONICAL_REPO", str(PROJECT_ROOT / "canonical.git"))
        )
        # 物理目录用短 ID，避免 Windows 长路径问题（R10 §14）
        self.worktree_root: Path = Path(
            os.environ.get("AP_WORKTREE_ROOT", str(PROJECT_ROOT / "wt"))
        )

        # --- 人类等待（R10 §18）---
        self.human_reminder_minutes: int = int(os.environ.get("AP_HUMAN_REMINDER", "30"))

        # --- 认证（auth-layer；R10 §17 API 层矩阵执行）---
        # off（默认）=零行为变化：既有套件/演练/前端都不动；enforce=主体令牌与
        # 权限矩阵生效。推广 enforce 之前必须先完成前端 token 接线（卡内披露）。
        # 未知值直接拒绝启动（fail-closed，防手滑拼错后静默退化成 off）。
        self.auth_mode: str = os.environ.get("AP_AUTH_MODE", "off").strip().lower()
        if self.auth_mode not in {"off", "enforce"}:
            raise ValueError(f"AP_AUTH_MODE must be 'off' or 'enforce', got {self.auth_mode!r}")

        # --- 分配授权门禁（R10 §39 硬规则 2；群主 2026-10-17 选定"分群开关"）---
        # §39 硬规则 2 的字面口径是"只有已 accepted 的分配才构成 §11 Claim 的授权"。
        # 全局强制会让存量任务全部不可领取（见 R10-FIX-13-DRAFT §10），所以按**群**开关：
        # 列在这里的 group_id 强制"先有分配才能 Claim"，其余群保持既有行为。
        # 默认空 = 一个群都不强制（零行为变化，不猜）；空值不等于放宽规则，
        # 规则本身仍在，只是没有群被纳入强制范围——纳入哪个群是显式配置。
        self.allocation_required_groups: frozenset[str] = frozenset(
            item.strip()
            for item in os.environ.get("AP_ALLOCATION_REQUIRED_GROUPS", "").split(",")
            if item.strip()
        )

        # --- 管理员 Harness（admin-harness；R10 §17 人类管理员侧的 harness 化）---
        # 平台只有 human/harness 两种主体，主体类型无法区分"管理员 harness"与
        # "普通成员 harness"。管理员身份因此用**受配置的主体白名单**表达：只有列在
        # AP_ADMIN_SUBJECTS 里的 harness 主体才能调用 §17 中本属人类管理员的那几项
        # 权限（见 app/auth/policy.py 的 ADMIN_HARNESS_KEYS）。
        # 默认空 = 没有任何 harness 拥有管理员权限（fail-closed，不猜）。
        self.admin_subjects: frozenset[str] = frozenset(
            item.strip()
            for item in os.environ.get("AP_ADMIN_SUBJECTS", "").split(",")
            if item.strip()
        )
        # 细化是一次真实的模型调用，落在一个独立空目录里（不是任何任务的
        # worktree），免得管理员的模型动到成员的工作区。
        self.admin_scratch_dir: Path = Path(
            os.environ.get("AP_ADMIN_SCRATCH", str(self.data_dir / "admin-scratch"))
        )
        self.admin_refine_timeout_seconds: int = int(
            os.environ.get("AP_ADMIN_REFINE_TIMEOUT", "600")
        )

        # --- Harness CLI（adapter-hardening；drill F3/F4）---
        # codex 沙箱：auto=按平台（nt→danger-full-access：Windows 上 codex 自带
        # 的 workspace-write 沙箱起不了 shell，drill F3 实证；posix→--full-auto）。
        # 显式覆盖留给 codex 修好沙箱后的部署；未知值拒绝启动。
        self.codex_sandbox: str = os.environ.get("AP_CODEX_SANDBOX", "auto").strip().lower()
        if self.codex_sandbox not in {"auto", "workspace-write", "danger-full-access"}:
            raise ValueError(
                "AP_CODEX_SANDBOX must be 'auto', 'workspace-write' or"
                f" 'danger-full-access', got {self.codex_sandbox!r}"
            )
        # opencode 启动：auto=nt 且 bash 可用时经壳（drill F4：python 直启不稳，
        # bash 壳稳定）；bash/direct 为显式覆盖；未知值拒绝启动。
        self.opencode_launch: str = os.environ.get("AP_OPENCODE_LAUNCH", "auto").strip().lower()
        if self.opencode_launch not in {"auto", "bash", "direct"}:
            raise ValueError(
                f"AP_OPENCODE_LAUNCH must be 'auto', 'bash' or 'direct', got {self.opencode_launch!r}"
            )

    def ensure_dirs(self) -> None:
        """创建运行期目录。幂等。"""
        for d in (self.data_dir, self.worktree_root, self.export_dir, self.admin_scratch_dir):
            d.mkdir(parents=True, exist_ok=True)

    def summary(self) -> dict[str, object]:
        """启动日志用。刻意不打印任何凭据。"""
        return {
            "host": self.host,
            "port": self.port,
            "data_dir": str(self.data_dir),
            "spec_path": str(self.spec_path),
            "check_spec_on_startup": self.check_spec_on_startup,
            "insecure_single_account": self.insecure_single_account,
            "auth_mode": self.auth_mode,
            # 主体 id 不是凭据；打印出来才能从启动日志判断管理员接线是否生效。
            "admin_subjects": sorted(self.admin_subjects),
            # 哪个群真的开着"先分配才能 Claim"的门禁，也从启动日志就能看出来。
            "allocation_required_groups": sorted(self.allocation_required_groups),
        }


settings = Settings()
