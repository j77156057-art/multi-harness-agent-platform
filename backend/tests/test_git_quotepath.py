"""非 ASCII 路径曾让"写在范围内的文件"被判成越界。

实测：管理员把目标细化成了带中文目录的任务（``docs/prep/自循环验收-1020/...``），
成员 CLI **只**写了被允许的那一个文件，平台却拒绝提交：

    RuntimeError: platform commit refused: dirty path outside exclusive_paths

原因不在成员：``git status --porcelain`` 默认把非 ASCII 路径**加引号并转义**
（``"docs/prep/\\350\\207\\250..."``），带空格的路径也会加引号。范围检查拿这个
转义后的字符串去比对授权路径，自然对不上。

修法：平台每次调 git 都带上 ``core.quotepath=false``（``app.git.repo.run_git``），
并且在范围检查前把仍然带引号的路径还原（``formal_worker._unquote_git_path``）。

同一个 ``run_git`` 还钉了 ``core.autocrlf=true``——机制一样（环境层
``GIT_CONFIG_*``），事故也一样安静：2026-11-21 一次提交把 20 个文件写成 CRLF
blob，40 行改动变成 17.5k 行整文件重写，而测试全绿。行尾那组用例在本文件末尾。
"""

from __future__ import annotations

from pathlib import Path

from app.adapter.formal_worker import _unquote_git_path
from app.adapter.scope import path_in_scope
from app.git.repo import run_git

CHINESE = "docs/prep/自循环验收-1020/source-inventory.md"
SPACED = "docs/with space.md"


def _repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    run_git("init", "-q", str(work), cwd=work)
    run_git("config", "user.email", "a@b.c", cwd=work)
    run_git("config", "user.name", "t", cwd=work)
    (work / "README.md").write_text("seed\n", encoding="utf-8")
    (work / "docs").mkdir()
    (work / "docs" / ".keep").write_text("", encoding="utf-8")
    run_git("add", "-A", cwd=work)
    run_git("commit", "-qm", "seed", cwd=work)
    return work


def test_git_returns_raw_paths_for_non_ascii_and_spaces(tmp_path):
    """平台自己的 git 调用必须拿到原始路径（这是修好的前提）。

    真实场景：成员在一个**新的**中文目录下写文件，``git status --porcelain``
    报的是那个目录（``?? docs/prep/自循环验收-1020/``），平台再用
    ``ls-files --others`` 把它展开成文件。两条都得是原始路径。
    """
    work = _repo(tmp_path)
    (work / "docs" / "prep" / "自循环验收-1020").mkdir(parents=True)
    (work / "docs" / "prep" / "自循环验收-1020" / "source-inventory.md").write_text(
        "x\n", encoding="utf-8"
    )
    (work / "docs").mkdir(exist_ok=True)
    (work / "docs" / "with space.md").write_text("y\n", encoding="utf-8")

    status = run_git("status", "--porcelain", cwd=work).stdout
    others = run_git("ls-files", "--others", "--exclude-standard", cwd=work).stdout.splitlines()

    # git 把未跟踪的目录折叠成 `?? docs/prep/`，平台看到结尾是 "/" 就用
    # `ls-files --others` 展开成文件——那一步必须拿到原始路径。
    assert "?? docs/prep/" in status, status
    assert CHINESE in others, others
    assert SPACED in others, others


def test_a_quoted_spaced_path_is_still_quoted_by_git(tmp_path):
    """``core.quotepath=false`` 只管非 ASCII；带空格的路径 git 照样加引号。

    所以范围检查前面必须把引号还原——这就是 ``_unquote_git_path`` 存在的原因。
    """
    work = _repo(tmp_path)
    (work / "docs" / "with space.md").write_text("y\n", encoding="utf-8")

    status = run_git("status", "--porcelain", cwd=work).stdout

    assert '"docs/with space.md"' in status, status
    assert _unquote_git_path('"docs/with space.md"') == "docs/with space.md"


def test_git_returns_raw_paths_for_tracked_non_ascii_files(tmp_path):
    """已跟踪的非 ASCII 文件（改动后）也必须是原始路径。"""
    work = _repo(tmp_path)
    target = work / "docs" / "prep" / "自循环验收-1020" / "source-inventory.md"
    target.parent.mkdir(parents=True)
    target.write_text("x\n", encoding="utf-8")
    run_git("add", "-A", cwd=work)
    run_git("commit", "-qm", "add", cwd=work)
    target.write_text("y\n", encoding="utf-8")

    status = run_git("status", "--porcelain", cwd=work).stdout
    changed = run_git("diff", "--name-only", cwd=work).stdout.splitlines()

    assert CHINESE in status, status
    assert CHINESE in changed, changed


