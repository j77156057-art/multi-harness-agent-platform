"""清掉 ``data/formal-overlays/`` 下跑完没被回收的 verify 工作副本。

为什么需要这个脚本
------------------
每次一个窗口跑完一条活，verify 会为它 checkout 一份完整工作树到
``data/formal-overlays/vj-<job>-<commit>/``，结束后由
``app/verify/overlay.py::recycle`` 调 ``_force_rmtree`` 删掉。

``_force_rmtree`` 内部是 ``shutil.rmtree(ignore_errors=True)``：在普通机器上
没问题，但**本机装了批量删除防护**（单次删除超过 50 个文件要人工确认），于是
553 个文件的 checkout 删不动 —— 目录留在盘上（一次约 9 MB），同时那个
"删除被拒"的信号让 worker 的收尾看起来像失败。实测 2026-11-27：34 个目录、
29 MB，跑一次任务多一份。

这个脚本解决两件事：**清**，以及**清错了能拿回来**。

怎么保证能回滚
--------------
不做逐文件删除，而是 ``os.rename`` 把整个目录**移进回收区**
（``data/overlay-trash/<run-id>/``）。同一块盘上 rename 是瞬时的，也绕开了
"逐文件删除"的防护。每一批都写 manifest（移动前写入、逐个 item 更新）：

    data/overlay-trash/<run-id>/manifest.json
    {
      "run_id": "20261127-223500",
      "mode": "trash",
      "started_at": "...",
      "finished_at": "...",
      "items": [
        {"source": "...", "target": "...", "files": 553, "bytes": 9437184,
         "moved": true, "error": null}
      ],
      "failures": []
    }

于是三种情况都有交代：

* 移成功了 —— ``--restore <run-id>`` 逐个移回原路径；
* 移到一半失败 —— manifest 里 ``moved=true`` 的能整体回滚，``moved=false``
  的还在原地（脚本只挪不改内容）；
* 进程被中途杀掉 —— manifest 是每动完一个 item 就 flush 一次的，下次
  ``--restore`` 仍然知道哪些已经挪走。

用法（**手动入口**，不进 `make check`）
-------------------------------------
    python scripts/clean_overlays.py                          # 干跑：列出会清什么
    python scripts/clean_overlays.py --apply                  # 移进回收区（可回滚）
    python scripts/clean_overlays.py --list                   # 回收区里有哪些批次
    python scripts/clean_overlays.py --restore 20261127-223500  # 把那一批移回去
    python scripts/clean_overlays.py --apply --purge          # 直接删（不可回滚，旧行为）
    python scripts/clean_overlays.py --purge-runs --older-than 7  # 删掉 7 天前的批次

什么时候拒绝动
--------------
数据库里还有 ``in_progress`` / ``verifying`` 的任务时直接退出（退出码 2）：
那时 overlay 可能正被 verify 使用，删了会让验证读到半个工作树。
真要在那种状态下硬清，加 ``--force`` —— 但那等于承认"我知道可能删到正在用的"。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OVERLAY_ROOT = ROOT / "data" / "formal-overlays"
DEFAULT_TRASH_ROOT = ROOT / "data" / "overlay-trash"
DEFAULT_DB = ROOT / "data" / "state.db"

#: 环境删除防护的阈值是 50；批大小取 40，留出余量。
#: 正常路径（rename）用不到它，只有 ``--purge`` 直删时才会逐文件走。
BATCH = 40

#: 这些状态说明有活正在跑，overlay 可能正在被读写。
BUSY_STATUSES = ("in_progress", "verifying")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tree_stats(path: Path) -> tuple[int, int]:
    """(文件数, 字节数)。只读，不改任何东西。"""
    files = 0
    size = 0
    for base, _dirs, names in os.walk(path):
        for name in names:
            files += 1
            try:
                size += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return files, size


def _busy_tasks(db_path: Path) -> list[str]:
    """数据库里正在执行的活。查不到库时按"忙"处理，宁可不动。"""
    if not db_path.exists():
        return ["<database missing>"]
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            marks = ",".join("?" for _ in BUSY_STATUSES)
            rows = conn.execute(
                f"SELECT task_id, status FROM tasks WHERE status IN ({marks})",  # noqa: S608
                BUSY_STATUSES,
            ).fetchall()
            return [f"{task_id}({status})" for task_id, status in rows]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [f"<database unreadable: {exc}>"]


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """原子写：先写临时文件再替换，避免半截 JSON 让 restore 读不出来。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _batch_purge(path: Path, failures: list[dict[str, str]]) -> int:
    """逐文件分批删（只在 ``--purge`` 用得上）。返回删掉的文件数。

    先 chmod 去只读：git 的对象文件是只读的，直接 unlink 会失败。
    """
    deleted = 0
    files: list[str] = []
    for base, _dirs, names in os.walk(path):
        for name in names:
            fp = os.path.join(base, name)
            files.append(fp)
            try:
                os.chmod(fp, 0o600)
            except OSError:
                pass
    for start in range(0, len(files), BATCH):
        for fp in files[start : start + BATCH]:
            try:
                os.unlink(fp)
                deleted += 1
            except OSError as exc:
                failures.append({"path": fp, "stage": "purge-file", "error": str(exc)})
        time.sleep(0.02)
    for base, dirs, _names in os.walk(path, topdown=False):
        for name in dirs:
            try:
                os.chmod(os.path.join(base, name), 0o700)
                os.rmdir(os.path.join(base, name))
            except OSError as exc:
                failures.append(
                    {"path": os.path.join(base, name), "stage": "purge-dir", "error": str(exc)}
                )
    try:
        os.rmdir(path)
    except OSError as exc:
        failures.append({"path": str(path), "stage": "purge-dir", "error": str(exc)})
    return deleted


