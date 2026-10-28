"""Where the relay's model quota comes from.

The platform does not own model credentials -- the harness CLIs do.  So the
relay does not carry a key list of its own: it *discovers* the upstreams in
the same configuration files the CLIs read, and it takes each key from the
place it already lives, at call time.

Nothing here is written to disk and no key is ever copied into the relay's
own state: the relay is a switchboard, not a key vault.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from app.config import settings
from app.pool.cli_model import CliModelSource

#: The protocols an upstream can speak.
OPENAI_CHAT = "openai_chat"
OPENAI_RESPONSES = "openai_responses"
ANTHROPIC = "anthropic"
#: A model that only a CLI can serve (the quota is CLI-only).
CLI = "cli"

#: Where the pool's own configuration lives (the CLI slots it may serve).
#: Kept for the historical CWD-relative deployments; new deployments put it
#: under ``AP_DATA_DIR`` (see :func:`pool_config_path`).
POOL_CONFIG_PATH = Path("data") / "pool" / "config.json"

#: What a declared slot may say its quota is.
#:
#: There is deliberately **no default**: a missing tier used to mean "free",
#: which would let a paid quota be spent as if it were free.  The built-in
#: slots below say it explicitly; a declaration that does not is not offered.
CLI_TIERS = ("free", "reserve", "metered")

#: The CLI stores the pool knows how to read usage from.  A slot that names
#: any other store cannot be accounted honestly (its usage would be unknown
#: for ever), so it is not offered -- better a visible rejection than a
#: silent "unknown" on every call.
KNOWN_CLI_STORES = ("opencode",)


def declared_cli_slots() -> list[dict[str, Any]]:
    """The slots an operator declared in the pool config -- **no CLI is spawned**.

    The live list is :func:`discover_cli_slots` (it runs the CLIs to find out what
    they can do); this one is the *declared* side only, cheap enough for a read
    face that answers "what is still missing for this CLI?".
    """
    path = pool_config_path()
    if not path.exists():
        return []
    try:
        document = _read_json(path)
    except (OSError, ValueError):
        return []
    slots = document.get("cli_slots") if isinstance(document, dict) else None
    if not isinstance(slots, list):
        return []
    return [slot for slot in slots if isinstance(slot, dict)]


def pool_config_path() -> Path:
    """Where the pool's own declarations live.

    The data directory first: that is where the pool's usage log is kept, and
    it is the directory the platform was actually pointed at.  The historical
    CWD-relative path stays as a fallback so an existing checkout keeps
    working.
    """
    scoped = Path(settings.data_dir) / "pool" / "config.json"
    if scoped.exists():
        return scoped
    return POOL_CONFIG_PATH


#: The default CLI slots: the quotas that **only** the CLI itself may spend.
#:
#: * OpenCode's free tier says so in as many words ("OpenCode's free tier can
#:   only be used from within OpenCode"), so it is served by the opencode CLI;
#: * codebuddy's account carries Hunyuan ``hy3`` and ``minimax-m3.1-flash``;
#: * qoder carries ``Qwen3.8-Flash`` and ``DeepSeek-Flash``.
#:
#: Each slot declares the CLI's own argv contract, because the CLIs differ:
#: codebuddy takes the question on stdin, qoder as a positional argument,
#: opencode can attach a file.
DEFAULT_CLI_SLOTS = (
    {
        "name": "opencode-cli",
        "model": "opencode/minimax-m3.1-flash-free",
        "binary": "opencode",
        "args": ["run", "--standalone", "--format", "json", "--auto"],
        "file_arg": "--file",
        # The opencode CLI does not put its usage on stdout; it keeps it in its
        # own store, keyed by the message id that *is* on stdout.  Declared
        # here, because "this CLI keeps a store we know how to read" is a fact
        # about the slot, not something to sniff at runtime.
        "store": "opencode",
        "tier": "free",
        # The CLI itself can list its catalogue (an **undeclared** listing
        # costs no quota; only a model call does).  The naming rule is the
        # declaration, not a sniff: "-free" is opencode's own suffix for its
        # free family -- the slot the platform first declared carries it.
        # Every roster entry matching the rule joins the slot's tier (free)
        # with the same argv contract; the roster is re-listed on a TTL, so
        # the pool adjusts when opencode adds or retires a free model.
        "enumerate": {"args": ["models"], "pattern": r"^opencode/.+-free$"},
        # 名册：这个 CLI **自己说**它能跑哪些模型（一次列举，不花额度）。
        # 名册不是档位 —— 它只说「有这些」，不说「这些免费不免费」。所以平台
        # 仍然只提供**声明过**的那几个，多出来的那些要告诉人（见 pool 的 rosters）。
        "roster": {"args": ["models"], "pattern": r"^(opencode/\S+)$", "group": 1},
    },
    {
        "name": "codebuddy",
        "model": "hy3",
        "tier": "free",
        "binary": "codebuddy",
        "args": [
            "-p",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "bypassPermissions",
            "--add-dir",
            "{workdir}",
        ],
        "prompt_via_stdin": True,
        # 名册：codebuddy 把可用模型写在 `--model` 的说明里，一次列举不花额度。
        "roster": {
            "args": ["--help"],
            "pattern": r"Currently supported: \(([^)]*)\)",
            "group": 1,
        },
    },
    {
        "name": "codebuddy",
        "model": "minimax-m3.1-flash",
        "tier": "free",
        "binary": "codebuddy",
        "args": [
            "-p",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "bypassPermissions",
            "--add-dir",
            "{workdir}",
        ],
        "prompt_via_stdin": True,
    },
    {
        "name": "qoder",
        "model": "Qwen3.8-Flash",
        "tier": "free",
        "binary": "qoderclicn",
        # 名册：qoder 的 CLI 自己会列（`--list-models`，不花额度）。
        "roster": {"args": ["--list-models"], "pattern": r"^(?!MODEL$)(\S+)$", "group": 1},
        "args": [
            "-p",
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            "-w",
            "{workdir}",
        ],
        # The qoder provider reports a **ratio** (context used / window) and its
        # own unit (credits), not tokens.  The window is in its model
        # catalogue -- which the platform may read, read-only, with a
        # credential the user provides.  Without that credential there is no
        # window, and the usage stays in credits.
        "catalogue": {
            "base_url": "https://gateway.qoder.com.cn",
            "path": "/api/v1/models",
            "credential_file": "~/.qoder-cn/api-token",
        },
    },
    {
        "name": "mmx",
        "model": "MiniMax-M3.1-Flash-Preview",
        "binary": "mmx",
        # ``--quiet`` would leave only the answer on stdout and throw the JSON
        # (and with it the **usage**) away; the pool reads the JSON.
        "args": ["text", "chat", "--output", "json", "--non-interactive"],
        "message_flag": "--message",
        # The CLI is signed in with the same account as the direct upstream, so
        # it spends the **same** M Plan allowance: reserve, not free.
        "tier": "reserve",
        # The slot only exists once the user has signed the CLI in: offering a
        # quota that cannot be spent would be a lie.
        "requires": {"file": ".mmx/config.json", "key": "api_key"},
    },
    {
        # 仓库主人：codebuddy 的 step-5 也是免费的（`--model` 说明里它排在
        # hy3-x 之后）。免费与否是**人的事实**，不是平台能猜出来的 ——
        # 所以它进声明（声明就是人的决定）。
        "name": "codebuddy",
        "model": "step-5-preview",
        "tier": "free",
        "binary": "codebuddy",
        "args": [
            "-p",
            "--output-format",
            "stream-json",
            "--permission-mode",
            "bypassPermissions",
            "--add-dir",
            "{workdir}",
        ],
        "prompt_via_stdin": True,
    },
    {
        "name": "qoder",
        "model": "DeepSeek-Flash",
        "tier": "free",
        "binary": "qoderclicn",
        "args": [
            "-p",
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            "-w",
            "{workdir}",
        ],
    },
)


@dataclass(frozen=True, slots=True)
class Upstream:
    """One place the relay can get a model from."""

    name: str
    protocol: str
    base_url: str
    models: tuple[str, ...] = ()
    #: Where the credential comes from: ``("config", path, key)``,
    #: ``("file", path)`` or ``("literal", value)``.  ``None`` means "no key".
    credential: tuple[Any, ...] | None = None
    #: Which configuration file this was discovered in (for the office).
    origin: str = ""
    #: Why this upstream is probably not usable right now.
    note: str = ""
    #: The CLI slot, when the protocol is ``CLI``.
    cli: CliModelSource | None = None
    #: Which kind of quota this is:
    #: ``free``        the CLI's own free tier (no plan, no money);
    #: ``reserve``     a plan's allowance kept **in reserve**: it is there when
    #:                   nothing else is left, but the platform does not spend
    #:                   it on its own -- asking for it has to be explicit;
    #: ``metered``     pay-as-you-go / pooled relay accounts (every call costs).
    tier: str = "metered"
    extra: dict[str, Any] = field(default_factory=dict)

    def credential_value(self) -> str | None:
        kind = self.credential[0] if self.credential else None
        if kind == "literal":
            return str(self.credential[1])
        if kind == "file":
            try:
                return Path(str(self.credential[1])).read_text(encoding="utf-8").strip()
            except OSError:
                return None
        if kind == "config":
            _, path, key = self.credential
            try:
                document = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            value = document
            for part in str(key).split("."):
                if not isinstance(value, dict):
                    return None
                value = value.get(part)
            return str(value) if value else None
        return None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _codex_upstreams(home: Path, relay_ports: tuple[int, ...]) -> list[Upstream]:
    """The codex chain: the local relay the codex CLI already talks to.

    ``~/.codex/config.toml`` lists the providers; the base URL points at the
    local proxy that injects the real upstream key.  The relay only needs the
    base URL -- the chain holds the credentials.
    """
    path = home / ".codex" / "config.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    found: list[Upstream] = []
    for match in re.finditer(
        r"^\[model_providers\.(?P<name>[A-Za-z0-9_.\-]+)\]\s*$(?P<body>(?:\n(?!\[)[^\n]*)*)",
        text,
        re.MULTILINE,
    ):
        body = match.group("body")
        base = re.search(r'^\s*base_url\s*=\s*"([^"]+)"', body, re.MULTILINE)
        if not base:
            continue
        base_url = base.group(1).rstrip("/")
        wire = re.search(r'^\s*wire_api\s*=\s*"([^"]+)"', body, re.MULTILINE)
        protocol = OPENAI_RESPONSES if (wire and wire.group(1) == "responses") else OPENAI_CHAT
        port = _port_of(base_url)
        if port and port in relay_ports:
            # That would be us: a loop back into the relay must never happen.
            continue
        found.append(
            Upstream(
                name=f"codex:{match.group('name')}",
                protocol=protocol,
                base_url=base_url,
                credential=("literal", "local-relay"),
                origin=str(path),
                note="codex CLI 本来就用这条链路；key 在链路里，不在池子里",
            )
        )
    return found


def _claude_upstream(home: Path, relay_ports: tuple[int, ...]) -> list[Upstream]:
    """The Claude CLI's provider (Anthropic protocol) with its key."""
    path = home / ".claude" / "settings.json"
    document = _read_json(path)
    if not isinstance(document, dict):
        return []
    env = document.get("env")
    env = env if isinstance(env, dict) else {}
    base_url = str(env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")
    if not base_url or not token:
        return []
    port = _port_of(base_url)
    if port and port in relay_ports:
        return []
    models: list[str] = []
    alias = document.get("model")
    if isinstance(alias, str) and alias.strip():
        resolved = env.get(f"ANTHROPIC_DEFAULT_{alias.strip().upper()}_MODEL") or env.get(
            "ANTHROPIC_MODEL"
        )
        if isinstance(resolved, str) and resolved.strip():
            models.append(resolved.strip())
    if not models:
        fallback = env.get("ANTHROPIC_MODEL")
        if isinstance(fallback, str) and fallback.strip():
            models.append(fallback.strip())
    return [
        Upstream(
            name="claude-provider",
            protocol=ANTHROPIC,
            base_url=base_url,
            models=tuple(models),
            credential=("config", str(path), "env.ANTHROPIC_AUTH_TOKEN"),
            origin=str(path),
            note="Claude CLI 自己配的那个 provider",
        )
    ]


def _opencode_upstreams(home: Path, relay_ports: tuple[int, ...]) -> list[Upstream]:
    """The providers the opencode CLI is configured with."""
    path = home / ".config" / "opencode" / "opencode.json"
    document = _read_json(path)
    if not isinstance(document, dict):
        return []
    providers = document.get("provider")
    if not isinstance(providers, dict):
        return []
    found: list[Upstream] = []
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        options = provider.get("options")
        options = options if isinstance(options, dict) else {}
        base_url = str(options.get("baseURL") or "").rstrip("/")
        if not base_url:
            continue
        port = _port_of(base_url)
        if port and port in relay_ports:
            continue
        models = tuple((provider.get("models") or {}).keys())
        key = options.get("apiKey")
        credential: tuple[Any, ...] | None
        if isinstance(key, str) and key.strip():
            credential = ("config", str(path), f"provider.{name}.options.apiKey")
        else:
            credential = None
        found.append(
            Upstream(
                name=f"opencode:{name}",
                protocol=OPENAI_CHAT,
                base_url=base_url,
                models=models,
                credential=credential,
                origin=str(path),
                note="opencode CLI 自己配的那个 provider",
            )
        )
    return found


def _minimax_credentials(home: Path) -> tuple[dict[str, Any], Path] | None:
    """The MiniMax CLI's credentials, if the user has signed in.

    Two shapes exist: an API key (``api_key``, the pay-as-you-go kind) and an
    OAuth login (``oauth.access_token`` + a refresh token).  The OAuth token
    **expires** -- the CLI refreshes it and rewrites this file, so the pool
    re-reads it on every call instead of caching it.
    """
    path = home / ".mmx" / "config.json"
    document = _read_json(path)
    if not isinstance(document, dict):
        return None
    if _minimax_credential_path(document) is None:
        return None
    return document, path


def _minimax_credential_path(document: dict[str, Any]) -> str | None:
    """Where the credential lives inside the mmx config."""
    if isinstance(document.get("api_key"), str) and document["api_key"].strip():
        return "api_key"
    oauth = document.get("oauth")
    if isinstance(oauth, dict):
        token = oauth.get("access_token")
        if isinstance(token, str) and token.strip():
            return "oauth.access_token"
    return None


def _minimax_base(document: dict[str, Any]) -> str:
    """The API base, resolved the way the mmx SDK resolves it.

    ``base_url`` (an explicit override) wins, then the account's own
    ``resource_url``, then the region's default.  Getting this wrong sends
    the token to the wrong region, so the order matters.
    """
    for candidate in (document.get("base_url"),):
        if isinstance(candidate, str) and candidate.startswith("http"):
            return candidate.rstrip("/")
    oauth = document.get("oauth")
    if isinstance(oauth, dict):
        candidate = oauth.get("resource_url")
        if isinstance(candidate, str) and candidate.startswith("http"):
            return candidate.rstrip("/")
    region = str(document.get("region") or "").strip().lower()
    if not region and isinstance(oauth, dict):
        region = str(oauth.get("region") or "").strip().lower()
    return "https://api.minimax.cn" if region == "cn" else "https://api.minimax.io"


def _minimax_upstream(home: Path) -> list[Upstream]:
    """MiniMax's own provider, read from the MiniMax CLI's credentials.

    The MiniMax CLI talks to an **Anthropic-compatible** endpoint
    (``<base>/anthropic/v1/messages``), so the pool can serve that quota as a
    plain upstream -- and the CLI slot can serve it the other way round.

    Nothing is read when the user has not signed in: an absent config file
    simply means "no MiniMax quota here".
    """
    found = _minimax_credentials(home)
    if found is None:
        return []
    document, path = found
    credential = _minimax_credential_path(document)
    return [
        Upstream(
            name="minimax",
            protocol=ANTHROPIC,
            base_url=f"{_minimax_base(document)}/anthropic",
            models=("MiniMax-M3.1-Flash-Preview",),
            credential=("config", str(path), credential or "api_key"),
            origin=str(path),
            note="mmx CLI 登录用的 MiniMax 订阅（留着当兜底）",
            tier="reserve",
        )
    ]


def _port_of(base_url: str) -> int | None:
    match = re.search(r":(\d+)(?:/|$)", base_url)
    return int(match.group(1)) if match else None


def pool_config_write_path() -> Path:
    """Where a **new** declaration is written: the data directory.

    The read path (:func:`pool_config_path`) falls back to a historical
    CWD-relative location so an old checkout keeps working.  A write must not
    land there: the platform runs with the backend directory as its CWD, so the
    declaration would end up inside the checkout instead of the data directory
    the platform was pointed at.
    """
    return Path(settings.data_dir) / "pool" / "config.json"


def _slot_key(slot: dict[str, Any]) -> tuple[str, str]:
    """A slot's identity: the CLI name and the model it spends."""
    return (str(slot.get("name") or "cli-slot"), str(slot.get("model") or "").strip())


#: How long a CLI's own roster listing stays fresh.  A listing costs no
#: quota, but it is a process spawn; ten minutes is far under the drift the
#: free family has shown and far above the discovery TTL that would ask.
ENUM_TTL_SECONDS = 600.0
#: How long an **empty** listing is believed.
#:
#: A listing that came back empty is either "this CLI really has nothing" or
#: "the spawn failed this time" -- and the two look the same from here.  Caching
#: it for the full ten minutes made one bad spawn hide a whole catalogue: the
#: office showed 19 models instead of 61, with 42 of them silently gone because
#: `opencode --list-models` had failed once.  The model list is the thing a
#: person picks from, so a hiccup costs half a minute, not ten.
EMPTY_ROSTER_TTL_SECONDS = 30.0
_ENUM_ROSTER: dict[tuple[str, str], tuple[float, list[str]]] = {}

#: The runner that reads one CLI's roster (injectable for tests).
ListingRunner = Callable[[str, list[str]], list[str]]


def _roster_is_stale(cached: tuple[float, list[str]], now: float) -> bool:
    """An empty listing expires fast; a real one keeps the long TTL."""
    at, roster = cached
    return now - at > (ENUM_TTL_SECONDS if roster else EMPTY_ROSTER_TTL_SECONDS)


def _run_listing(binary: str, args: list[str]) -> list[str]:
    """Run the CLI's listing subcommand and return its non-empty lines."""
    completed = subprocess.run(
        [binary, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60.0,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{binary} {' '.join(args)} failed ({completed.returncode}):"
            f" {(completed.stderr or completed.stdout or '')[-200:]}"
        )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _listing_attempt(binary: str, args: list[str], runner: ListingRunner | None) -> list[str]:
    """Read one listing, retrying once before believing it is empty.

    A single failed spawn used to look exactly like "this CLI has no models":
    `opencode models` failing once hid 41 models from the picker (the office
    showed the four declared ones and nothing else), because the empty answer
    was cached.  This machine runs opencode windows of its own, so one failed
    spawn is a normal event, not a broken CLI -- ask once more before writing
    down "nothing".
    """
    ask = runner or _run_listing
    for attempt in range(2):
        try:
            names = ask(binary, args)
        except Exception:  # noqa: BLE001 - a silent CLI is silence, not a guess
            names = []
        if names:
            return names
        if attempt == 0:
            time.sleep(0.3)
    return []


def _enumerate_roster(
    slot: dict[str, Any], source: CliModelSource, runner: ListingRunner | None
) -> list[str]:
    """The models the CLI itself lists that match the slot's declared rule.

    A listing subprocess is shared via a TTL cache keyed by the resolved
    binary -- several slots may expand from one CLI without re-spawning it.
    A listing that cannot run leaves the expansion empty: the declared slot
    still serves, and nothing is invented to fill its place.
    """
    spec = slot.get("enumerate")
    if not isinstance(spec, dict):
        return []
    args = [str(arg) for arg in (spec.get("args") or ())]
    if not args:
        return []
    binary = source.resolve_binary()
    key = (binary, " ".join(args))
    now = time.monotonic()
    cached = _ENUM_ROSTER.get(key)
    if cached is None or _roster_is_stale(cached, now):
        _ENUM_ROSTER[key] = (now, _listing_attempt(binary, args, runner))
    pattern_text = str(spec.get("pattern") or "")
    try:
        pattern = re.compile(pattern_text)
    except re.error:
        return []
    return [line for line in _ENUM_ROSTER.get(key, (now, []))[1] if pattern.fullmatch(line)]


def _listing_lines(
    slot: dict[str, Any], source: CliModelSource, runner: ListingRunner | None
) -> list[str]:
    """The CLI's own listing (cached, TTL).  A listing costs no quota."""
    spec = slot.get("roster")
    if not isinstance(spec, dict):
        return []
    args = [str(arg) for arg in (spec.get("args") or ())]
    if not args:
        return []
    binary = source.resolve_binary()
    key = (binary, " ".join(args))
    now = time.monotonic()
    cached = _ENUM_ROSTER.get(key)
    if cached is None or _roster_is_stale(cached, now):
        _ENUM_ROSTER[key] = (now, _listing_attempt(binary, args, runner))
    return list(_ENUM_ROSTER.get(key, (now, []))[1])


def cli_roster(
    slot: dict[str, Any], source: CliModelSource, runner: ListingRunner | None
) -> list[str]:
    """What the CLI itself says it can run.

    The roster is **not a tier**: it says what exists, not what is free.  The
    pool still serves only the models the operator declared; the ones the CLI
    knows but nobody declared are reported, so the human can see "the CLI can
    do this, and nobody said whether it costs anything".
    """
    spec = slot.get("roster")
    if not isinstance(spec, dict):
        return []
    lines = _listing_lines(slot, source, runner)
    pattern_text = str(spec.get("pattern") or "")
    try:
        pattern = re.compile(pattern_text) if pattern_text else None
    except re.error:
        return []
    group = int(spec.get("group") or 0)
    names: list[str] = []
    for line in lines:
        if pattern is None:
            names.append(line.strip())
            continue
        match = pattern.search(line)
        if not match:
            continue
        value = match.group(group) if group <= (match.lastindex or 0) else match.group(0)
        for part in str(value).split(","):
            name = part.strip()
            if name:
                names.append(name)
    return names


def discover_cli_rosters(
    config_path: Path | None = None,
    home: Path | None = None,
    *,
    listing_runner: ListingRunner | None = None,
) -> dict[str, list[str]]:
    """What every CLI says it can run, keyed by the CLI's name.

    Read-only and quota-free: a listing is a question, not a model call.
    """
    path = config_path or pool_config_path()
    declared: list[dict[str, Any]] = []
    document = _read_json(path) if path.exists() else {}
    if isinstance(document, dict):
        slots = document.get("cli_slots")
        if isinstance(slots, list):
            declared = [slot for slot in slots if isinstance(slot, dict)]
    rosters: dict[str, list[str]] = {}
    for slot in _merge_slots(declared):
        spec = slot.get("roster")
        if not isinstance(spec, dict):
            continue
        name = str(slot.get("name") or "cli-slot")
        source = CliModelSource(
            name=name,
            model=str(slot.get("model") or ""),
            binary=str(slot.get("binary") or ""),
            args=tuple(str(part) for part in (slot.get("args") or ())),
        )
        names = cli_roster(slot, source, listing_runner)
        if not names:
            continue
        merged = list(rosters.get(name) or [])
        for entry in names:
            if entry not in merged:
                merged.append(entry)
        rosters[name] = merged
    return rosters


def invalidate_rosters() -> None:
    """Forget the cached CLI listings.

    The probe asks for them again: a listing costs no quota, only a process
    spawn, and a stale roster is exactly what "did we miss a new free model?"
    is about.
    """
    _ENUM_ROSTER.clear()


class PoolDeclarationError(ValueError):
    """A model cannot be given a tier (unknown CLI, unknown model, bad tier)."""


#: The fields a new slot **inherits** from the CLI's other slots: how to run
#: that CLI.  A model is a different thing, not a different program.
_SLOT_CONTRACT_FIELDS = (
    "binary",
    "args",
    "prompt_via_stdin",
    "file_arg",
    "message_flag",
    "timeout_seconds",
    "store",
    "catalogue",
    "requires",
)


def declare_cli_model(
    cli: str,
    model: str,
    tier: str,
    *,
    config_path: Path | None = None,
    roster: list[str] | None = None,
) -> dict[str, Any]:
    """Give a model the CLI says it can run a tier (free / metered / reserve).

    Three rules:

    * **Only what the CLI itself lists** may be declared -- the platform does
      not invent models.  (``roster`` is that list; ``None`` means "no
      listing available", and the check is skipped rather than guessed.)
    * **The tier is the human's call**: free or paid is a fact about the
      account, not something the platform can sniff out.
    * **The argv contract is inherited**: the new slot runs the CLI the same
      way the CLI's other slots do -- a different contract would not run.
    """
    name = str(cli or "").strip()
    model_name = str(model or "").strip()
    tier_name = str(tier or "").strip().lower()
    if not name or not model_name:
        raise PoolDeclarationError("which CLI and which model must be named")
    if tier_name not in CLI_TIERS:
        raise PoolDeclarationError(
            "the tier must be free, reserve or metered -- the platform will not "
            "guess whether spending this quota costs money"
        )
    path = config_path or pool_config_write_path()
    declared: list[dict[str, Any]] = []
    document = _read_json(path) if path.exists() else {}
    if isinstance(document, dict):
        slots = document.get("cli_slots")
        if isinstance(slots, list):
            declared = [slot for slot in slots if isinstance(slot, dict)]
    siblings = [slot for slot in _merge_slots(declared) if str(slot.get("name") or "") == name]
    if not siblings:
        raise PoolDeclarationError(f"the pool has no slot for {name}")
    if roster is not None:
        # 名册里的名字是 CLI 自己的写法（`opencode/beam-free`），模型 id 用的是它的
        # 短名（`beam-free`）。两边必须按同一种口径比：以前拿**全名**去比名册的
        # **短名**，于是 opencode 那 42 个模型一次都没定档成功过（codebuddy 的名册
        # 不带斜杠，所以它一直正常）。这里先把人给的名字归一成 CLI 自己列的那一个。
        bare = model_name.split("/")[-1]
        matched = next((entry for entry in roster if str(entry).split("/")[-1] == bare), None)
        if matched is None:
            raise PoolDeclarationError(
                f"{name} does not list {model_name} itself -- the pool does not "
                "offer models the CLI cannot run"
            )
        # 存 CLI 那份写法：argv 直接拿它去跑，短名会让 CLI 找不到模型。
        model_name = str(matched)
    contract = {key: siblings[0][key] for key in _SLOT_CONTRACT_FIELDS if key in siblings[0]}
    slot: dict[str, Any] = {"name": name, "model": model_name, "tier": tier_name}
    slot.update(contract)
    kept = [
        entry
        for entry in declared
        if not (
            str(entry.get("name") or "") == name
            and str(entry.get("model") or "").strip() == model_name
        )
    ]
    kept.append(slot)
    payload = dict(document) if isinstance(document, dict) else {}
    payload["cli_slots"] = kept
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return slot


def _merge_slots(declared: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The built-in slots plus the declared ones.

    A declaration with the same (name, model) **replaces** the built-in; one
    with ``"enabled": false`` removes it (by name alone when no model is
    given, which is how a whole CLI is switched off).  Declarations no longer
    replace the whole list: adding one harness used to silently drop every
    other free slot, which is the opposite of what adding a harness means.
    """
    merged = {_slot_key(slot): dict(slot) for slot in DEFAULT_CLI_SLOTS}
    order = list(merged)
    for slot in declared:
        key = _slot_key(slot)
        if slot.get("enabled") is False:
            if key[1]:
                merged.pop(key, None)
            else:
                for existing in [item for item in merged if item[0] == key[0]]:
                    merged.pop(existing, None)
            continue
        if key not in merged:
            order.append(key)
        merged[key] = dict(slot)
    return [merged[key] for key in order if key in merged]


def discover_cli_slots(
    config_path: Path | None = None,
    home: Path | None = None,
    *,
    listing_runner: ListingRunner | None = None,
) -> list[Upstream]:
    """The CLI slots the pool may serve.

    They are declared, not discovered: a CLI slot runs a real CLI, and which
    CLIs are allowed to spend their quota for the platform is a decision, not
    a fact to be sniffed out of a config file.  Declarations extend the
    built-in list (see :func:`_merge_slots`).

    A declaration that does not say **which tier** it is, or that names a
    store the pool cannot read, is not offered: an unreported quota must be
    visible, not a free-looking one that spends money.
    """
    path = config_path or pool_config_path()
    declared: list[dict[str, Any]] = []
    document = _read_json(path) if path.exists() else {}
    if isinstance(document, dict):
        slots = document.get("cli_slots")
        if isinstance(slots, list):
            declared = [slot for slot in slots if isinstance(slot, dict)]
    upstreams: list[Upstream] = []
    home = home if home is not None else Path.home()
    for slot in _merge_slots(declared):
        name = str(slot.get("name") or "cli-slot")
        model = str(slot.get("model") or "").strip()
        if not model:
            continue
        requires = slot.get("requires")
        if isinstance(requires, dict):
            relative = str(requires.get("file") or "")
            key = str(requires.get("key") or "")
            document = _read_json(home / relative) if relative else {}
            if not isinstance(document, dict):
                # Not signed in: no such quota (yet).
                continue
            if key == "api_key":
                # The mmx slot accepts an API key *or* an OAuth login.
                if not _minimax_credential_path(document):
                    continue
            elif not document.get(key):
                continue
        tier = str(slot.get("tier") or "").strip().lower()
        store = str(slot.get("store") or "").strip()
        problem = ""
        if tier not in CLI_TIERS:
            problem = (
                "declared without a tier: say free, reserve or metered -- the pool "
                "will not guess whether spending this quota costs money"
            )
        elif store and store not in KNOWN_CLI_STORES:
            problem = (
                f"declares the store {store!r}, which the pool cannot read; "
                "leave store empty or name one it knows"
            )
        if problem:
            # Keep it in the list, with no models: the office shows *why* this
            # quota is not on offer, instead of hiding a broken declaration.
            upstreams.append(
                Upstream(
                    name=f"{name}:{model}",
                    protocol=CLI,
                    base_url=f"cli://{model}",
                    models=(),
                    credential=None,
                    origin=str(path),
                    note=problem,
                    cli=None,
                    tier="",
                )
            )
            continue
        source = CliModelSource(
            name=name,
            model=model,
            binary=str(slot.get("binary") or ""),
            args=tuple(str(part) for part in (slot.get("args") or ())),
            prompt_via_stdin=bool(slot.get("prompt_via_stdin")),
            file_arg=(str(slot.get("file_arg")) if slot.get("file_arg") else None),
            message_flag=(str(slot.get("message_flag")) if slot.get("message_flag") else None),
            timeout_seconds=float(slot.get("timeout_seconds") or 900.0),
            store=store,
            catalogue=dict(slot.get("catalogue") or {}),
        )
        # The pool's model id is namespaced by the slot: two CLIs may both
        # offer "deepseek-v4.1-flash", and the pool must not merge them.
        pool_id = f"{name}/{model.split('/', 1)[-1]}"
        upstreams.append(
            Upstream(
                name=f"{name}:{model}",
                protocol=CLI,
                base_url=f"cli://{model}",
                models=(pool_id,),
                credential=None,
                origin=str(path),
                note="只有这个 CLI 自己能用这份额度",
                cli=source,
                tier=tier,
            )
        )
        # The slot's own enumerate rule may widen the family: the CLI's free
        # roster is read from the CLI itself (a listing costs no quota), the
        # naming rule is the operator's declaration.  Each hit joins with the
        # same argv contract; the declared model itself never duplicates.
        for raw_model in _enumerate_roster(slot, source, listing_runner):
            if raw_model == model:
                continue
            if any(existing.name == f"{name}:{raw_model}" for existing in upstreams):
                continue
            extra_id = f"{name}/{raw_model.split('/', 1)[-1]}"
            extra = replace(source, model=raw_model)
            upstreams.append(
                Upstream(
                    name=f"{name}:{raw_model}",
                    protocol=CLI,
                    base_url=f"cli://{raw_model}",
                    models=(extra_id,),
                    credential=None,
                    origin=str(path),
                    note="roster entry matched the slot's declared enumerate rule",
                    cli=extra,
                    tier=tier,
                )
            )
    return upstreams


def discover_upstreams(
    home: Path | None = None,
    *,
    relay_ports: tuple[int, ...] = (),
    include_cli: bool = True,
    config_path: Path | None = None,
) -> list[Upstream]:
    """Every upstream the platform's harnesses already have access to.

    ``relay_ports`` are the ports the pool itself listens on: an upstream
    pointing there would be a loop, and loops are skipped.
    """
    home = home if home is not None else Path.home()
    found: list[Upstream] = []
    found.extend(_codex_upstreams(home, relay_ports))
    found.extend(_claude_upstream(home, relay_ports))
    found.extend(_opencode_upstreams(home, relay_ports))
    found.extend(_minimax_upstream(home))
    if include_cli:
        found.extend(discover_cli_slots(config_path, home=home))
    # The same service can show up under two names (the codex chain and the
    # opencode "openai" provider both point at 127.0.0.1:8402).  Two
    # upstreams on one port would double-count one quota -- and the chat
    # spelling of it is a trap: that chain serves the **responses** API, and
    # its chat path answers nothing.  So one port keeps one upstream: the
    # responses one, which the pool can talk to (the chat shape is then
    # translated on the way in).
    seen: set[tuple[str, str]] = set()
    by_port: dict[tuple[str, str], Upstream] = {}
    unique: list[Upstream] = []
    for upstream in found:
        key = (upstream.protocol, upstream.base_url)
        if key in seen:
            continue
        seen.add(key)
        port_key = _port_key(upstream.base_url)
        if port_key is not None and port_key in by_port:
            existing = by_port[port_key]
            if existing.protocol == OPENAI_RESPONSES:
                continue
            if upstream.protocol == OPENAI_RESPONSES:
                unique.remove(existing)
                by_port[port_key] = upstream
                unique.append(upstream)
            continue
        if port_key is not None:
            by_port[port_key] = upstream
        unique.append(upstream)
    # The user's own keys go in **after** the fold above, on purpose: that loop
    # folds two upstreams pointing at the same host:port into one (one quota,
    # one identity).  A key the user typed in is a **different credential** even
    # when the address is the same, so it must not be folded away.
    unique.extend(user_key_upstreams())
    return unique


def user_key_upstreams() -> list[Upstream]:
    """The keys the **user** gave the platform, as upstreams of the ``user`` tier.

    They are read at call time (like every other upstream's credential) and
    they carry the ``user`` tier, which the router only spends after the user
    has said yes.
    """
    from app.pool import user_keys  # local import: keeps the module cycle out

    found: list[Upstream] = []
    for key in user_keys.entries():
        found.append(
            Upstream(
                name=f"user:{key.id}",
                protocol=key.protocol,
                base_url=key.base_url,
                models=key.models,
                credential=("file", str(user_keys.key_dir() / f"{key.id}.key")),
                origin="你自己的 key",
                note="你自己的 key —— 平台不会自己花，要你点了同意",
                tier=user_keys.USER_TIER,
                extra={
                    "key_id": key.id,
                    "label": key.label,
                    "approval": key.approval,
                },
            )
        )
    return found


def _port_key(base_url: str) -> tuple[str, str] | None:
    """The host:port of a base URL -- the identity of the service behind it."""
    match = re.match(r"^https?://([^/:]+)(?::(\d+))?", base_url.strip())
    if not match:
        return None
    host = match.group(1)
    port = match.group(2) or ("443" if base_url.startswith("https") else "80")
    return (host, port)
