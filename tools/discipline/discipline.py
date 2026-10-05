# -*- coding: utf-8 -*-
"""discipline —— 协作纪律检查器（把纯自觉的规则变成会红的断言）。

存在理由见 `docs/dev-convention.md` 的「约束的强制程度」。一句话：R10 的自洽性由
`docstate` 机械保证，但**纪律层此前几乎全是散文** —— 清单把「没有 test_id 的完成
标准不可验收」列为硬约束 H-4，却没有任何东西检查 TASKS.md 里的任务卡有没有
test_id；「登记先于开工」「禁止在 main 上直接提交」同样只是文字。

本工具补的就是这一层。它检查 `TASKS.md` 的内部一致性，以及 README /
dev-convention.md 里声明的 docstate 断言数是否与实际相符。

与 docstate 的关系：docstate 管**规范与代码不脱节**，本工具管**登记表自身是否自洽**。
两者都是零外部依赖（纯标准库），都并入 `scripts.sh check`，且都在 CI 跑。
把纪律检查放进已有的门禁，而不是新增 git hook —— hook 可以 `--no-verify` 绕过，
门禁不行。

用法：
    python tools/discipline/discipline.py                # 检查默认路径
    python tools/discipline/discipline.py --repo <path>  # 指定仓库根
    python tools/discipline/discipline.py --json
    python tools/discipline/discipline.py --verbose

退出码：0 = 全部通过；1 = 存在失败项；2 = 找不到被检查的文件
"""
import argparse
import collections
import io
import json
import os
import re
import sys

DEFAULT_TASKS = os.path.join("TASKS.md")

# 任务表「状态」列允许包含的关键字。与 TASKS.md「状态图例」保持一致；
# 允许附加注记（如 "⏳ waiting ★瓶颈"），故用包含判定而非全等。
LEGAL_STATUS_KEYWORDS = [
    "ready",
    "waiting",
    "prep",
    "doing",
    "review",
    "done",
    "已验收",
    "blocked",
    "claimed",
]

# 未标记为完成的状态 —— 这些任务卡必须声明工作区（H-7）
FINISHED_STATUS_KEYWORDS = ["done", "已验收"]

# 空白模板的占位任务编号，必须排除在卡片检查之外
TEMPLATE_TASK_ID = "MVP-__"


class Result:
    """与 docstate 同构的结果收集器。"""

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


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def section(text, start, end_prefix="\n## "):
    """切片出一个 `##` 章节；找不到起点返回空串。"""
    if start not in text:
        return ""
    body = text.split(start, 1)[1]
    return body.split(end_prefix, 1)[0] if end_prefix in body else body


def table_rows(block):
    """把 markdown 表格区块解析成单元格列表，跳过表头与分隔行。"""
    rows = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")]
        if len(cells) < 3:
            continue
        name = cells[1]
        if set(name) <= set("-: ") or name == "---":
            continue
        rows.append(cells[1:-1])
    return rows


def parse_task_table(text):
    """任务表 → [{task_id, harness, deps, status, branch, test_id}]"""
    entries = []
    for cells in table_rows(section(text, "## 任务表")):
        if cells[0] == "task_id":
            continue
        entries.append(
            {
                "task_id": cells[0],
                "harness": cells[1] if len(cells) > 1 else "",
                "deps": cells[2] if len(cells) > 2 else "",
                "status": cells[3] if len(cells) > 3 else "",
                "branch": cells[4] if len(cells) > 4 else "",
                "test_id": cells[5] if len(cells) > 5 else "",
            }
        )
    return entries


def parse_claim_table(text):
    """认领登记表 → [{task_id, harness, claimed_at, branch, status}]"""
    entries = []
    for cells in table_rows(section(text, "## 认领登记")):
        if cells[0] == "序号":
            continue
        entries.append(
            {
                "seq": cells[0],
                "task_id": cells[1] if len(cells) > 1 else "",
                "harness": cells[2] if len(cells) > 2 else "",
                "claimed_at": cells[3] if len(cells) > 3 else "",
                "branch": cells[4] if len(cells) > 4 else "",
                "status": cells[5] if len(cells) > 5 else "",
            }
        )
    return entries


