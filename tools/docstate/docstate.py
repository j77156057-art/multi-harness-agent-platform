# -*- coding: utf-8 -*-
"""docstate —— 规范一致性检查器（MVP-01 交付物）

用途：对 `docs/spec/0.3-R10.md` 跑机械一致性断言，防止"文档声称闭环"与"正文实际自洽"
脱节。这一工具的存在理由见 `docs/dev-convention.md` 的 H-2。

背景：R10 复审证明 R9 的 10 条"已闭环"修订全部真实落地，但 R9 自身仍有 6 处机械缺口
（事件未登记 allowlist、声明数字与实际不符、关键迁移边被折行拆断等），只有脚本能发现。
本工具把那 43 项断言固化为 CI 门禁。

用法：
    python tools/docstate/docstate.py                    # 检查默认规范文件
    python tools/docstate/docstate.py --spec <path>      # 指定规范文件
    python tools/docstate/docstate.py --json             # 机器可读输出
    python tools/docstate/docstate.py --verbose          # 打印全部通过项

退出码：0 = 全部通过；1 = 存在失败项
"""
import argparse
import collections
import io
import json
import os
import re
import sys

DEFAULT_SPEC = os.path.join("docs", "spec", "0.3-R10.md")

# 迁移表允许的伪状态（不参与 §28 可达性检查，见 R10 §10 补充说明表）
PSEUDO_STATES = {"child_created"}

TERMINAL_STATES = {"merged", "cancelled"}

ALLOWED_STATE_NAMES = set("""draft draft_rejected ready_waiting_deps ready queued verifying
claimed in_progress needs_help blocked changes_requested plan_drift impl_drift
awaiting_review approved merging stale reclaiming abandoned escalated awaiting_human
merged cancelled""".split()) | PSEUDO_STATES

ALLOWLIST_HEADINGS = [
    ("M", "迁移事件（M，74 项）："),
    ("O", "观测事件（O，13 项）："),
    ("R", "恢复事件（R，1 项）："),
]

EXPECTED_COUNTS = {"M": 74, "O": 13, "R": 1, "UNION": 88, "LOOPS": 10}

SKIP_CURRENT = {"正式状态", "正常流程", "关键迁移表", "硬规则", "边", "迁移表补充说明",
                "current", "迁移表补充说明（R10：原 R9 把副作用写在 next 括号内，导致 next 不可机械判定；R10 全部移入本表，next 列只留单一状态）"}


class Result:
    def __init__(self):
        self.checks = []

    def chk(self, ok, msg, group=""):
        self.checks.append({"ok": bool(ok), "msg": msg, "group": group})
        return ok

    @property
    def failed(self):
        return [c for c in self.checks if not c["ok"]]

    @property
    def passed(self):
        return [c for c in self.checks if c["ok"]]


