"""A model served by a harness CLI -- the platform's "quota slot".

Some model quota simply cannot be called as an HTTP API.  OpenCode's free
tier says so in as many words: *"OpenCode's free tier can only be used from
within OpenCode."*  The only way to spend that quota is to let the CLI spend
it.

So the pool can serve such a model by **running the CLI as the execution
slot** and reading its answer:

* the CLI runs in a throw-away directory, so it cannot touch the repository
  (or the person's files) while it answers;
* the work order is explicit: answer the question, do not use tools;
* the answer is the last text event of the CLI's own JSON output.

The work still happens in the *calling* harness's slot: the pool only supplies
the model behind it.  That is the whole point of the pool -- the quota moves,
the execution does not.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.pool.cli_usage import opencode_message_usage

#: What the CLI is told before the question.  The CLI is an agent with tools;
#: for a model call it must answer, not work.
CLI_MODEL_PREAMBLE = (
    "Answer the question below. Reply with the answer only. "
    "Do not use tools, do not read or write files, do not run commands."
)


@dataclass(frozen=True, slots=True)
class CliModelSource:
    """One model served by a CLI.

    The CLI's own contract decides the rest: codebuddy takes the question on
    stdin (a multi-line positional prompt is truncated), qoder takes it as a
    positional argument, and opencode can attach a file.  Each slot therefore
    declares its own argv.
    """

    name: str
    #: The model as the CLI names it (``provider/model``).
    model: str
    #: The CLI binary (resolved on PATH).
    binary: str = ""
    #: The arguments before the question.  ``{workdir}`` is replaced with the
    #: throw-away directory the CLI runs in.
    args: tuple[str, ...] = ()
    #: True when the question travels on stdin instead of argv.
    prompt_via_stdin: bool = False
    #: The flag a long question is attached with (opencode: ``--file``).
    file_arg: str | None = None
    #: The flag the question is passed as (mmx: ``--message``).  When it is
    #: None the question is the last positional argument.
    message_flag: str | None = None
    timeout_seconds: float = 900.0
    #: Which of the CLI's own stores holds its usage, when the CLI does not
    #: put it on stdout.  Declared, not sniffed: "this CLI keeps a store we
    #: know how to read" is a fact about the slot, not a guess at runtime.
    store: str = ""
    #: The working directory the CLI runs in (a throw-away one by default).
    workdir: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def resolve_binary(self) -> str:
        """The CLI to run.

        PATH first; then the places the installers actually write to, because
        a long-running worker often has an older PATH than the installer left
        behind (the qoder CN installer writes its own directory).
        """
        if self.binary and (Path(self.binary).exists() or os.path.sep in self.binary):
            return self.binary
        wanted = self.binary or "opencode"
        found = shutil.which(wanted) or shutil.which(f"{wanted}.cmd")
        if (
            found
            and os.name == "nt"
            and not found.lower().endswith((".exe", ".cmd", ".bat", ".com"))
        ):
            for suffix in (".cmd", ".exe", ".bat"):
                candidate = found + suffix
                if os.path.exists(candidate):
                    return candidate
        if found:
            return found
        fallbacks = {
            "qoderclicn": [
                Path.home() / ".qoder-cn" / "bin" / "qoderclicn" / "qoderclicn.exe",
                Path(r"D:\study\tools\QoderWork CN\resources\bin\qoderclicn.exe"),
            ],
            "codebuddy": [
                Path.home() / ".codebuddy" / "bin" / "codebuddy.exe",
            ],
        }
        for candidate in fallbacks.get(wanted, []):
            if candidate.exists():
                return str(candidate)
        return wanted

    def _argv(self, prompt: str, workdir: Path) -> list[str]:
        """The command line for one model call.

        Two Windows facts shape it:

        * the npm shim is a ``.cmd`` file, and cmd.exe hands the CLI only the
          **first line** of an argument -- so a question that travels as an
          argument is collapsed to one line, and a long one travels in a file
          the CLI attaches instead;
        * the WindowsApps ``bash`` alias drops argv, so the bash recipe is
          only used when a real bash is installed.
        """
        binary = self.resolve_binary()
        args = [part.replace("{workdir}", str(workdir)) for part in self.args]
        base = [binary, *args, "--model", self.model]
        one_line = " ".join(part.strip() for part in prompt.splitlines() if part.strip())
        if self.message_flag:
            # The question is a flag value, so it never becomes a positional
            # argument (and cmd.exe cannot truncate it).
            return [*base, self.message_flag, one_line]
        if len(one_line) <= 1500:
            return [*base, one_line]
        if not self.file_arg:
            return [*base, one_line]
        # Long question: let the CLI read it from a file (it supports --file).
        question_file = workdir / "question.txt"
        question_file.write_text(prompt, encoding="utf-8", newline="\n")
        return [
            *base,
            self.file_arg,
            str(question_file),
            "Answer the question in the attached file.",
        ]

    def _through_bash(self, argv: list[str]) -> list[str]:
        """Run the CLI through bash on Windows.

        The npm shims are ``.cmd`` files: cmd.exe mangles an argument that
        contains newlines, and the CLI then only ever sees the first line of
        the question (it answers "no question was provided").  The same recipe
        the harness adapters use -- ``bash -lc 'exec "$0" "$@"'`` -- hands the
        argv to the real binary untouched.
        """
        if os.name != "nt":
            return argv
        bash = shutil.which("bash")
        if not bash:
            return argv
        if Path(bash).name.lower() == "bash.exe" and "windowsapps" in bash.lower():
            # The WindowsApps alias is a WSL launcher that drops argv after -c.
            return argv
        return ["bash", "-lc", 'exec "$0" "$@"', *argv]

    def prompt_for(self, messages: list[dict[str, Any]]) -> str:
        """Flatten an OpenAI-style message list into one work order.

        The CLI is an agent with its own system prompt, so the message has to
        look like a *question*, not like a transcript: the earlier version
        (role markers, a trailing "[assistant]") made the agent answer "no
        question was provided".
        """
        system: list[str] = []
        question = ""
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            content = message.get("content")
            if isinstance(content, list):
                text = "\n".join(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("text")
                )
            else:
                text = str(content or "")
            if not text.strip():
                continue
            if role == "system":
                system.append(text)
            elif role == "user":
                question = text
        parts = [CLI_MODEL_PREAMBLE]
        if system:
            parts.append("Context:\n" + "\n\n".join(system))
        parts.append("Question:\n" + (question or "(no question was given)"))
        return "\n\n".join(parts)

    def complete(self, messages: list[dict[str, Any]]) -> str:
        """Run the CLI once and return its answer."""
        return self.run(messages)["text"]

    def run(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Run the CLI once and report what it said: text, usage, session.

        The usage and the session id are the **CLI's own** numbers, taken
        from the CLI's own output.  A CLI that does not report them simply
        has none here -- the pool does not invent them.
        """
        prompt = self.prompt_for(messages)
        with tempfile.TemporaryDirectory(prefix="pool-cli-") as directory:
            workdir = Path(directory)
            argv = self._through_bash(self._argv(prompt, workdir))
            try:
                completed = subprocess.run(
                    argv,
                    cwd=self.workdir or workdir,
                    input=prompt if self.prompt_via_stdin else None,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(f"{argv[0]} is not available on this host") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"the CLI slot {self.name} ran out of time after {self.timeout_seconds}s"
                ) from exc
        if completed.returncode != 0:
            raise RuntimeError(
                f"the CLI slot {self.name} failed ({completed.returncode}): "
                f"{(completed.stderr or completed.stdout or '')[-300:]}"
            )
        report = extract_report(completed.stdout)
        if report.get("usage") is None and self.store and report.get("message_ids"):
            # The CLI did not put its usage on stdout; it kept it in its own
            # store, and the message ids are the exact key into it.
            usage = self._usage_from_store(report["message_ids"])
            if usage is not None:
                report["usage"] = usage
                report["usage_source"] = "the CLI's own store"
        return report

    def _usage_from_store(self, message_ids: list[str]) -> dict[str, int] | None:
        """The usage the CLI recorded in its own store, if we know that store."""
        if self.store == "opencode":
            return opencode_message_usage(message_ids)
        return None


