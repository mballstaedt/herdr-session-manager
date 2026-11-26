"""Pre-restart safety copy of Herdr's saved layout.

Copies the Herdr session's ``session.json`` (workspaces, tabs, panes, cwd, layout and
agent session references) into ``<session_dir>/pre-restart-backups/`` before the
Operator restarts the computer or stops Herdr, and reports what will and will not
come back.

Read-only toward Herdr: it never stops, restarts, prompts or sends input to the server
or any pane. The only writes are the new backup file (0600, in a 0700 directory) and
pruning older backups that this command itself created. ``session-backups/`` and
``session-snapshots/`` belong to Herdr and are never touched.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

BACKUP_DIR = "pre-restart-backups"
DEFAULT_KEEP = 10
MIN_CLAUDE_INTEGRATION = 6  # Herdr docs: Claude Code native restore needs integration v6+
_NAME_RE = re.compile(r"^session-\d{8}T\d{6}(?:-\d+)?\.json$")


def _herdr(args: list[str]) -> str:
    exe = os.environ.get("HERDR_BIN_PATH") or "herdr"
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=10, check=True).stdout


def resolve_session_dir(explicit: str | None) -> Path:
    """Explicit dir > session whose socket matches HERDR_SOCKET_PATH > default session > ~/.config/herdr."""
    if explicit:
        return Path(explicit).expanduser()
    try:
        sessions = json.loads(_herdr(["session", "list", "--json"])).get("sessions", [])
        sock = os.environ.get("HERDR_SOCKET_PATH")
        for s in sessions:
            if sock and s.get("socket_path") == sock:
                return Path(s["session_dir"])
        for s in sessions:
            if s.get("default"):
                return Path(s["session_dir"])
    except Exception:
        pass
    return Path(os.environ.get("HOME", "~")).expanduser() / ".config" / "herdr"


def summarize(data: dict) -> dict:
    tabs = panes = 0
    agents: dict[str, int] = {}
    for w in data.get("workspaces") or []:
        for t in w.get("tabs") or []:
            tabs += 1
            for p in (t.get("panes") or {}).values():
                panes += 1
                a = (p or {}).get("agent_session") or {}
                if a.get("value"):
                    k = a.get("agent") or "unknown"
                    agents[k] = agents.get(k, 0) + 1
    return {"workspaces": len(data.get("workspaces") or []), "tabs": tabs, "panes": panes, "agent_sessions": agents}


def _resume_enabled(session_dir: Path) -> bool | None:
    cfg = session_dir / "config.toml"
    if not cfg.exists():
        return True  # Herdr default
    try:
        import tomllib
        with cfg.open("rb") as f:
            return bool(tomllib.load(f).get("session", {}).get("resume_agents_on_restore", True))
    except Exception:
        return None


def _integrations() -> dict[str, int | None]:
    out = {}
    for line in _herdr(["integration", "status"]).splitlines():
        m = re.match(r"^(\S+):\s+(.*)$", line.strip())
        if not m or m.group(2).startswith("not installed"):
            continue
        v = re.search(r"\(v(\d+)\)", m.group(2))
        out[m.group(1)] = int(v.group(1)) if v else None
    return out


def _prune(bdir: Path, keep: int) -> list[str]:
    ours = sorted((p for p in bdir.iterdir() if p.is_file() and _NAME_RE.match(p.name)), key=lambda p: p.name)
    removed = []
    for p in ours[: max(0, len(ours) - keep)]:
        p.unlink()
        removed.append(p.name)
    return removed


def backup(session_dir: Path | str, keep: int = DEFAULT_KEEP, live: bool = False, now: dt.datetime | None = None) -> dict:
    session_dir = Path(session_dir)
    src = session_dir / "session.json"
    res: dict = {"ok": False, "session_dir": str(session_dir), "warnings": [], "busy": [], "checks": {}}
    if not src.is_file():
        res["error"] = f"no saved Herdr layout at {src}; nothing to back up"
        return res
    raw = src.read_bytes()
    try:
        data = json.loads(raw)
    except ValueError:
        res["error"] = f"{src} is not valid JSON; refusing to copy a corrupt layout (Herdr keeps its own recovery copies in session-backups/)"
        return res

    bdir = session_dir / BACKUP_DIR
    bdir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(bdir, 0o700)
    stamp = (now or dt.datetime.now()).strftime("%Y%m%dT%H%M%S")
    dest, n = bdir / f"session-{stamp}.json", 1
    while dest.exists():
        dest, n = bdir / f"session-{stamp}-{n}.json", n + 1
    fd, tmp = tempfile.mkstemp(dir=bdir, prefix=".tmp-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    digest = hashlib.sha256(raw).hexdigest()
    if hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
        res["error"] = f"verification failed: {dest} does not match {src}"
        return res

    res.update(ok=True, backup=str(dest), sha256=digest, bytes=len(raw), summary=summarize(data),
               pruned=_prune(bdir, keep), keep=keep)
    resume = _resume_enabled(session_dir)
    res["checks"]["resume_agents_on_restore"] = resume
    if resume is False:
        res["warnings"].append("config.toml sets resume_agents_on_restore = false: agent panes will come back as plain shells")
    elif resume is None:
        res["warnings"].append("config.toml could not be parsed; cannot confirm agents will resume")

    if live:
        try:
            ints = _integrations()
            res["checks"]["integrations"] = ints
            v = ints.get("claude")
            if res["summary"]["agent_sessions"].get("claude") and (v is None or v < MIN_CLAUDE_INTEGRATION):
                res["warnings"].append(f"claude integration v{v} is older than v{MIN_CLAUDE_INTEGRATION}: Claude panes will not auto-resume (herdr integration install claude)")
        except Exception:
            res["warnings"].append("integration status unavailable; cannot confirm agent resume support")
        try:
            panes = json.loads(_herdr(["pane", "list"]))["result"]["panes"]
            me = os.environ.get("HERDR_PANE_ID")  # the pane running this command is busy by definition
            for p in panes:
                if p.get("pane_id") != me and p.get("agent") and p.get("agent_status") in ("working", "blocked"):
                    res["busy"].append({"pane": p["pane_id"], "status": p["agent_status"],
                                        "title": p.get("terminal_title_stripped") or ""})
            for b in res["busy"]:
                res["warnings"].append(f"{b['pane']} is {b['status']}: its in-flight turn will be lost if Herdr stops now")
        except Exception:
            res["warnings"].append("live pane state unavailable; could not check for agents mid-turn")
    return res


def render(r: dict) -> str:
    if not r.get("ok"):
        return f"**Pre-restart safety copy FAILED** — {r.get('error')}\nNothing was changed."
    s = r["summary"]
    agents = ", ".join(f"{n} {k}" for k, n in sorted(s["agent_sessions"].items())) or "none"
    lines = [
        "**Safety copy saved** — Herdr layout backed up before restart/shutdown",
        f"- File: `{r['backup']}` ({r['bytes']} bytes, sha256 {r['sha256'][:12]}…, mode 0600)",
        f"- Captured: {s['workspaces']} workspaces, {s['tabs']} tabs, {s['panes']} panes; agent sessions to resume: {agents}",
    ]
    if r.get("pruned"):
        lines.append(f"- Pruned {len(r['pruned'])} older safety copies (kept newest {r.get('keep', DEFAULT_KEEP)})")
    lines.append("")
    lines.append("**Warnings**" if r["warnings"] else "**Checks:** agent resume on restore is enabled; no blockers found.")
    lines += [f"- {w}" for w in r["warnings"]]
    lines += [
        "",
        "**After the restart:** run `herdr`. Layout, folders and supported agent conversations come back;",
        "running processes, shells' screen output and any in-flight agent turn do not.",
        "",
        "**If the layout comes back wrong (manual rollback):**",
        "1. `herdr server stop`",
        f"2. `cp {Path(r['session_dir']) / 'session.json'} {Path(r['session_dir']) / 'session.json.replaced'}`",
        f"3. `cp {r['backup']} {Path(r['session_dir']) / 'session.json'}`",
        "4. `herdr`",
        "",
        "Not done by this command: stopping Herdr, closing panes or restarting anything. You do that.",
    ]
    return "\n".join(lines)
