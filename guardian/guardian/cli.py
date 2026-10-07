import argparse
import sys
import time

from .actions import ActionContext
from .config import state_db
from .flows import Deps, act_flow, ask_flow, review_flow, run_once
from .report import build_report
from .state import State


def default_deps(state: State) -> Deps:
    return Deps(ctx=ActionContext(protected=state.protected()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="guardian", description="Disk & process guardian (asks before acting).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="one check cycle (used by the systemd timer)")
    sc = sub.add_parser("scan", help="show current findings; never acts")
    sc.add_argument("--dry-run", action="store_true", help="accepted for clarity; scan never acts")
    for name in ("ask", "act"):
        sub.add_parser(name, help=f"{name} on one finding (used internally)").add_argument("finding_id")
    sub.add_parser("review", help="approve pending findings interactively")
    sub.add_parser("protect", help="never kill processes matching NAME (glob)").add_argument("name")
    sub.add_parser("history", help="show recent actions")
    rp = sub.add_parser("report", help="summary of the last N hours (beta run)")
    rp.add_argument("--hours", type=int, default=24)
    args = ap.parse_args(argv)

    state = State(state_db())
    deps = default_deps(state)
    if args.cmd == "run":
        run_once(state, deps)
    elif args.cmd == "scan":
        findings = run_once(state, deps, dry_run=True)
        if not findings:
            print("All clear.")
        for f in findings:
            print(f"[{f.severity.value}] {f.title}\n  {f.reason}")
            for a in f.actions:
                print(f"    would: {a.name}{' ' + a.target if a.target else ''}")
        print("(dry run — nothing executed)")
    elif args.cmd == "ask":
        ask_flow(state, deps, args.finding_id)
    elif args.cmd == "act":
        act_flow(state, deps, args.finding_id)
    elif args.cmd == "review":
        review_flow(state, deps)
    elif args.cmd == "protect":
        state.add_protected(args.name)
        print(f"Protected: {args.name}")
    elif args.cmd == "report":
        print(build_report(state, now=time.time(), hours=args.hours), end="")
    elif args.cmd == "history":
        for at, _fid, action, ok, detail in state.history():
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
            print(f"{stamp}  {'ok  ' if ok else 'FAIL'}  {action}  {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