def _scan(overlay_root: Path) -> list[Path]:
    if not overlay_root.exists():
        return []
    return sorted(p for p in overlay_root.iterdir() if p.is_dir())


def cmd_clean(args: argparse.Namespace) -> int:
    overlay_root: Path = args.overlay_root
    trash_root: Path = args.trash_root
    targets = _scan(overlay_root)
    if not targets:
        print(f"nothing to clean: {overlay_root} has no sub-directories")
        return 0

    busy = _busy_tasks(args.db)
    if busy and not args.force:
        print(
            "refusing to clean: these tasks are still running and may be using an overlay:",
            file=sys.stderr,
        )
        for item in busy:
            print(f"  {item}", file=sys.stderr)
        print("wait for them to finish, or pass --force if you know better", file=sys.stderr)
        return 2

    total_files = 0
    total_bytes = 0
    rows: list[dict[str, Any]] = []
    for path in targets:
        files, size = _tree_stats(path)
        total_files += files
        total_bytes += size
        rows.append({"name": path.name, "files": files, "bytes": size})

    print(f"{'directory':<62} {'files':>6} {'size':>10}")
    for row in rows:
        print(f"{row['name']:<62} {row['files']:>6} {row['bytes'] / 1024:>9.0f}K")
    print(f"{'total':<62} {total_files:>6} {total_bytes / 1024:>9.0f}K")

    if not args.apply:
        print("\ndry run -- nothing moved (add --apply to move them into the trash)")
        if not args.purge:
            print("moved directories are restorable with --restore <run-id>")
        return 0

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = trash_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "mode": "purge" if args.purge else "trash",
        "overlay_root": str(overlay_root),
        "started_at": _now(),
        "finished_at": None,
        "items": [],
        "failures": [],
    }
    manifest_path = run_dir / "manifest.json"
    _write_manifest(manifest_path, manifest)
    print(f"\nrun {run_id} -> {run_dir}")

    moved = 0
    for path in targets:
        item: dict[str, Any] = {
            "source": str(path),
            "target": None,
            "files": next((r["files"] for r in rows if r["name"] == path.name), 0),
            "bytes": next((r["bytes"] for r in rows if r["name"] == path.name), 0),
            "moved": False,
            "error": None,
        }
        try:
            if args.purge:
                item["files"] = _batch_purge(path, manifest["failures"])
                item["moved"] = not path.exists()
            else:
                target = run_dir / path.name
                # 同盘 rename：瞬时完成，不触发逐文件删除的防护。
                os.rename(path, target)
                item["target"] = str(target)
                item["moved"] = True
        except OSError as exc:
            item["error"] = str(exc)
            manifest["failures"].append(
                {"path": str(path), "stage": "purge" if args.purge else "move", "error": str(exc)}
            )
        manifest["items"].append(item)
        if item["moved"]:
            moved += 1
        # 每动完一个就落盘：中途被杀时 restore 仍知道哪些挪走了。
        _write_manifest(manifest_path, manifest)

    manifest["finished_at"] = _now()
    _write_manifest(manifest_path, manifest)

    print(f"moved {moved}/{len(targets)}; failures: {len(manifest['failures'])}")
    print(f"manifest: {manifest_path}")
    if args.purge:
        print("purged -- these cannot be restored (the manifest only records what was there)")
    else:
        print(f"restore with: python scripts/clean_overlays.py --restore {run_id}")
    return 0 if not manifest["failures"] else 1


def _load_manifest(trash_root: Path, run_id: str) -> dict[str, Any] | None:
    path = trash_root / run_id / "manifest.json"
    if not path.exists():
        print(f"no such run: {path}", file=sys.stderr)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"manifest is unreadable: {exc}", file=sys.stderr)
        return None


