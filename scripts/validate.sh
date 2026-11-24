#!/usr/bin/env bash
# Validate herdr-session-manager: SKILL.md front matter, unit tests, shell syntax,
# and (unless --no-install) both installed projections. --live also runs the read-only
# context + list modes against the current Herdr session.
set -uo pipefail
SKILL_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME=herdr-session-manager
CHECK_INSTALL=1; LIVE=0
for a in "$@"; do case "$a" in --no-install) CHECK_INSTALL=0 ;; --live) LIVE=1 ;; esac; done
fail=0
ok()   { printf 'PASS %s\n' "$*"; }
bad()  { printf 'FAIL %s\n' "$*"; fail=1; }

python3 -B - "$SKILL_DIR/SKILL.md" "$NAME" <<'PY' && ok "SKILL.md front matter" || bad "SKILL.md front matter"
import re, sys
text = open(sys.argv[1]).read()
m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
assert m, "missing YAML front matter"
fm = {}
for line in m.group(1).splitlines():
    k, _, v = line.partition(":")
    fm[k.strip()] = v.strip().strip('"')
assert fm.get("name") == sys.argv[2], f"name must be {sys.argv[2]}"
d = fm.get("description", "")
assert 40 <= len(d) <= 1024, f"description length {len(d)}"
assert set(fm) <= {"name", "description", "license", "metadata", "allowed-tools"}, f"unexpected keys {set(fm)}"
PY

for f in "$SKILL_DIR"/scripts/*.sh "$SKILL_DIR/scripts/hsm"; do bash -n "$f" || bad "bash -n $f"; done && ok "shell syntax"
python3 -B -m py_compile "$SKILL_DIR/scripts/inventory.py" && ok "inventory.py compiles" || bad "inventory.py compiles"
(cd "$SKILL_DIR" && PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests 2>&1 | tail -3) | tee /dev/stderr | grep -q '^OK' && ok "unit tests" || bad "unit tests"

if command -v ya >/dev/null 2>&1 || [ -x /opt/homebrew/bin/ya ]; then
  python3 -B -c "import sys; sys.path.insert(0, '$SKILL_DIR/scripts'); import inventory as i; ok, why = i.yazi_config_ok(); print(why); sys.exit(0 if ok else 1)" \
    && ok "Yazi guard config parses" || bad "Yazi guard config parses"
else
  printf 'SKIP Yazi not installed (files mode unavailable)\n'
fi

if [ "$CHECK_INSTALL" = 1 ]; then
  C="$HOME/.claude/skills/$NAME"
  if [ -f "$C/SKILL.md" ] && diff -rq -x __pycache__ -x .DS_Store -x .git -x .hsm-install "$SKILL_DIR" "$C" >/dev/null; then ok "Claude copy $C matches this checkout"; else bad "Claude copy $C missing or out of date (run scripts/install.sh)"; fi
  L="$HOME/.agents/skills/$NAME"
  if [ -L "$L" ] && [ "$(cd -P "$L" && pwd)" = "$SKILL_DIR" ]; then ok "Codex symlink $L -> this checkout"; else bad "Codex symlink $L missing or wrong"; fi
fi

if [ "$LIVE" = 1 ]; then
  python3 -B "$SKILL_DIR/scripts/inventory.py" context >/dev/null && ok "live Herdr context" || bad "live Herdr context"
  python3 -B "$SKILL_DIR/scripts/inventory.py" list >/dev/null && ok "live list" || bad "live list"
fi
exit $fail
