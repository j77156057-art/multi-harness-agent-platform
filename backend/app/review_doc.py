"""The final plan, as a document a human can read and sign off.

After the administrator has refined the requirement, circulated the plan and
folded the comments in, the result is still just rows in a database.  A person
has to be able to *read* it -- and to see who said what while it was being
shaped.  So the platform renders it:

* **Markdown** -- the plain document (the goal, the tasks, the acceptance, the
  circulation rounds and their comments);
* **HTML** -- the same content as a page, so the office can show it without
  asking the reader to install anything.

Both files are written next to the platform's other exports, and the paths are
reported so the group can point at them.

The renderer is deliberately dumb: it reads the plan, its newest snapshot and
the circulation rounds, and formats them.  It decides nothing.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.db.connection import Database

#: The statuses that mean "this task fell over" (the office's failure panel).
FAILED_STATES = ("abandoned", "stale")


def _plan_rows(database: Database, plan_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    plan = database.read("SELECT * FROM plans WHERE plan_id = ?", (plan_id,))
    if not plan:
        return {}, None
    snapshots = database.read(
        "SELECT * FROM plan_snapshots WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1",
        (plan_id,),
    )
    return dict(plan[0]), (dict(snapshots[0]) if snapshots else None)


def _snapshot_tasks(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    try:
        raw = json.loads(str(snapshot.get("content_json") or "{}"))
    except ValueError:
        return []
    tasks = raw.get("tasks")
    return [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []


def _circulation(database: Database, plan_id: str) -> list[dict[str, Any]]:
    """Every circulation round of this plan, with the comments it collected."""
    rounds = database.read(
        "SELECT * FROM circulation_rounds WHERE plan_id = ? ORDER BY round_no",
        (plan_id,),
    )
    out: list[dict[str, Any]] = []
    for row in rounds:
        round_id = str(row["circulation_id"])
        try:
            reviewers = json.loads(str(row["reviewer_harness_ids_json"] or "[]"))
        except ValueError:
            reviewers = []
        reviews = database.read(
            "SELECT harness_id, body, submitted_at FROM circulation_reviews"
            " WHERE circulation_id = ? AND round_no = ? ORDER BY submitted_at",
            (round_id, int(row["round_no"])),
        )
        out.append(
            {
                "circulation_id": round_id,
                "round_no": int(row["round_no"]),
                "state": str(row["state"]),
                "opened_by": str(row["opened_by"] or ""),
                "reviewer_harness_ids": [str(item) for item in reviewers],
                "reviews": [
                    {
                        "harness_id": str(review["harness_id"]),
                        "body": str(review["body"] or ""),
                        "submitted_at": str(review["submitted_at"] or ""),
                    }
                    for review in reviews
                ],
            }
        )
    return out


def render_markdown(
    *,
    project_name: str,
    plan_id: str,
    goal: str,
    tasks: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
) -> str:
    """The document, in Markdown."""
    lines: list[str] = []
    lines.append(f"# {project_name} · 方案定稿")
    lines.append("")
    lines.append(f"- 方案编号：`{plan_id}`")
    lines.append(f"- 任务数：{len(tasks)}")
    lines.append("")
    if goal:
        lines.append("## 目标")
        lines.append("")
        lines.append(goal.strip())
        lines.append("")
    lines.append("## 任务")
    lines.append("")
    for index, task in enumerate(tasks, start=1):
        title = str(task.get("title") or task.get("id") or f"任务 {index}")
        lines.append(f"### {index}. {title}")
        lines.append("")
        lines.append(f"- 任务号：`{task.get('id', '')}`")
        targets = task.get("assigned_harness_ids") or []
        if targets:
            lines.append(f"- 执行窗口：{', '.join(str(item) for item in targets)}")
        lines.append("")
        goal = str(task.get("goal") or "").strip()
        if goal:
            lines.append("**工单**")
            lines.append("")
            lines.append(goal)
            lines.append("")
        acceptance = task.get("acceptance")
        if isinstance(acceptance, str):
            acceptance = [{"description": acceptance}]
        if isinstance(acceptance, list) and acceptance:
            lines.append("**验收**")
            lines.append("")
            for item in acceptance:
                if isinstance(item, dict):
                    description = str(item.get("description") or "").strip()
                    check = str(item.get("check") or "")
                    lines.append(f"- {description}" + (f"（{check}）" if check else ""))
                else:
                    lines.append(f"- {str(item).strip()}")
            lines.append("")
        paths = task.get("exclusive_paths") or []
        if paths:
            lines.append("**允许改动的路径**")
            lines.append("")
            for path in paths:
                lines.append(f"- `{path}`")
            lines.append("")
    if rounds:
        lines.append("## 传阅记录")
        lines.append("")
        for round_info in rounds:
            lines.append(f"### 第 {round_info['round_no']} 轮（{round_info['state']}）")
            lines.append("")
            lines.append(f"- 传阅对象：{', '.join(round_info['reviewer_harness_ids']) or '—'}")
            lines.append(f"- 发起：{round_info['opened_by'] or '—'}")
            lines.append("")
            if not round_info["reviews"]:
                lines.append("_本轮没有意见。_")
                lines.append("")
            for review in round_info["reviews"]:
                lines.append(f"**{review['harness_id']}**")
                lines.append("")
                lines.append(review["body"].strip())
                lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("这份方案由管理员 Harness 细化并传阅完善；**由人最终审核**后才会派单。")
    return "\n".join(lines) + "\n"


def render_html(markdown_text: str, *, title: str) -> str:
    """A readable page.

    The renderer is small on purpose: it turns the Markdown-ish structure into
    HTML with escaping, not a Markdown parser.  Everything that comes from a
    model or a person is escaped -- this document ends up in a browser.
    """
    body_lines: list[str] = []
    in_code = False
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            body_lines.append("</ul>")
            in_list = False

    for raw in markdown_text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            close_list()
            body_lines.append("<pre><code>" if not in_code else "</code></pre>")
            in_code = not in_code
            continue
        if in_code:
            body_lines.append(html.escape(line))
            continue
        if not line.strip():
            close_list()
            continue
        if line.startswith("### "):
            close_list()
            body_lines.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("## "):
            close_list()
            body_lines.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            close_list()
            body_lines.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("- "):
            if not in_list:
                body_lines.append("<ul>")
                in_list = True
            body_lines.append(f"<li>{_inline(line[2:])}</li>")
        elif line.startswith("> "):
            close_list()
            body_lines.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.startswith("---"):
            close_list()
            body_lines.append("<hr>")
        else:
            close_list()
            body_lines.append(f"<p>{_inline(line)}</p>")
    close_list()
    body = "\n".join(body_lines)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body {{ font: 15px/1.7 system-ui, "Microsoft YaHei", sans-serif; max-width: 52rem;
         margin: 2rem auto; padding: 0 1.2rem; color: #1f1f1f; background: #fbfaf7; }}
  h1 {{ font-size: 1.6rem; border-bottom: 2px solid #2f6f4f; padding-bottom: .4rem; }}
  h2 {{ font-size: 1.25rem; margin-top: 2rem; color: #2f6f4f; }}
  h3 {{ font-size: 1.05rem; margin-top: 1.4rem; }}
  pre {{ background: #f2efe8; padding: .8rem; overflow-x: auto; border-radius: 6px; }}
  code {{ font-family: ui-monospace, Consolas, monospace; }}
  li {{ margin: .25rem 0; }}
  blockquote {{ border-left: 3px solid #cfc9bd; margin: .6rem 0; padding: .2rem .8rem; color: #555; }}
  hr {{ border: 0; border-top: 1px solid #ded8cc; margin: 2rem 0; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _inline(text: str) -> str:
    """Escape, then keep the inline code and the bold markers readable."""
    escaped = html.escape(text)
    escaped = escaped.replace("`", "<code>")
    escaped = escaped.replace("**", "")
    return escaped


def build_document(
    database: Database,
    *,
    plan_id: str,
    project_name: str = "",
    goal: str = "",
    export_dir: Path | None = None,
) -> dict[str, Any]:
    """Render the plan document and write the MD/HTML files.

    Returns the document itself plus where it was written, so the caller can
    point a human at it.
    """
    plan, snapshot = _plan_rows(database, plan_id)
    tasks = _snapshot_tasks(snapshot)
    rounds = _circulation(database, plan_id)
    title = f"{project_name} · 方案定稿" if project_name else "方案定稿"
    markdown_text = render_markdown(
        project_name=project_name or str(plan.get("project_id") or "方案"),
        plan_id=plan_id,
        goal=goal,
        tasks=tasks,
        rounds=rounds,
    )
    html_text = render_html(markdown_text, title=title)
    directory = Path(export_dir or settings.export_dir) / "plans"
    directory.mkdir(parents=True, exist_ok=True)
    revision = len(rounds)
    stem = f"plan-{plan_id}-r{revision}"
    md_path = directory / f"{stem}.md"
    html_path = directory / f"{stem}.html"
    md_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    return {
        "plan_id": plan_id,
        "title": title,
        "revision": revision,
        "markdown": markdown_text,
        "html": html_text,
        "markdown_path": str(md_path),
        "html_path": str(html_path),
        "tasks": len(tasks),
        "rounds": rounds,
    }
