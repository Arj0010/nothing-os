You are running the daily **Guardian beta review** (10-day beta started 2026-10-07) in ~/guardian.
Edits, tests and commits are pre-approved — **fix things yourself, then explain**. Don't wait for confirmation.

## Goal
Find what Guardian got wrong in the last 24 h, fix the cause in code so it doesn't recur, and prove the fix with tests.

## Steps
1. Read context: `docs/specs/2026-10-07-guardian-design.md` (design + safety invariants), `beta/LOG.md`
   (earlier days' fixes — don't redo or undo them without reason), today's report (newest file in `beta/reports/`)
   and the previous reports.
2. Gather evidence beyond the report as needed (read-only): `.venv/bin/guardian scan`, `.venv/bin/guardian history`,
   `journalctl --user -u guardian.service --since -24h`, `journalctl -u earlyoom --since -24h`, `free -h`,
   `cat /proc/pressure/memory`, `df -h /`.
3. Classify each problem:
   - **False alarm** — flagged something that was fine (e.g. threshold too tight, normal process flagged as a leak).
   - **Missed** — RAM/disk trouble or an earlyoom kill that Guardian didn't warn about first.
   - **Bad action** — an action failed, was refused wrongly, or freed nothing (e.g. journal vacuum frees 0 B).
   - **Noise** — re-asked too often, digest spam, slow or failed runs.
   If nothing went wrong, say so plainly and stop — do not change code for the sake of it.
4. For each real problem, fix it with TDD: write a failing test that reproduces it → fix →
   `.venv/bin/pytest -q` (all must pass). Keep fixes minimal; prefer adjusting a threshold/rule over adding machinery.
5. Append to `beta/LOG.md`: date, problems found (with evidence), fixes made, anything deferred.
6. Commit on the current `beta` branch (include today's report and LOG.md): one commit for the day, message
   `beta day N: <summary>`. **No Co-Authored-By trailers.** The timer runs from this checkout, so fixes are live
   for the next 2-minute run.
7. Finish with a short plain-language summary for Arjun: what went wrong, what you changed, what you deferred.

## Hard rules
When the session ends, `beta/verify.sh` automatically **reverts all of today's commits** if tests fail or any of
these changed: `root-helper/`, `systemd/`, `guardian/protect.py`, `guardian/config.py`, `install.sh`, `uninstall.sh`,
`beta/PROMPT.md`, `beta/daily.sh`, `beta/verify.sh`, `beta/settings.json`. If a fix truly needs one of them, don't make it — write it
under "Needs Arjun" in `beta/LOG.md` instead.
- Nothing outside the action registry in `guardian/actions.py` may change the system.
- Do not add new deletable paths, widen `CACHE_PATHS`, remove protected process names, or add root-helper verbs.
- The emergency auto-path may only run the zero-risk list.
- Do not run `install.sh`, `uninstall.sh`, `sudo`/`pkexec`, or delete user files while investigating.
