"""Shared env-template logic (stdlib only).

Templates live in ``environment/templates/<alias>.json`` (v1 schema, see
``default.json``). This module is used by both the interactive
``env-setup`` interview and the ``herdr-agent-firejail --template`` runtime
path so they can never drift apart.

Artifact mapping (rendered, git-trackable, source of truth stays the JSON):
  alias == "default" -> firejail/herdr-agent.profile,
                       firejail/herdr-netfilter.net,
                       proxy/config/allowlist.txt
  alias == "<name>"  -> firejail/herdr-agent-<name>.profile,
                       firejail/herdr-netfilter-<name>.net,
                       proxy/config/allowlist-<name>.txt
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

VERSION = 1

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent.parent
DEFAULT_TEMPLATES_DIR = REPO_ROOT / "environment" / "templates"


def templates_dir() -> Path:
    override = os.environ.get("GULYAS_TEMPLATES_DIR")
    if override:
        return Path(override)
    return DEFAULT_TEMPLATES_DIR


def is_valid_alias(alias: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-_]{0,63}", alias or ""))


def list_templates() -> list[str]:
    d = templates_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json") if p.is_file())


def template_path(alias: str) -> Path:
    return templates_dir() / f"{alias}.json"


def load_template(alias_or_path: str) -> dict:
    """Load by alias ('web-strict') or explicit path ('./my.json')."""
    p = Path(alias_or_path)
    if p.suffix == ".json" and (p.exists() or "/" in alias_or_path):
        return json.loads(p.read_text())
    alias = alias_or_path
    f = template_path(alias)
    if not f.exists():
        raise FileNotFoundError(
            f"env template '{alias}' not found in {templates_dir()} "
            f"(run env-setup to create it)"
        )
    return json.loads(f.read_text())


def validate_template(t: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(t, dict):
        return ["template must be a JSON object"]
    if t.get("version") != VERSION:
        errors.append(f"version must be {VERSION}")
    alias = t.get("alias", "")
    if not is_valid_alias(alias):
        errors.append("alias must match [a-z0-9][a-z0-9-_]{0,63} (lowercase, digits, '-'/'_')")
    fs = t.get("filesystem", {})
    net = t.get("network", {})
    if not isinstance(fs, dict):
        errors.append("filesystem must be an object")
    if not isinstance(net, dict):
        errors.append("network must be an object")
    else:
        port = net.get("proxy_port", 8888)
        if not isinstance(port, int) or not (1 <= port <= 65535):
            errors.append("network.proxy_port must be 1..65535")
        allow = net.get("allowlist", [])
        if not isinstance(allow, list) or not all(isinstance(x, str) and x.strip() for x in allow):
            errors.append("network.allowlist must be a list of non-empty domain strings")
        bmax = net.get("budget_max_default", 200)
        if not isinstance(bmax, int) or bmax <= 0:
            errors.append("network.budget_max_default must be a positive int")
    strict = (fs or {}).get("strictness", "standard")
    if strict not in ("minimal", "standard", "strict", "custom"):
        errors.append("filesystem.strictness must be minimal|standard|strict|custom")
    for key in ("blacklist", "writable_caches", "read_only_dirs", "read_only_toolchain",
                "allowed_tools"):
        val = (fs or {}).get(key, [])
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            errors.append(f"filesystem.{key} must be a list of strings")
    for x in (fs or {}).get("allowed_tools", []):
        if not isinstance(x, str) or not x or "/" in x or any(c.isspace() for c in x):
            errors.append(
                f"filesystem.allowed_tools has invalid tool name: {x!r} (bare basename expected)")
            break
    return errors


def artifact_paths(alias: str) -> dict[str, Path]:
    """Derived firejail/proxy artifacts for a template alias (repo-relative)."""
    firejail = REPO_ROOT / "environment" / "firejail"
    cfg = REPO_ROOT / "environment" / "proxy" / "config"
    if alias == "default":
        return {
            "profile": firejail / "herdr-agent.profile",
            "netfilter": firejail / "herdr-netfilter.net",
            "allowlist": cfg / "allowlist.txt",
        }
    safe = alias  # validated by is_valid_alias before rendering
    return {
        "profile": firejail / f"herdr-agent-{safe}.profile",
        "netfilter": firejail / f"herdr-netfilter-{safe}.net",
        "allowlist": cfg / f"allowlist-{safe}.txt",
    }


def render_profile(t: dict) -> str:
    fs = t.get("filesystem", {})
    alias = t.get("alias", "custom")
    lines = [
        f"# Generated from environment/templates/{alias}.json — do not hand-edit.",
        "# Re-render with: bash environment/scripts/env-setup (choose render) or --render-all",
        "noprofile",
    ]
    if fs.get("noroot", True):
        lines.append("noroot")
    if fs.get("nonewprivs", True):
        lines.append("nonewprivs")
    if fs.get("seccomp", True):
        lines.append("seccomp")
        lines.append("seccomp.block-secondary")
    lines.append("caps.drop all")
    lines.append("machine-id")
    if fs.get("private_dev", True):
        lines.append("private-dev")
    if fs.get("private_tmp", True):
        lines.append("private-tmp")
    etc = fs.get("private_etc", [])
    if etc:
        lines.append("private-etc " + ",".join(etc))
    if fs.get("nodbus", True):
        lines.append("dbus-user none")
        lines.append("dbus-system none")
    lines.append("nogroups")
    lines.append("nosound")
    lines.append("notv")
    if fs.get("nox11", True):
        lines.append("nox11")
    lines.append("nodvd")
    if fs.get("disable_mnt", True):
        lines.append("disable-mnt")
    for d in fs.get("read_only_dirs", []):
        lines.append(f"read-only {d}")
    for p in fs.get("blacklist", []):
        lines.append(f"blacklist {p}")
    for p in fs.get("noblacklist", []):
        lines.append(f"noblacklist {p}")
    tools = fs.get("allowed_tools", [])
    for p in fs.get("read_only_toolchain", []):
        if tools and p in ("/bin", "bin"):
            continue  # private-bin below scopes /bin instead of a blanket whitelist
        lines.append(f"whitelist {p}")
    for p in fs.get("writable_caches", []):
        lines.append(f"whitelist {p}")
    # go mod cache is conventionally read-only; keep explicit if listed writable
    if "${HOME}/go/pkg/mod" in fs.get("writable_caches", []):
        lines.append("read-only ${HOME}/go/pkg/mod")
    if tools:
        # Tool gate: rebuild /bin with only these binaries visible in the jail.
        lines.append("private-bin " + ",".join(tools))
    if fs.get("memory_deny_write_execute", True):
        lines.append("memory-deny-write-execute")
    if fs.get("restrict_namespaces", True):
        lines.append("restrict-namespaces")
    # NOTE: per-worktree --whitelist is added at runtime by herdr-agent-firejail.
    return "\n".join(lines) + "\n"


def render_netfilter(t: dict) -> str:
    net = t.get("network", {})
    alias = t.get("alias", "custom")
    port = int(net.get("proxy_port", 8888))
    allow_dns = bool(net.get("allow_dns", True))
    lines = [
        f"# Generated from environment/templates/{alias}.json — do not hand-edit.",
        "*filter",
        ":INPUT DROP [0:0]",
        ":FORWARD DROP [0:0]",
        ":OUTPUT DROP [0:0]",
        "-A INPUT -i lo -j ACCEPT",
        "-A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT",
        "-A OUTPUT -o lo -j ACCEPT",
        f"-A OUTPUT -d 127.0.0.1 -p tcp --dport {port} -j ACCEPT",
    ]
    if allow_dns:
        lines.append("-A OUTPUT -p udp --dport 53 -j ACCEPT")
        lines.append("-A OUTPUT -p tcp --dport 53 -j ACCEPT")
    lines.append("COMMIT")
    return "\n".join(lines) + "\n"


def render_allowlist(t: dict) -> str:
    net = t.get("network", {})
    alias = t.get("alias", "custom")
    domains = net.get("allowlist", [])
    lines = [
        f"# Generated from environment/templates/{alias}.json — do not hand-edit.",
        "# one domain per line, '#' = comment. '*.example.com' matches subdomains only.",
    ]
    lines.extend(domains)
    return "\n".join(lines) + "\n"


def render_all(t: dict) -> dict[str, str]:
    return {
        "profile": render_profile(t),
        "netfilter": render_netfilter(t),
        "allowlist": render_allowlist(t),
    }


def write_artifacts(t: dict) -> dict[str, Path]:
    errors = validate_template(t)
    if errors:
        raise ValueError("invalid template: " + "; ".join(errors))
    paths = artifact_paths(t["alias"])
    rendered = render_all(t)
    paths["profile"].write_text(rendered["profile"])
    paths["netfilter"].write_text(rendered["netfilter"])
    paths["allowlist"].write_text(rendered["allowlist"])
    return paths