def load_spec(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def extract_transitions(text):
    """从 §10 提取迁移表边。返回 [(current, event, guard, next)]。"""
    if "## 10. 状态机" not in text:
        return []
    sec = text.split("## 10. 状态机")[1].split("## 11. Claim")[0]
    m = re.search(r"关键迁移表[^\n]*\n\n~~~text\n(.*?)\n~~~", sec, re.S)
    if not m:
        return []
    rows = []
    for line in m.group(1).split("\n"):
        t = line.strip()
        if t.count("|") < 3:
            continue
        parts = [x.strip() for x in t.split("|")]
        cur = parts[0]
        if cur in SKIP_CURRENT or set(cur) <= set("-: "):
            continue
        rows.append((cur, parts[1], parts[2], parts[-1]))
    return rows


def extract_allowlist(text):
    """提取 M/O/R 三集合。"""
    out = {}
    for key, heading in ALLOWLIST_HEADINGS:
        m = re.search(re.escape(heading) + r"\n\n~~~text\n(.*?)\n~~~", text, re.S)
        if not m:
            out[key] = set()
            continue
        items = m.group(1).split()
        out[key] = set(items)
        out[key + "_raw"] = items
    return out


def parse_schema_definitions(sec8):
    """解析 §8 canonical 表定义行，返回 [(表, 主键/唯一键, 关键字段)]。"""
    rows = []
    for line in sec8.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        parts = [cell.strip() for cell in stripped.split("|")]
        if len(parts) < 5:
            continue
        name = parts[1]
        if name == "表" or set(name) <= set("-: "):
            continue
        rows.append((name, parts[2], parts[3]))
    return rows


def parse_required_fields(text):
    """解析 §37.6 补充清单，返回 {表名: 必要字段单元格}（合并写法按 `/` 展开）。"""
    if "### 37.6" not in text:
        return {}
    section = text.split("### 37.6")[1].split("### 37.7")[0]
    fields = {}
    for line in section.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        parts = [cell.strip() for cell in stripped.split("|")]
        if len(parts) < 4 or parts[1] == "表" or set(parts[1]) <= set("-: "):
            continue
        for name in parts[1].split("/"):
            fields[name.strip()] = parts[2]
    return fields


def parse_unique_columns(keys_cell):
    """取出主键/唯一键单元格中 `unique(...)` 引用的列名。"""
    columns = []
    for group in re.findall(r"unique\(([^)]*)\)", keys_cell):
        columns.extend(column.strip() for column in group.split(",") if column.strip())
    return columns


def is_parseable_required(cell):
    """§37.6 必要字段单元格是否可机器解析：须以 `、` 分列，且不含合并写法或散文分句。"""
    return bool(cell) and "、" in cell and "/" not in cell and "；" not in cell


def column_tokens(*cells):
    """把若干单元格切成列名 token 集合。§8 用 `,`，§37.6 用 `、`，散文里还有括号。"""
    tokens = set()
    for cell in cells:
        if cell:
            tokens.update(token for token in re.split(r"[,\s、()（）]+", cell) if token)
    return tokens


def run(spec_path, verbose=False):
    r = Result()
    text = load_spec(spec_path)

    # --- 组 1：文件与章节结构 ---
    r.chk("## 10. 状态机" in text, "规范含 §10 状态机章节", "structure")
    r.chk("### 37.7 事件 allowlist" in text, "规范含 §37.7 事件 allowlist 章节", "structure")
    r.chk("## 8. Canonical 数据模型" in text, "规范含 §8 canonical schema 章节", "structure")

    # --- 组 2：allowlist 集合 ---
    al = extract_allowlist(text)
    M, O, R = al.get("M", set()), al.get("O", set()), al.get("R", set())
    union = M | O | R
    r.chk(len(M) == EXPECTED_COUNTS["M"], "M 集合 = %d（实际 %d）" % (EXPECTED_COUNTS["M"], len(M)), "allowlist")
    r.chk(len(O) == EXPECTED_COUNTS["O"], "O 集合 = %d（实际 %d）" % (EXPECTED_COUNTS["O"], len(O)), "allowlist")
    r.chk(len(R) == EXPECTED_COUNTS["R"], "R 集合 = %d（实际 %d）" % (EXPECTED_COUNTS["R"], len(R)), "allowlist")
    r.chk(len(union) == EXPECTED_COUNTS["UNION"],
          "并集 = %d（实际 %d）" % (EXPECTED_COUNTS["UNION"], len(union)), "allowlist")
    r.chk(not (M & O) and not (M & R) and not (O & R), "M/O/R 两两交集为空", "allowlist")
    for key in ("M", "O", "R"):
        raw = al.get(key + "_raw", [])
        r.chk(len(raw) == len(set(raw)), "%s 集合内部无重复" % key, "allowlist")
    allow = union

    # --- 组 3：迁移表封闭性与唯一性 ---
    rows = extract_transitions(text)
    r.chk(len(rows) > 0, "迁移表解析出 %d 条边" % len(rows), "transitions")
    used = set(ev for _, ev, _, _ in rows)
    escape = sorted(used - allow)
    r.chk(not escape, "§10 全部 event ⊆ allowlist；越界=%s" % escape, "transitions")
    pair_count = collections.Counter((c, e) for c, e, _, _ in rows)
    conflict = {k: v for k, v in pair_count.items() if v > 1}
    r.chk(not conflict, "同一 current+event 唯一；冲突=%s" % conflict, "transitions")
    multi = [(c, e, n) for c, e, _, n in rows if "或" in n]
    r.chk(not multi, "next 单值（无'或'）；多值=%s" % multi, "transitions")
    paren = [(c, e, n) for c, e, _, n in rows if "（" in n]
    r.chk(not paren, "next 无括号补充；带括号 %d 条" % len(paren), "transitions")
    r.chk("\n       | lease_expire" not in text.split("## 11. Claim")[0],
          "迁移表无被折行拆断的边", "transitions")

    # --- 组 4：R10 关键事件必须存在 ---
    for ev, note in [
        ("claim_deferred_resource", "R10-01 ready|claim 双 next 拆分"),
        ("reopen", "R10-02 abandoned 恢复出口"),
        ("orphan_detected", "R10-05 ready_waiting_deps 孤儿出口"),
        ("plan.approve_dependency_pending", "R10-fix-04 plan.approve 多候选 next 拆分"),
        ("differential_not_applicable", "R10-fix-03 not_applicable 正向边"),
        ("human.reopen_no_owner", "R10-fix-05 human.reopen 双边拆分"),
        ("merge_conflict_needs_owner", "R10-06 merge_conflict 双 next 拆分"),
        ("merge_post_commit_failure_escalated", "R10-06 post_commit_failure 双 next 拆分"),
    ]:
        r.chk(ev in M, "M 集合含 %s（%s）" % (ev, note), "r10-events")
        r.chk(ev in used, "§10 有 %s 对应边" % ev, "r10-events")

    # --- 组 5：自环边 ---
    loops = [(c, e) for c, e, _, n in rows
             if n in [x.strip() for x in c.split("/")]]
    r.chk(len(loops) == EXPECTED_COUNTS["LOOPS"],
          "自环 = %d 条（实际 %d）" % (EXPECTED_COUNTS["LOOPS"], len(loops)), "loops")
    for ev in ["verify_env_failed", "verify_lease_expire", "human_wait_timeout",
               "escalation_reminder", "submit_rejected_verify_capacity",
               "review_lease_expire", "merge_lease_expire",
               "help_requested", "help_accepted", "help_rejected"]:
        r.chk(ev in dict((e, c) for c, e in loops), "自环清单含 %s" % ev, "loops")

    # --- 组 6：状态覆盖 ---
    out_count = collections.Counter()
    for c, _, _, _ in rows:
        for s in c.split("/"):
            out_count[s.strip()] += 1
    # 注意：伪状态（child_created）只出现在 next 位置，从不作为 current，
    # 按R10 §10 补充说明表不参与可达性检查，必须排除
    no_out = sorted(s for s in ALLOWED_STATE_NAMES
                    if s not in TERMINAL_STATES
                    and s not in PSEUDO_STATES
                    and out_count.get(s, 0) == 0)
    r.chk(not no_out, "全部非终态有出边；无出边=%s" % no_out, "states")
    for t in TERMINAL_STATES:
        r.chk(out_count.get(t, 0) == 0, "终态 %s 无出边（实际 %d）" % (t, out_count.get(t, 0)), "states")
    rwd = [(e, n) for c, e, _, n in rows if "ready_waiting_deps" in c]
    r.chk(len(rwd) == 3, "ready_waiting_deps 3 条出边（实际 %d）" % len(rwd), "states")
    r.chk(any(e == "block_timeout" for e, _ in rwd), "含 block_timeout 出口（R10-05）", "states")
    r.chk(any(e == "orphan_detected" for e, _ in rwd), "含 orphan_detected 出口（R10-05）", "states")

    # --- 组 7：schema 关键字段 ---
    sec8 = text.split("## 8. Canonical 数据模型")[1].split("## 9.")[0]
    tasks_row = ""
    for line in sec8.split("\n"):
        if line.strip().startswith("| tasks |"):
            tasks_row = line
            break
    r.chk(bool(tasks_row), "§8 存在 tasks 最小定义行", "schema")
    for f in ["attempt_no", "hotfix", "budget_override_by", "upstream_invalidated_seq",
              "base_locked_seq", "max_attempts", "review_policy"]:
        r.chk(f in tasks_row, "tasks.%s 在 §8 tasks 行" % f, "schema")
    for f in ["patch_hash", "applied_by", "generated_commit", "fencing_epoch", "patch_uri"]:
        r.chk(f in sec8, "side_effect_intents 补 %s" % f, "schema")
    r.chk("patch_base_commit" in sec8, "artifacts 补 patch_base_commit", "schema")
    r.chk("budget_override_by` 是唯一权威列名" in text, "R10-14 命名统一声明存在", "schema")
    r.chk("attempt_no" in text.split("## 11. Claim")[1][:2000], "§11 Claim SQL 引用 attempt_no", "schema")

    # --- 组 7b：§8 主键/唯一键与关键字段一致性（R10-fix-09） ---
    # 覆盖边界（与 §38 R10-fix-09 一致，勿静默扩大）：本组只对 §37.6 必要字段单元格
    # 可机器解析的表生效——不含 "/"、不含"；"。§37.6 对 task_outputs 等表使用有意的
    # 合并/散文写法（R10-08 记录过），那些行**跳过而非判失败**，属已知覆盖缺口。
    # check 消息带上实际检查表数与跳过表名，复核者据此判断覆盖面，不静默缩小。
    definitions = parse_schema_definitions(sec8)
    required = parse_required_fields(text)

    offenders = []
    checked_tables = 0
    skipped_tables = []
    for name, keys_cell, fields_cell in definitions:
        columns = parse_unique_columns(keys_cell)
        if not columns:
            continue
        required_cell = required.get(name)
        if required_cell is not None and not is_parseable_required(required_cell):
            skipped_tables.append(name)
            continue
        checked_tables += 1
        declared = column_tokens(fields_cell, required_cell)
        missing = sorted(column for column in columns if column not in declared)
        if missing:
            offenders.append("%s 引用未声明列 %s" % (name, ",".join(missing)))
    r.chk(
        not offenders,
        "§8 唯一键引用的列均已声明；越界=%s（检查 %d 张表，跳过 %d 张：%s）"
        % (offenders or "无", checked_tables, len(skipped_tables), skipped_tables or "无"),
        "schema-keys",
    )

    unresolved = []
    checked_ddls = 0
    if "## 11. Claim" in text:
        section11 = text.split("## 11. Claim")[1].split("## 12.")[0]
        for match in re.finditer(r"([a-z_][a-z0-9_]*)\(([^()]*)\)", section11):
            table, column_text = match.group(1), match.group(2)
            names = []
            for column in column_text.split(","):
                stripped = re.sub(
                    r"\s+(?:PRIMARY\s+KEY|NOT\s+NULL|DEFAULT\b.*|CHECK\b.*)$",
                    column.strip(),
                    flags=re.I,
                ).strip()
                if re.fullmatch(r"[a-z_][a-z0-9_]*", stripped):
                    names.append(stripped)
            if len(names) < 2:
                continue
            checked_ddls += 1
            declared = column_tokens(required.get(table))
            declared |= column_tokens(
                *[cells for row_name, _, cells in definitions if row_name == table]
            )
            missing = sorted(name for name in names if name not in declared)
            if missing:
                unresolved.append("%s 声明了未登记列 %s" % (table, ",".join(missing)))
    r.chk(
        not unresolved,
        "§11 DDL 列均落在 §8/§37.6 声明内；越界=%s（检查 %d 处 DDL）"
        % (unresolved or "无", checked_ddls),
        "schema-keys",
    )

    # --- 组 8：§37.6 schema 补充清单 ---
    if "### 37.6" in text:
        sec376 = text.split("### 37.6")[1].split("### 37.7")[0]
        for t in ["command_idempotency", "verification_receipts", "side_effect_intents",
                  "task_dependencies", "claim_requests"]:
            r.chk(t in sec376, "§37.6 含 %s" % t, "schema-list")

    # --- 组 9：test_id 阶段落点 ---
    declared = set()
    for m in re.finditer(r"阶段 (\d(?:[ab])?) [^\n]*检查 ID[：:]\s*([^\n]+)", text):
        declared |= set(re.findall(r"[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+", m.group(2)))
    allids = set(re.findall(r"\b(?:DOC|CORE|GIT|VERIFY|MERGE)-[A-Z0-9]+(?:-[A-Z0-9]+)+\b", text))
    dangling = sorted(allids - declared)
    r.chk(not dangling, "无悬空 test_id；悬空=%s" % dangling, "trace")
    for must in ["DOC-STATE-001", "DOC-SCHEMA-001", "DOC-TRACE-001", "DOC-PLAN-001",
                 "CORE-CLAIM-002", "CORE-FENCE-001", "GIT-ACL-002", "GIT-PACK-001",
                 "MERGE-REVERT-002"]:
        r.chk(must in declared, "检查 ID 已登记：%s" % must, "trace")

    # --- 组 10：阶段边界与通过条件 ---
    if "### 29.3 终审通过条件" in text:
        sec293 = text.split("### 29.3 终审通过条件")[1].split("### 29.4")[0]
        r.chk("88" in sec293 and "allowlist" in sec293,
              "§29.3 含 allowlist 封闭性断言（88 项）", "gates")
    r.chk("### 29.6 R11 复审请求" in text, "含 §29.6 R11 复审请求", "gates")
    r.chk("## 38. R10 终审修订映射与增量规范" in text, "含 §38 R10 增量规范", "gates")
    r.chk("版本：0.3-R10" in text, "版本行为 0.3-R10", "gates")
    r.chk("本文 0.3-R10 已经是整合规范" in text, "§29 指向 R10", "gates")

    return r


def main():
    ap = argparse.ArgumentParser(description="规范一致性检查器")
    ap.add_argument("--spec", default=DEFAULT_SPEC, help="规范文件路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--verbose", action="store_true", help="打印通过项")
    args = ap.parse_args()

    if not os.path.exists(args.spec):
        sys.stderr.write("spec not found: %s\n" % args.spec)
        return 2

    result = run(args.spec, verbose=args.verbose)

    if args.json:
        print(json.dumps({
            "spec": args.spec,
            "total": len(result.checks),
            "passed": len(result.passed),
            "failed": len(result.failed),
            "checks": result.checks,
        }, ensure_ascii=False, indent=2))
    else:
        print("=== docstate: %s ===" % args.spec)
        groups = collections.OrderedDict()
        for c in result.checks:
            groups.setdefault(c["group"], []).append(c)
        for g, items in groups.items():
            bad = [i for i in items if not i["ok"]]
            print("[%s] %d 项，%d 失败" % (g, len(items), len(bad)))
            for i in items:
                if not i["ok"]:
                    print("   FAIL  " + i["msg"])
                elif args.verbose:
                    print("   ok    " + i["msg"])
        print("")
        print("结果: %d 项检查，%d 项失败" % (len(result.checks), len(result.failed)))

    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
