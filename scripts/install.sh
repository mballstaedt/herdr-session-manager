#!/usr/bin/env bash
# Install / update / uninstall herdr-session-manager for Claude Code and Codex.
#
#   scripts/install.sh               install or update both (idempotent)
#   scripts/install.sh --dry-run     show what would change; write nothing
#   scripts/install.sh --uninstall   remove both installs (never touches this checkout)
#   options: --claude-only | --codex-only
#
# Claude: a copy at ~/.claude/skills/herdr-session-manager (re-run after pulling updates).
# Codex:  a symlink ~/.agents/skills/herdr-session-manager -> this checkout.
# Overrides for tests: HSM_CLAUDE_HOME, HSM_AGENTS_SKILLS.
set -euo pipefail

NAME=herdr-session-manager
MARKER=.hsm-install
SKILL_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY=0; UNINSTALL=0; DO_CLAUDE=1; DO_CODEX=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --claude-only) DO_CODEX=0 ;;
    --codex-only) DO_CLAUDE=0 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

CLAUDE_DEST="${HSM_CLAUDE_HOME:-$HOME/.claude}/skills/$NAME"
CODEX_LINK="${HSM_AGENTS_SKILLS:-$HOME/.agents/skills}/$NAME"
say() { printf '%s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then say "  would: $*"; else "$@"; fi; }

# ------------------------------------------------------------------ Claude
claude_step() {
  if [ -e "$CLAUDE_DEST" ] && [ ! -f "$CLAUDE_DEST/$MARKER" ]; then
    say "claude: refusing to overwrite $CLAUDE_DEST (not created by this installer)" >&2; return 1
  fi
  if [ "$UNINSTALL" = 1 ]; then
    if [ -e "$CLAUDE_DEST" ]; then say "claude: removing $CLAUDE_DEST"; run rm -rf "$CLAUDE_DEST"; else say "claude: not installed"; fi
    return 0
  fi
  if [ -e "$CLAUDE_DEST" ] && diff -rq -x __pycache__ -x .DS_Store -x .git -x "$MARKER" "$SKILL_DIR" "$CLAUDE_DEST" >/dev/null 2>&1; then
    say "claude: no changes ($CLAUDE_DEST)"; return 0
  fi
  say "claude: copying to $CLAUDE_DEST"
  [ "$DRY" = 1 ] && return 0
  # Stage next to the destination so the final mv is a same-filesystem rename.
  local tmp; mkdir -p "$(dirname "$CLAUDE_DEST")"; tmp="$(mktemp -d "$(dirname "$CLAUDE_DEST")/.hsm.XXXXXX")"
  (cd "$SKILL_DIR" && tar --exclude __pycache__ --exclude .DS_Store --exclude .git -cf - .) | (cd "$tmp" && tar -xf -)
  printf 'source=%s\n' "$SKILL_DIR" > "$tmp/$MARKER"
  rm -rf "$CLAUDE_DEST"; mv "$tmp" "$CLAUDE_DEST"
}

# ------------------------------------------------------------------ Codex
codex_step() {
  if [ -e "$CODEX_LINK" ] && [ ! -L "$CODEX_LINK" ]; then
    say "codex: refusing to overwrite $CODEX_LINK (not a symlink)" >&2; return 1
  fi
  if [ "$UNINSTALL" = 1 ]; then
    if [ -L "$CODEX_LINK" ]; then say "codex: removing $CODEX_LINK"; run rm -f "$CODEX_LINK"; else say "codex: not installed"; fi
    return 0
  fi
  if [ -L "$CODEX_LINK" ] && [ "$(cd -P "$CODEX_LINK" 2>/dev/null && pwd)" = "$SKILL_DIR" ]; then
    say "codex: already installed ($CODEX_LINK)"; return 0
  fi
  say "codex: linking $CODEX_LINK -> $SKILL_DIR"
  run mkdir -p "$(dirname "$CODEX_LINK")"
  run ln -sfn "$SKILL_DIR" "$CODEX_LINK"
}

status=0
[ "$DO_CLAUDE" = 1 ] && { claude_step || status=1; }
[ "$DO_CODEX" = 1 ] && { codex_step || status=1; }
exit $status
