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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.pool.cli_model import CliModelSource

#: The protocols an upstream can speak.
OPENAI_CHAT = "openai_chat"
OPENAI_RESPONSES = "openai_responses"
ANTHROPIC = "anthropic"
#: A model that only a CLI can serve (the quota is CLI-only).
CLI = "cli"

#: Where the pool's own configuration lives (the CLI slots it may serve).
POOL_CONFIG_PATH = Path("data") / "pool" / "config.json"

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
    },
    {
        "name": "codebuddy",
        "model": "hy3",
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
        "name": "codebuddy",
        "model": "minimax-m3.1-flash",
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
        "binary": "qoderclicn",
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
        "name": "qoder",
        "model": "DeepSeek-Flash",
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


def discover_cli_slots(config_path: Path | None = None, home: Path | None = None) -> list[Upstream]:
    """The CLI slots the pool may serve.

    They are declared, not discovered: a CLI slot runs a real CLI, and which
    CLIs are allowed to spend their quota for the platform is a decision, not
    a fact to be sniffed out of a config file.
    """
    path = config_path or POOL_CONFIG_PATH
    declared: list[dict[str, Any]] = []
    document = _read_json(path) if path.exists() else {}
    if isinstance(document, dict):
        slots = document.get("cli_slots")
        if isinstance(slots, list):
            declared = [slot for slot in slots if isinstance(slot, dict)]
    if not declared:
        declared = [dict(slot) for slot in DEFAULT_CLI_SLOTS]
    upstreams: list[Upstream] = []
    home = home if home is not None else Path.home()
    for slot in declared:
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
        name = str(slot.get("name") or "cli-slot")
        source = CliModelSource(
            name=name,
            model=model,
            binary=str(slot.get("binary") or ""),
            args=tuple(str(part) for part in (slot.get("args") or ())),
            prompt_via_stdin=bool(slot.get("prompt_via_stdin")),
            file_arg=(str(slot.get("file_arg")) if slot.get("file_arg") else None),
            message_flag=(str(slot.get("message_flag")) if slot.get("message_flag") else None),
            timeout_seconds=float(slot.get("timeout_seconds") or 900.0),
            store=str(slot.get("store") or ""),
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
                tier=str(slot.get("tier") or "free"),
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