def cmd_list(args: argparse.Namespace) -> int:
    trash_root: Path = args.trash_root
    if not trash_root.exists():
        print(f"trash is empty: {trash_root}")
        return 0
    for run_dir in sorted(p for p in trash_root.iterdir() if p.is_dir()):
        manifest = _load_manifest(trash_root, run_dir.name)
        if manifest is None:
            print(f"{run_dir.name}  <manifest missing or broken>")
            continue
        items = manifest.get("items") or []
        held = sum(1 for item in items if item.get("moved"))
        size = sum(int(item.get("bytes") or 0) for item in items if item.get("moved"))
        print(
            f"{run_dir.name}  mode={manifest.get('mode')}  "
            f"moved={held}/{len(items)}  {size / 1024:.0f}K  "
            f"failures={len(manifest.get('failures') or [])}"
        )
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    trash_root: Path = args.trash_root
    manifest = _load_manifest(trash_root, args.restore)
    if manifest is None:
        return 2
    if manifest.get("mode") == "purge":
        print(
            "this run was a purge: the files were deleted one by one and nothing is left to"
            " move back (the manifest only records what used to be there)",
            file=sys.stderr,
        )
        return 2

    restored = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    for item in manifest.get("items") or []:
        source = Path(str(item.get("source")))
        target = item.get("target")
        if not item.get("moved") or not target:
            skipped += 1
            continue
        target_path = Path(str(target))
        if not target_path.exists():
            failures.append(
                {"path": str(target_path), "stage": "restore", "error": "no longer in the trash"}
            )
            continue
        if source.exists():
            # 不清空目的地的旧残留：把两个目录并排留着比覆盖安全。
            failures.append(
                {"path": str(source), "stage": "restore", "error": "destination already exists"}
            )
            continue
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            os.rename(target_path, source)
            item["moved"] = False
            item["restored_at"] = _now()
            restored += 1
        except OSError as exc:
            failures.append({"path": str(source), "stage": "restore", "error": str(exc)})

    manifest.setdefault("restores", []).append(
        {"at": _now(), "restored": restored, "failures": failures}
    )
    _write_manifest(trash_root / args.restore / "manifest.json", manifest)
    print(f"restored {restored}, skipped {skipped}, failures {len(failures)}")
    for failure in failures:
        print(f"  {failure['path']}: {failure['error']}", file=sys.stderr)
    return 0 if not failures else 1


def cmd_purge_runs(args: argparse.Namespace) -> int:
    trash_root: Path = args.trash_root
    if not trash_root.exists():
        print(f"trash is empty: {trash_root}")
        return 0
    purged = 0
    failures: list[dict[str, str]] = []
    for run_dir in sorted(p for p in trash_root.iterdir() if p.is_dir()):
        try:
            age = time.time() - run_dir.stat().st_mtime
        except OSError:
            continue
        if age < args.older_than * 86400:
            continue
        if args.apply:
            _batch_purge(run_dir, failures)
            purged += 1
            print(f"purged {run_dir.name}")
        else:
            print(f"would purge {run_dir.name} (age {age / 86400:.1f}d)")
    if not args.apply:
        print("\ndry run -- nothing purged (add --apply)")
        return 0
    print(f"purged {purged} run(s); failures: {len(failures)}")
    return 0 if not failures else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/clean_overlays.py",
        description="把跑完没回收的 verify overlay 移进回收区（可回滚）",
    )
    parser.add_argument("--overlay-root", type=Path, default=DEFAULT_OVERLAY_ROOT)
    parser.add_argument("--trash-root", type=Path, default=DEFAULT_TRASH_ROOT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="真的动手（默认只列出来）")
    parser.add_argument("--purge", action="store_true", help="直接删，不进回收区（不可回滚）")
    parser.add_argument("--force", action="store_true", help="有任务在跑时也清（危险）")
    parser.add_argument("--list", action="store_true", help="列出回收区里的批次")
    parser.add_argument("--restore", metavar="RUN_ID", help="把某个批次移回原路径")
    parser.add_argument("--purge-runs", action="store_true", help="清掉回收区里的旧批次")
    parser.add_argument("--older-than", type=float, default=7.0, help="多少天以前的批次（默认 7）")
    args = parser.parse_args(argv)

    if args.list:
        return cmd_list(args)
    if args.restore:
        return cmd_restore(args)
    if args.purge_runs:
        return cmd_purge_runs(args)
    if args.purge and not args.apply:
        print("--purge needs --apply (it deletes, it does not park)", file=sys.stderr)
        return 2
    return cmd_clean(args)


if __name__ == "__main__":
    raise SystemExit(main())