def parse_cards(text):
    """任务卡 → [{title, fields, body}]。

    卡片的证据常在后续 `###` 小节里（`### 验收结果`、`### 完成标准与验证结果`
    表格），所以卡片的范围必须延伸到**下一张卡**的开头，而不是下一个 `###`。
    两遍解析：先按 `### ` 切块，认出哪些块是卡片，再把非卡片小节并入前一张卡片。
    """
    blocks = re.split(r"^### ", text, flags=re.M)[1:]
    cards = []
    for block in blocks:
        title = block.splitlines()[0].strip()
        # `字段：值`，值可跨行：这里只取首行，内容判断交给 has_verification_evidence
        fields = {}
        for match in re.finditer(r"^([^\s：:]{1,20})[：:](.*)$", block, re.M):
            key = match.group(1).strip()
            if key not in fields:
                fields[key] = match.group(2).strip()
        task_id = fields.get("任务编号")
        if not task_id or task_id == TEMPLATE_TASK_ID:
            # 空白模板（`## 统一任务卡` 下）或普通小节：若是普通小节则并入上一张卡
            if cards and task_id is None:
                cards[-1]["body"] += "\n### " + block
            continue
        cards.append({"title": title, "fields": fields, "body": block, "task_id": task_id})
    return cards


def is_finished(status):
    return any(keyword in (status or "") for keyword in FINISHED_STATUS_KEYWORDS)


def is_waiting(status):
    """是否尚未开工。带注记（如 `⏳ waiting ★瓶颈`）也要算等待。"""
    return "waiting" in (status or "")


def has_verification_evidence(card):
    """完成卡片的验证证据：或是 `验证结果：` 字段有实质内容，或是一张结果表。

    历史上各卡片写法不统一（MVP-01 用缩进的 `验证结果：` 多行块，MVP-02 用
    `### 验收结果` 表格，MVP-02-amend 用 `### 完成标准与验证结果` 表格），
    因此这里接受两种形式，而不是逼着改写历史记录。
    """
    body = card["body"]
    if re.search(r"验证结果[：:]\s*\n\s*\S", body) or re.search(r"验证结果[：:]\s*\S", body):
        return True
    return bool(re.search(r"^###\s*(完成标准与验证结果|验收结果|验证结果)", body, re.M))


def check_registry(result, tasks_text):
    """组 1：任务表与认领登记表自身的完整性。"""
    tasks = parse_task_table(tasks_text)
    claims = parse_claim_table(tasks_text)

    result.chk(bool(tasks), "任务表解析出 %d 行" % len(tasks), "registry")
    result.chk(bool(claims), "认领登记表解析出 %d 行" % len(claims), "registry")

    bad_status = [t["task_id"] for t in tasks if not any(
        keyword in t["status"] for keyword in LEGAL_STATUS_KEYWORDS)]
    result.chk(
        not bad_status,
        "任务表状态列取值合法；非法=%s" % (bad_status or "无"),
        "registry",
    )

    # 认领即需负责人与分支（登记规则第 0 条、第 6 条）。尚未开工的任务豁免。
    unassigned = [
        t["task_id"]
        for t in tasks
        if not is_finished(t["status"])
        and not is_waiting(t["status"])
        and ("—" in t["harness"] or "—" in t["branch"])
    ]
    result.chk(
        not unassigned,
        "已开始的任务均有 Harness 与分支；缺=%s" % (unassigned or "无"),
        "registry",
    )

    missing_test_id = [t["task_id"] for t in tasks if not t["test_id"].strip()]
    result.chk(
        not missing_test_id,
        "任务表每行都有 test_id（H-4）；缺=%s" % (missing_test_id or "无"),
        "registry",
    )

    # 两张表必须互相覆盖 —— 但只有已开始的任务才该有认领记录，
    # 没人认领的 waiting 任务不需要占位。
    task_ids = {t["task_id"] for t in tasks}
    claim_ids = {c["task_id"] for c in claims}
    result.chk(
        claim_ids <= task_ids,
        "认领登记的任务都在任务表中；孤儿=%s" % (sorted(claim_ids - task_ids) or "无"),
        "registry",
    )
    started = {t["task_id"] for t in tasks if not is_waiting(t["status"])}
    result.chk(
        started <= claim_ids,
        "已开工任务都有认领登记；未登记=%s" % (sorted(started - claim_ids) or "无"),
        "registry",
    )

    # 认领必须有可追溯的时间与分支，否则占用对其他 AI 不可见
    incomplete = [
        c["task_id"]
        for c in claims
        if not re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", c["claimed_at"])
        or "—" in c["branch"]
    ]
    result.chk(
        not incomplete,
        "认领登记均有时间与分支；缺=%s" % (incomplete or "无"),
        "registry",
    )
    return tasks, claims