def extract_report(stdout: str) -> dict[str, Any]:
    """What a CLI's own output says: the answer, its usage, its session.

    The usage is **the CLI's own number** -- the Claude-Code-shaped stream
    (``{"type": "result", "usage": {...}}``) reports the turn's totals, and
    the opencode-shaped stream reports the session id, which is what makes a
    record traceable back to the CLI's own store.  A CLI that reports neither
    simply has no usage here: the pool does not guess one.
    """
    report: dict[str, Any] = {
        "text": "",
        "usage": None,
        "credits": None,
        "session_id": None,
        "message_ids": [],
    }
    # Some CLIs answer with **one** JSON document (the mmx CLI does), others
    # with one event per line (the opencode CLI does).  Try the whole thing
    # first, then the line by line reading.
    try:
        whole = json.loads(stdout)
    except ValueError:
        whole = None
    if isinstance(whole, dict):
        _read_event(whole, report)
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        _read_event(event, report)
    if not report["text"]:
        report["text"] = stdout.strip()[-4000:]
    return report


def _read_event(event: dict[str, Any], report: dict[str, Any]) -> None:
    """Take what one event of a CLI's output says."""
    session = event.get("session_id") or event.get("sessionID")
    if isinstance(session, str) and session.strip():
        report["session_id"] = session.strip()
    # The message id is the **exact** key into the CLI's own store: it is
    # how a turn's messages are found without guessing which ones they were.
    part = event.get("part")
    candidates = []
    if isinstance(part, dict):
        candidates.append(part.get("messageID"))
    message = event.get("message")
    if isinstance(message, dict):
        candidates.append(message.get("id"))
    for candidate in candidates:
        if (
            isinstance(candidate, str)
            and candidate.strip()
            and candidate not in report["message_ids"]
        ):
            report["message_ids"].append(candidate)
    usage = event.get("usage")
    if not isinstance(usage, dict) and isinstance(event.get("message"), dict):
        message_usage = event["message"].get("usage")
        if isinstance(message_usage, dict):
            usage = message_usage
    if isinstance(usage, dict) and usage:
        counts = _usage_counts(usage)
        # A CLI that reports the **shape** of a usage but not the numbers
        # (the qoder CLI does: its provider counts credits, not tokens) has
        # not told us the usage -- an all-zero usage is that, not a
        # measurement of zero.
        if counts["prompt_tokens"] or counts["completion_tokens"]:
            report["usage"] = counts
        credits = usage.get("credits")
        if isinstance(credits, int | float):
            # Some providers count **credits** instead of tokens.  That is
            # their unit, and it is worth keeping: the usage is not lost, it
            # is just not in tokens.
            report["credits"] = float(credits)
    text = ""
    if isinstance(part, dict) and isinstance(part.get("text"), str):
        text = part["text"]
    elif event.get("type") == "text" and isinstance(event.get("text"), str):
        text = event["text"]
    elif isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            text = content
    if text.strip():
        report["text"] = text
    for key in ("result", "text", "content"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            report["text"] = value
        elif isinstance(value, list):
            for block in value:
                if (
                    isinstance(block, dict)
                    and isinstance(block.get("text"), str)
                    and block["text"].strip()
                ):
                    report["text"] = block["text"]


def _usage_counts(usage: dict[str, Any]) -> dict[str, int]:
    """The CLI's usage, in the pool's two counters.

    The CLIs report different shapes (Anthropic's ``input_tokens`` /
    ``output_tokens``, the cache counters, the total).  The pool counts the
    input and the output; the cache is the CLI's own business.
    """
    return {
        "prompt_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
    }


def extract_text(stdout: str) -> str:
    """The last text event of a CLI's JSON output.

    The opencode CLI writes one JSON event per line (``{"type": "text",
    "part": {"text": "..."}}``).  The shape is not pinned by any spec, so the
    reader is forgiving: any event carrying a non-empty ``text`` counts, and
    the raw tail is the fallback.
    """
    return extract_report(stdout)["text"]
