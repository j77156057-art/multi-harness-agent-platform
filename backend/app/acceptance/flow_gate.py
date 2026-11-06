"""交付验收（experience acceptance）：把 v5 冻结判据接进真实派单链路。

这一层**不重新定义**判据：禁词归一化、骨架匹配、阈值全部来自
``backend/scripts/evaluate_experience_acceptance_v5.py``（运行时按 SHA-256
校验冻结身份，判据一变就拒绝启用）。这里只做三件事：

1. 从任务真实的 commit 里读出交付文件（仓库事实，不读模型自述）；
2. 调用冻结判据给出 pass / fail / unjudgeable，并把不合格原因结构化；
3. 供 worker 做"至多一次退回"与终态落盘。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.git.repo import run_git

ROOT = Path(__file__).resolve().parents[3]
FROZEN_SCRIPT = ROOT / "backend" / "scripts" / "evaluate_experience_acceptance_v5.py"
FROZEN_FIXTURE = ROOT / "artifacts" / "experience-acceptance-ab" / "v5" / "fixtures" / "v4-ret03-split.md"

#: v5 冻结声明（protocol.json 原文）。
FROZEN = {
    "script": "sha256:c9387c5022cd4bff9d5d2718e08c85e912b39e60c9ca1589a152cd3b1389856c",
    "check_spec": "sha256:442fd571907b11642fd2550da393f04fc23c54a1ba1ae6cd837108ac74919787",
    "v4_fixture": "sha256:83dda027e4ee98091c6df72295099c37544f8361da50f7c269f2141219e487fb",
}
PROFILE = "experience-v5"
#: v5 检查清单里 RET-03 那一组旧禁词（冻结内容，不新增、不改阈值）。
TOKENS = ("TODO", "待补充", "占位", "示例文本")
CONTENT_MIN_LENGTH = 40
MAX_RETRIES = 1


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_frozen():
    spec = importlib.util.spec_from_file_location("app_acceptance_frozen_v5", FROZEN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["app_acceptance_frozen_v5"] = module
    spec.loader.exec_module(module)
    return module


V5 = _load_frozen()


class FrozenCriteriaChanged(RuntimeError):
    """冻结判据的文件或检查清单与 v5 声明不一致。"""


def frozen_identity() -> dict[str, str]:
    """运行时核对冻结身份；不一致就拒绝启用（fail-closed）。"""
    script = _sha(FROZEN_SCRIPT.read_text(encoding="utf-8"))
    check_spec = V5.spec_sha()
    if script != FROZEN["script"]:
        raise FrozenCriteriaChanged(f"v5 判据脚本变了：{script} != {FROZEN['script']}")
    if check_spec != FROZEN["check_spec"]:
        raise FrozenCriteriaChanged(f"v5 检查清单变了：{check_spec} != {FROZEN['check_spec']}")
    if not FROZEN_FIXTURE.exists():
        raise FrozenCriteriaChanged(f"v4 固定夹具不存在：{FROZEN_FIXTURE}")
    fixture = _sha(FROZEN_FIXTURE.read_text(encoding="utf-8"))
    if fixture != FROZEN["v4_fixture"]:
        raise FrozenCriteriaChanged(f"v4 固定夹具变了：{fixture} != {FROZEN['v4_fixture']}")
    return {
        "profile": PROFILE,
        "script": script,
        "check_spec": check_spec,
        "v4_fixture": fixture,
    }


def banned_hits(text: str, tokens: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """冻结判据的禁词匹配（可见文本 + 字母数字骨架各一次）。"""
    return V5.banned_hits(text, list(tokens))


def visible_text(text: str) -> str:
    return V5.v3.visible_text(text)


def judge_text(
    text: str,
    *,
    tokens: tuple[str, ...] | list[str] = TOKENS,
    other_checks_pass: bool = True,
    previous_verdict: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """与 v5 ``new_verdict`` 同一语义的纯文本判定（供接入回归用）。"""
    if not text or not text.strip():
        return "unjudgeable_no_text", []
    hits = banned_hits(text, tokens)
    if hits:
        return "fail", hits
    if other_checks_pass and previous_verdict in (None, "pass", "still_bypassing"):
        return "pass", []
    return (previous_verdict or "fail"), []


def _line_of(text: str, needle: str) -> int | None:
    index = text.find(needle)
    if index < 0:
        return None
    return text.count("\n", 0, index) + 1


def _read_commit_file(worktree: Path, commit: str, path: str) -> str | None:
    proc = run_git("show", f"{commit}:{path}", cwd=worktree, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout


class AcceptanceRejected(RuntimeError):
    """交付未通过验收（退回后仍不合格，或不可判定）。"""

    def __init__(self, task_id: str, record: dict[str, Any]) -> None:
        gaps = record.get("gaps") or []
        super().__init__(f"{task_id}: acceptance {record.get('state')} ({len(gaps)} gap(s))")
        self.task_id = task_id
        self.record = record

    def abandon_reason(self) -> str:
        payload = {
            "acceptance": self.record.get("state"),
            "profile": PROFILE,
            "gaps": self.record.get("gaps") or [],
            "rounds": [
                {"round": item["round"], "verdict": item["verdict"], "commit": item["commit"]}
                for item in self.record.get("rounds") or []
            ],
        }
        return "acceptance_failed " + json.dumps(payload, ensure_ascii=False)[:1800]


class ExperienceAcceptanceGate:
    """一次尝试的交付验收：判交付 commit、生成退回工单、落盘终态。"""

    def __init__(
        self,
        *,
        task_id: str,
        expected_paths: list[str] | tuple[str, ...],
        attempt_id: str = "",
        tokens: tuple[str, ...] = TOKENS,
    ) -> None:
        self.task_id = task_id
        self.attempt_id = attempt_id
        self.expected_paths = [str(path).replace("\\", "/") for path in expected_paths]
        self.tokens = tokens
        self.rounds: list[dict[str, Any]] = []
        self.identity = frozen_identity()

    # ------------------------------------------------------------------ judge

    def run(
        self,
        *,
        commit: str,
        worktree: Path,
        changed: list[str],
        round_no: int | None = None,
    ) -> dict[str, Any]:
        changed_set = {path.replace("\\", "/") for path in changed}
        files: dict[str, str] = {}
        for path in changed:
            posix = path.replace("\\", "/")
            text = _read_commit_file(worktree, commit, posix)
            if text is not None:
                files[posix] = text
        expected_files = [p for p in self.expected_paths if not p.endswith("/")]
        expected_dirs = [p for p in self.expected_paths if p.endswith("/")]

        checks: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        for path in expected_files:
            exists = path in changed_set and path in files
            checks.append({"id": "delivery_path_exists", "path": path, "pass": exists})
            if not exists:
                gaps.append(
                    {
                        "check": "delivery_path_exists",
                        "path": path,
                        "detail": f"交付提交里没有 {path}",
                    }
                )
        for prefix in expected_dirs:
            exists = any(p.startswith(prefix) for p in changed_set)
            checks.append({"id": "delivery_path_exists", "path": prefix, "pass": exists})
            if not exists:
                gaps.append(
                    {
                        "check": "delivery_path_exists",
                        "path": prefix,
                        "detail": f"授权落点 {prefix} 下没有任何交付文件",
                    }
                )

        delivered = "\n".join(
            files.get(path, "") for path in expected_files if path in files
        ) or "\n".join(files.values())
        length = len(visible_text(delivered))
        ok_length = length >= CONTENT_MIN_LENGTH
        checks.append(
            {
                "id": "content_min_length",
                "min_chars": CONTENT_MIN_LENGTH,
                "actual": length,
                "pass": ok_length,
            }
        )
        if not ok_length:
            gaps.append(
                {
                    "check": "content_min_length",
                    "min_chars": CONTENT_MIN_LENGTH,
                    "actual": length,
                    "detail": f"交付正文可见长度 {length} < {CONTENT_MIN_LENGTH}",
                }
            )

        hit_rows: list[dict[str, Any]] = []
        for path, text in sorted(files.items()):
            for hit in banned_hits(text, self.tokens):
                rendered = visible_text(text)
                needle = visible_text(str(hit["token"]))
                hit_rows.append(
                    {
                        "check": "banned_token",
                        "path": path,
                        "token": hit["token"],
                        "visible_hit": hit["visible_hit"],
                        "skeleton_hit": hit["skeleton_hit"],
                        "skeleton_index": hit["skeleton_index"],
                        "skeleton_fragment": hit["skeleton_fragment"],
                        "skeleton_context": hit["skeleton_context"],
                        "line": _line_of(rendered, needle) if hit["visible_hit"] else None,
                    }
                )
        checks.append(
            {
                "id": "banned_tokens_absent",
                "tokens": list(self.tokens),
                "hits": hit_rows,
                "pass": not hit_rows,
            }
        )
        gaps.extend(hit_rows)

        has_text = any(text.strip() for text in files.values())
        if not has_text:
            verdict = "unjudgeable_no_text"
        elif any(not check["pass"] for check in checks):
            verdict = "fail"
        else:
            verdict = "pass"

        record = {
            "round": round_no if round_no is not None else len(self.rounds) + 1,
            "commit": commit,
            "changed_files": sorted(changed_set),
            "checks": checks,
            "gaps": gaps,
            "verdict": verdict,
            "frozen": self.identity,
        }
        self.rounds.append(record)
        return record

    # ------------------------------------------------------------------ gate

    def check(self, *, commit: str, worktree: Path, changed: list[str]) -> dict[str, Any]:
        return self.run(commit=commit, worktree=worktree, changed=changed)

    def retry_available(self) -> bool:
        return len(self.rounds) < MAX_RETRIES + 1

    def retry_goal(self, goal: str | None, record: dict[str, Any]) -> str:
        lines = [
            "",
            "## 验收退回（只允许一次）",
            f"上一轮交付（commit {record['commit'][:12]}）没有通过验收，逐项修掉：",
        ]
        for index, gap in enumerate(record["gaps"], start=1):
            if gap["check"] == "delivery_path_exists":
                lines.append(f"{index}. 交付路径不存在：`{gap['path']}`（必须创建该文件）")
            elif gap["check"] == "content_min_length":
                lines.append(
                    f"{index}. 正文太短：可见长度 {gap['actual']}，至少 {gap['min_chars']}"
                )
            else:
                where = f"`{gap['path']}`"
                if gap.get("line"):
                    where += f" 第 {gap['line']} 行"
                elif gap.get("skeleton_index") is not None and gap["skeleton_index"] >= 0:
                    where += f" 骨架下标 {gap['skeleton_index']}"
                lines.append(
                    f"{index}. 命中禁词 `{gap['token']}`：{where}"
                    f"（上下文：{gap.get('skeleton_context') or ''}）"
                )
        lines += [
            "",
            "## 当前验收口径（压过旧派单）",
            "旧派单要求「列出旧禁词清单」的部分已经被当前验收口径作废。"
            f"可见正文里不得出现这些串：{'、'.join(self.tokens)}。",
            "不要换一种写法继续列出它们：不要用编码（HTML 实体 / \\uXXXX / 百分号）、"
            "不要插入顿号/斜杠/空格/零宽字符把词拆开、不要用同形字或缩写。"
            "把该写的内容写成正常中文/英文，并保留对验收口径的正常说明。",
            "交付物路径保持不变：" + "、".join(f"`{p}`" for p in self.expected_paths),
        ]
        base = (goal or "").rstrip()
        return base + "\n" + "\n".join(lines)

    def final_record(
        self,
        state: str,
        *,
        attempt_state: str | None = None,
        finish_reason: str | None = None,
    ) -> dict[str, Any]:
        retries = max(len(self.rounds) - 1, 0)
        if attempt_state is None:
            attempt_state = state if state in {"passed", "retry_fixed"} else "abandoned"
        if retries == 0:
            retry_state = "first_attempt"
        elif attempt_state in {"passed", "retry_fixed"}:
            retry_state = "retry_fixed"
        else:
            retry_state = "still_failing"
        record = {
            "profile": PROFILE,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "state": state,
            # 平台状态机字段（tasks.acceptance_state）直接落这个值：
            # passed / retry_fixed / abandoned / unjudgeable。
            "attempt_state": attempt_state,
            "retries": retries,
            "retry_state": retry_state,
            "frozen": self.identity,
            "rounds": self.rounds,
            "gaps": self.rounds[-1]["gaps"] if self.rounds else [],
            "finish_reason": finish_reason,
            "judged_at": datetime.now(UTC).isoformat(),
        }
        return record

    def persist(self, record: dict[str, Any]) -> Path:
        directory = Path(settings.data_dir) / "acceptance"
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{self.task_id}.{self.attempt_id or 'unknown'}.json"
        path = directory / name
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
