#!/usr/bin/env bash
# Runs after each beta session. Undoes the session's commits if tests fail or a safety-critical file changed.
# Usage: beta/verify.sh <commit the session started from>
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
start="$1"
SAFETY='^(root-helper/|systemd/|guardian/protect\.py|guardian/config\.py|install\.sh|uninstall\.sh|beta/(PROMPT\.md|daily\.sh|verify\.sh|settings\.json))'

if [ -n "$(git status --porcelain)" ]; then  # keep uncommitted leftovers recoverable, but not live
  git stash push -u -m "beta: uncommitted at session end $(date +%F)" >/dev/null
fi
[ "$(git rev-parse HEAD)" = "$start" ] && { echo "beta verify: no commits this session."; exit 0; }

touched="$(git diff --name-only "$start" HEAD | grep -E "$SAFETY" || true)"
if [ -z "$touched" ] && .venv/bin/pytest -q >/dev/null 2>&1; then
  msg="Fixes kept: $(git log --oneline "$start..HEAD" | head -3 | tr '\n' ' ')"
  echo "beta verify: OK. $msg"
  notify-send -a Guardian "Guardian beta: fixes applied" "$msg"
else
  why="${touched:+touched safety files: $touched}"
  why="${why:-tests failed}"
  git revert --no-edit "$start..HEAD" >/dev/null
  echo "beta verify: REVERTED ($why)" | tee -a beta/LOG.md
  notify-send -a Guardian -u critical "Guardian beta: fixes reverted" "$why — see beta/LOG.md"
fi
