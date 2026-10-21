"""集成健康度的恢复出口（``--recheck-health``）。

R10 §21 把非绿的 ``integration_health`` 定成整个项目合并队列的 stop-the-line，
而回到绿色的唯一途径是"再跑一轮合并/回滚"。集成检查档配错时这就是死锁：队列
被停掉，而唯一能解开它的合并又恰好被停掉。

这里钉住恢复出口的三条边界：

1. 默认是**只读**的：跑一次配置好的集成检查，不写任何东西；
2. 只有检查通过，``apply`` 才把投影写回绿色；检查失败时行保持原样；
3. 没有配置集成检查档时**拒绝**执行——没有跑过检查就没有资格宣布队列健康。
"""

from __future__ import annotations

import pytest

from app.merge.worker import MergeError, MergeWorker
from tests.merge_support import Chain, build_chain, health_of, policy

PASS = ["python", "-c", "import sys; sys.exit(0)"]
FAIL = ["python", "-c", "import sys; sys.exit(1)"]


def escalate(chain: Chain) -> None:
    """Put the project's health row where a failed post-merge check left it."""
    with chain.database.transaction() as conn:
        conn.execute(
            "UPDATE integration_health SET status = 'escalated' WHERE project_id = 'P-1'"
        )


def worker_for(chain: Chain, *, command=None, with_policy: bool = True) -> MergeWorker:
    return MergeWorker(
        chain.database,
        chain.repo,
        policy=policy(command=command) if with_policy else None,
        overlay_root=chain.tmp_path / "recheck-ov",
    )


def test_a_read_only_recheck_writes_nothing(tmp_path):
    chain = build_chain(tmp_path)
    escalate(chain)

    outcome = worker_for(chain, command=PASS).recheck_integration_health("P-1")

    assert outcome.result == "passed"
    assert outcome.applied is False
    assert outcome.status == "escalated"
    assert health_of(chain) == "escalated"


def test_a_passing_recheck_may_put_the_health_back_to_green(tmp_path):
    chain = build_chain(tmp_path)
    escalate(chain)

    outcome = worker_for(chain, command=PASS).recheck_integration_health("P-1", apply=True)

    assert outcome.result == "passed"
    assert outcome.applied is True
    assert health_of(chain) == "green"
    tip = chain.repo.ref_commit(chain.repo.integration_ref)
    assert outcome.commit == tip
    stored = chain.database.read(
        "SELECT integration_commit FROM integration_health WHERE project_id = 'P-1'"
    )[0]["integration_commit"]
    assert stored == tip


def test_a_failing_check_never_turns_the_health_green(tmp_path):
    chain = build_chain(tmp_path)
    escalate(chain)

    outcome = worker_for(chain, command=FAIL).recheck_integration_health("P-1", apply=True)

    assert outcome.result == "failed"
    assert outcome.applied is False
    assert outcome.detail is not None
    assert health_of(chain) == "escalated"


def test_a_project_without_a_health_row_is_refused(tmp_path):
    chain = build_chain(tmp_path)

    with pytest.raises(MergeError):
        worker_for(chain, command=PASS).recheck_integration_health("P-does-not-exist")


def test_without_a_profile_the_queue_is_not_declared_healthy(tmp_path):
    """没跑过检查就没有资格宣布队列健康。"""
    chain = build_chain(tmp_path)
    escalate(chain)

    with pytest.raises(MergeError):
        worker_for(chain, with_policy=False).recheck_integration_health("P-1", apply=True)

    assert health_of(chain) == "escalated"