def check_cards(result, tasks_text, claims):
    """组 2：任务卡本身的完整性。"""
    cards = {c["fields"]["任务编号"]: c for c in parse_cards(tasks_text)}
    claim_ids = {c["task_id"] for c in claims}
    result.chk(
        claim_ids <= set(cards),
        "已认领任务都有任务卡；缺卡=%s" % (sorted(claim_ids - set(cards)) or "无"),
        "cards",
    )

    missing_truth = [
        task_id for task_id, card in cards.items() if not card["fields"].get("真相源引用")
    ]
    result.chk(
        not missing_truth,
        "任务卡均声明真相源引用；缺=%s" % (missing_truth or "无"),
        "cards",
    )

    missing_test_id = [task_id for task_id, card in cards.items() if not card["fields"].get("test_id")]
    result.chk(
        not missing_test_id,
        "任务卡均有 test_id（H-4：没有 test_id 的完成标准不可验收）；缺=%s"
        % (missing_test_id or "无"),
        "cards",
    )

    # 工作区（H-7）只对未完成的任务强制：字段是后加的，不追溯改写历史卡片
    unfinished = [
        task_id for task_id, card in cards.items()
        if not is_finished(card["fields"].get("当前状态", ""))
    ]
    without_worktree = [
        task_id for task_id in unfinished if not cards[task_id]["fields"].get("工作区")
    ]
    result.chk(
        not without_worktree,
        "未完成任务卡声明工作区（H-7）；缺=%s（检查 %d 张未完成卡）"
        % (without_worktree or "无", len(unfinished)),
        "cards",
    )

    # 完成的卡片必须留下可追溯的 commit 与验证证据（登记规则第 3 条）
    bad_commit = [
        task_id for task_id, card in cards.items()
        if is_finished(card["fields"].get("当前状态", "")) and not card["fields"].get("commit")
    ]
    result.chk(
        not bad_commit,
        "已完成任务卡均填 commit；缺=%s" % (bad_commit or "无"),
        "cards",
    )

    no_evidence = [
        task_id for task_id, card in cards.items()
        if is_finished(card["fields"].get("当前状态", ""))
        and not has_verification_evidence(card)
    ]
    result.chk(
        not no_evidence,
        "已完成任务卡均附验证证据；缺=%s" % (no_evidence or "无"),
        "cards",
    )
    return cards