def test_a_quoted_path_is_unwrapped():
    assert _unquote_git_path('"docs/with space.md"') == "docs/with space.md"
    # 自循环 = E8 87 AA E5 BE AA E7 8E AF, 本 = E6 9C AC
    assert (
        _unquote_git_path(
            '"docs/prep/\\350\\207\\252\\345\\276\\252\\347\\216\\257/\\346\\234\\254.md"'
        )
        == "docs/prep/自循环/本.md"
    )
    # A plain path is returned untouched.
    assert _unquote_git_path("docs/plain.md") == "docs/plain.md"


def test_the_scope_check_accepts_a_non_ascii_authorized_path():
    """The authorized path and the git path must compare equal."""
    assert path_in_scope(CHINESE, [CHINESE]) is True
    assert path_in_scope(SPACED, [SPACED]) is True
    assert path_in_scope("docs/prep/别的/file.md", [CHINESE]) is False


# --------------------------------------------------------------------------
# 行尾（``core.autocrlf=true``，2026-11-21 事故的钉子）
#
# 工作区是 CRLF、历史是 LF 时，一个没做归一化的 ``git add`` 会把 CRLF 原样
# 存进 blob：文件内容对、测试全绿，唯一的痕迹是整文件重写的 diff。
# ``run_git`` 现在对**所有**调用钉 ``core.autocrlf=true``，这一组用例守住它。
# --------------------------------------------------------------------------


def _blob_size(work: Path, spec: str) -> int:
    """The committed blob's size in **bytes**.

    Deliberately not ``cat-file -p``'s text: ``run_git`` runs git in text mode,
    where universal newlines turn ``"a\\r\\nb"`` into ``"a\\nb"`` -- asserting
    "no CR in stdout" would pass even against a CRLF blob. The size is the only
    byte-level evidence that survives that translation.
    """
    commit = run_git("rev-parse", spec, cwd=work).stdout.strip()
    return int(run_git("cat-file", "-s", commit, cwd=work).stdout.strip())


def test_add_normalises_a_crlf_working_tree_to_an_lf_blob(tmp_path):
    """CRLF 工作区提交后，blob 里必须是 LF（事故的直接症状）。"""
    work = _repo(tmp_path)
    (work / "crlf.txt").write_bytes(b"a\r\nb\r\n")

    run_git("add", "crlf.txt", cwd=work)
    run_git("commit", "-qm", "crlf", cwd=work)

    blob = run_git("cat-file", "-p", "HEAD:crlf.txt", cwd=work).stdout
    assert "\r" not in blob, repr(blob)
    assert blob == "a\nb\n", repr(blob)
    # 字节证据：CRLF 版是 6 字节，LF 版是 4 字节。
    assert _blob_size(work, "HEAD:crlf.txt") == len(b"a\nb\n")


def test_a_crlf_working_tree_is_not_dirty_after_the_commit(tmp_path):
    """归一化只发生在 add 一侧，不能让工作区看起来有假脏。

    否则 ``git status --porcelain`` 会一直报这个文件被改过，范围检查/提交判定
    就会跟着误判成「成员改了范围外的文件」。
    """
    work = _repo(tmp_path)
    (work / "crlf.txt").write_bytes(b"a\r\nb\r\n")
    run_git("add", "crlf.txt", cwd=work)
    run_git("commit", "-qm", "crlf", cwd=work)

    status = run_git("status", "--porcelain", cwd=work).stdout

    assert status.strip() == "", status
    # 工作区那份还是 CRLF（autocrlf=true 只归一化入库方向，不回去改人的文件）。
    assert (work / "crlf.txt").read_bytes() == b"a\r\nb\r\n"


def test_a_pure_lf_file_is_committed_byte_for_byte(tmp_path):
    """反向：本来就是 LF 的文件不能被钉子改动一个字节。"""
    work = _repo(tmp_path)
    (work / "lf.txt").write_bytes(b"x\ny\n")

    run_git("add", "lf.txt", cwd=work)
    run_git("commit", "-qm", "lf", cwd=work)

    assert run_git("cat-file", "-p", "HEAD:lf.txt", cwd=work).stdout == "x\ny\n"
    assert _blob_size(work, "HEAD:lf.txt") == len(b"x\ny\n")
    assert (work / "lf.txt").read_bytes() == b"x\ny\n"


