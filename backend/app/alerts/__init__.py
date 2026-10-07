"""出站告警 webhook（alert-webhook）。

Mechanism learned from Paperclip's outbound-webhook pattern -- signed payloads,
HTTPS-only, retryable deliveries (see
``docs/reports/paperclip-buzz-source-review.md`` §2.8) -- reimplemented natively
on this repository's own primitives: a cursor over ``task_events``, a delivery
queue, and stdlib HTTP. No code was copied from either reviewed project.
"""

from app.alerts.service import (
    KIND_TASK_ESCALATED,
    AlertConfigError,
    AlertNotifier,
    AlertRunReport,
    escalation_events,
)

__all__ = [
    "KIND_TASK_ESCALATED",
    "AlertConfigError",
    "AlertNotifier",
    "AlertRunReport",
    "escalation_events",
]
