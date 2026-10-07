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

    def ensure_dirs(self) -> None:
        """创建运行期目录。幂等。"""
        for d in (self.data_dir, self.worktree_root, self.export_dir):
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
        }


settings = Settings()