def test_a_one_line_edit_in_a_crlf_working_tree_stays_a_one_line_diff(tmp_path):
    """事故的镜像：40 行文件里改一行，只能是一行 diff。

    修之前这里会是 41 增 / 40 删——就是那次 17500 行的 ``flat-office.js``。
    """
    work = _repo(tmp_path)
    lines = [f"line {i}" for i in range(1, 41)]
    target = work / "flat-office.js"
    target.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))  # 历史是 LF
    run_git("add", "flat-office.js", cwd=work)
    run_git("commit", "-qm", "seed", cwd=work)

    # Windows 的 checkout/编辑器把整份文件写成 CRLF，人只改了一行。
    edited = lines[:20] + ["line CHANGED"] + lines[21:]
    target.write_bytes(("\r\n".join(edited) + "\r\n").encode("utf-8"))
    run_git("add", "flat-office.js", cwd=work)
    run_git("commit", "-qm", "edit", cwd=work)

    numstat = run_git("show", "--numstat", "--pretty=format:", "HEAD", cwd=work).stdout.split()

    assert numstat[:2] == ["1", "1"], numstat


def test_the_pins_survive_a_caller_that_brings_its_own_git_config(tmp_path):
    """调用方自带 ``GIT_CONFIG_*`` 时，钉子不能被整片顶掉。

    ``env_extra`` 曾经在钉子写完之后才 ``env.update``：调用方只要带一个
    ``GIT_CONFIG_COUNT``，``core.autocrlf`` 就读回空、``core.quotepath`` 也被顶掉
    ——钉子「看起来还在代码里」，实际一个都没生效。所以这里断言两半：调用方的
    key 活着（``core.abbrev=12`` 真的改变 ``rev-parse --short`` 的长度），钉子也
    活着（CRLF 工作区仍然存成 LF blob）。
    """
    work = _repo(tmp_path)
    caller = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.abbrev",
        "GIT_CONFIG_VALUE_0": "12",
    }
    (work / "crlf.txt").write_bytes(b"a\r\nb\r\n")

    run_git("add", "crlf.txt", cwd=work, env_extra=caller)
    run_git("commit", "-qm", "crlf", cwd=work, env_extra=caller)

    # 调用方的 key 还在（它占 index 0，钉子接在它后面）。
    short = run_git("rev-parse", "--short", "HEAD", cwd=work, env_extra=caller).stdout.strip()
    assert len(short) == 12, short
    # 钉子也还在：autocrlf 读回空的话，这里会是 6 字节的 CRLF blob。
    assert _blob_size(work, "HEAD:crlf.txt") == len(b"a\nb\n")
    assert run_git("config", "--get", "core.autocrlf", cwd=work).stdout.strip() == "true"


def test_only_autocrlf_false_reproduces_the_incident(tmp_path):
    """注释写的是「``false`` 才复现，``input`` 不会」——这条断言守住那句话。

    实测（这个用例本身即证据）：``input`` 在 add 方向照样 CRLF→LF，只有
    ``false``（或 unset 且没有任何一层配置给 ``true``）才会把 CRLF 原样入库。
    谁要验证「钉子是否多余」，拿 ``input`` 去自测会得到「不复现」的结论，然后
    把钉子删掉——错误注释能把下一个人带到的就是这个坑，所以两半都钉住。
    """
    work = _repo(tmp_path)

    # ``-c`` 排在环境里的钉子之后，能盖过它——这就是「有人手工自测」的路径。
    (work / "false.txt").write_bytes(b"a\r\nb\r\n")
    run_git("-c", "core.autocrlf=false", "add", "false.txt", cwd=work)
    run_git("-c", "core.autocrlf=false", "commit", "-qm", "false", cwd=work)

    (work / "input.txt").write_bytes(b"a\r\nb\r\n")
    run_git("-c", "core.autocrlf=input", "add", "input.txt", cwd=work)
    run_git("-c", "core.autocrlf=input", "commit", "-qm", "input", cwd=work)

    # false：CRLF 原样入库（6 字节）——事故就长这样。
    assert _blob_size(work, "HEAD:false.txt") == len(b"a\r\nb\r\n")
    # input：add 方向照样归一化（4 字节）——它复现不了事故。
    assert _blob_size(work, "HEAD:input.txt") == len(b"a\nb\n")