def check_counts(result, repo_root):
    """组 3：README / dev-convention 声明的 docstate 断言数必须与实际相符。

    这条防的是「手工同步计数」—— 加断言后忘了改文档，文档就开始骗人。

    只看**同时提到 docstate 和项数**的行。否则会把 discipline 的项数也当成
    docstate 的数来比（语义混淆），而按固定正则抓整篇又会漏掉「88 项机械断言」
    这类中间夹字的写法 —— 那会让本文件被静默跳过，比抓错更糟。
    """
    sys.path.insert(0, os.path.join(repo_root, "tools"))
    try:
        from docstate.docstate import run as run_docstate
    except ImportError:
        result.chk(False, "无法导入 tools/docstate/docstate.py", "counts")
        return

    spec = os.path.join(repo_root, "docs", "spec", "0.3-R10.md")
    if not os.path.exists(spec):
        result.chk(False, "找不到 docstate 的输入规范 %s" % spec, "counts")
        return

    actual = len(run_docstate(spec).checks)
    result.chk(True, "docstate 实际断言数 = %d" % actual, "counts")

    docs = {
        "README.md": os.path.join(repo_root, "README.md"),
        "docs/dev-convention.md": os.path.join(repo_root, "docs", "dev-convention.md"),
    }
    stale = []
    unchecked = []
    ambiguous = 0
    for label, path in docs.items():
        if not os.path.exists(path):
            stale.append("%s（文件不存在）" % label)
            continue
        declared = set()
        for line in read(path).splitlines():
            if "docstate" not in line:
                continue
            if "discipline" in line:
                # 同一行提到两个工具时，数字指向哪个无法判定，跳过而不是猜。
                # 曾因此把 discipline 的 17 项误当成 docstate 的声明数。
                ambiguous += 1
                continue
            declared |= {int(n) for n in re.findall(r"(\d+)\s*项", line)}
        if not declared:
            unchecked.append(label)
            continue
        # 必须「全部」正确，而不是「有一处对就行」：README 里 docstate 的计数出现在
        # 两处，用并集判定会出现「一处改成 88、另一处留着 87」仍然全绿的情况，
        # 文档可以在一个地方撒谎而检查放行。
        wrong = sorted(n for n in declared if n != actual)
        if wrong:
            stale.append("%s 声明了错误计数 %s，实际 %d" % (label, wrong, actual))
    result.chk(
        not stale,
        "文档声明的 docstate 断言数与实际一致；不符=%s（跳过 %d 行同行提到两个工具的描述）"
        % (stale or "无", ambiguous),
        "counts",
    )
    result.chk(
        not unchecked,
        "两份文档都给出了 docstate 断言数（无法核对的：%s）" % (unchecked or "无"),
        "counts",
    )


def run(repo_root="."):
    result = Result()
    tasks_path = os.path.join(repo_root, DEFAULT_TASKS)
    if not os.path.exists(tasks_path):
        raise FileNotFoundError(tasks_path)

    tasks_text = read(tasks_path)
    _tasks, claims = check_registry(result, tasks_text)
    check_cards(result, tasks_text, claims)
    check_counts(result, repo_root)
    return result


def main():
    parser = argparse.ArgumentParser(description="协作纪律检查器")
    parser.add_argument("--repo", default=".", help="仓库根目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--verbose", action="store_true", help="打印通过项")
    args = parser.parse_args()

    try:
        result = run(args.repo)
    except FileNotFoundError as error:
        sys.stderr.write("not found: %s\n" % error)
        return 2

    if args.json:
        print(json.dumps({
            "total": len(result.checks),
            "passed": len(result.passed),
            "failed": len(result.failed),
            "checks": result.checks,
        }, ensure_ascii=False, indent=2))
    else:
        print("=== discipline: %s ===" % args.repo)
        groups = collections.OrderedDict()
        for check in result.checks:
            groups.setdefault(check["group"], []).append(check)
        for group, items in groups.items():
            bad = [i for i in items if not i["ok"]]
            print("[%s] %d 项，%d 失败" % (group, len(items), len(bad)))
            for item in items:
                if not item["ok"]:
                    print("   FAIL  " + item["msg"])
                elif args.verbose:
                    print("   ok    " + item["msg"])
        print("")
        print("结果: %d 项检查，%d 项失败" % (len(result.checks), len(result.failed)))

    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())