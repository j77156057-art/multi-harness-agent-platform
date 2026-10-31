"""评审标准归人管，不归代码管（`app/admin/review_rules.py`）。

这两条要一起测：给一份文件，标准就换；文件坏了，平台用内置默认并说明原因，
而不是把整条评审队列停掉。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.admin.review_rules import DEFAULT_CRITERIA, load_review_rules, rules_path


def test_the_repository_default_file_carries_the_built_in_bar() -> None:
    """仓库根上那份文件存在，且内容就是以前写死在提示词里的那一条。"""
    criteria, problem = load_review_rules()
    assert problem is None
    assert criteria == DEFAULT_CRITERIA


def test_a_changed_file_changes_the_standard(tmp_path: Path) -> None:
    """项目主人改文件 = 改评审标准，不用碰代码。"""
    target = tmp_path / "review-rules.json"
    target.write_text(
        json.dumps({"criteria": ["approve unless the acceptance is contradicted."]}, ensure_ascii=False),
        encoding="utf-8",
    )
    criteria, problem = load_review_rules(target)
    assert problem is None
    assert criteria == ("approve unless the acceptance is contradicted.",)


def test_the_env_var_points_at_another_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"criteria": ["one bar"]}), encoding="utf-8")
    monkeypatch.setenv("AP_REVIEW_RULES_FILE", str(target))
    assert rules_path() == target
    assert load_review_rules()[0] == ("one bar",)


def test_a_missing_file_is_not_fatal(tmp_path: Path) -> None:
    """文件不在 = 用内置默认。它不等于「评审规则是空的」。"""
    criteria, problem = load_review_rules(tmp_path / "nope.json")
    assert criteria == DEFAULT_CRITERIA
    assert problem is None


def test_a_broken_file_falls_back_and_says_why(tmp_path: Path) -> None:
    target = tmp_path / "review-rules.json"
    target.write_text("{not json", encoding="utf-8")
    criteria, problem = load_review_rules(target)
    assert criteria == DEFAULT_CRITERIA
    assert problem and "unreadable" in problem


def test_an_empty_criteria_list_is_not_a_silent_pass(tmp_path: Path) -> None:
    """空数组不能被读成「没有任何标准」—— 那样每一次评审都会直接 approve。"""
    target = tmp_path / "review-rules.json"
    target.write_text(json.dumps({"criteria": []}), encoding="utf-8")
    criteria, problem = load_review_rules(target)
    assert criteria == DEFAULT_CRITERIA
    assert problem and "criteria" in problem
