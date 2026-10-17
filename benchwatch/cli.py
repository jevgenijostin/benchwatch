"""Command-line interface. Exit 0: successful; 1: HIGH check violation; 2: input error."""

import argparse
import json
import sys

from .core import Policy, audit, parse_log
from .licensing import issue_license, valid_license


def parser():
    root = argparse.ArgumentParser(prog="benchwatch")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("report", "check"):
        command = commands.add_parser(name)
        command.add_argument("game_log")
        command.add_argument("--policy", required=name == "check")
        command.add_argument("--unit", choices=["minutes", "innings", "shifts"], default="minutes")
        command.add_argument("--format", choices=["text", "json"], default="text")
        command.add_argument("--multi-team", action="store_true")
        command.add_argument("--license-key", default="")
    demo = commands.add_parser("demo-license", help="issue a public-secret demo key")
    demo.add_argument("--holder", required=True)
    demo.add_argument("--expires", required=True, help="inclusive YYYY-MM-DD expiry")
    return root


def render_text(result):
    if result["errors"]:
        return "\n".join(f"ERROR line {e['line']}: {e['message']}" for e in result["errors"])
    unit = result["unit"]
    lines = [
        f"BenchWatch | {unit}",
        f"League: {result['league']['team_count']} team(s), "
        f"{result['league']['player_count']} player(s)",
    ]
    for team in result["teams"]:
        lines.append(
            f"\nTeam {team['team']} | mean {team['team_mean']:.2f} {unit}; "
            f"roster variance {team['roster_variance']:.2f} {unit}^2"
        )
        for p in team["players"]:
            balance = "n/a" if p["imbalance_percent"] is None else f"{p['imbalance_percent']:.2f}%"
            lines.append(
                f"  {p['player']}: total {p['total']:.2f}, mean {p['mean']:.2f}, "
                f"games {p['games_played']}, min/max {p['min']:.2f}/{p['max']:.2f}, "
                f"variance/stddev {p['variance']:.2f}/{p['stddev']:.2f}, "
                f"team deviation {p['deviation_from_team_mean']:+.2f}, "
                f"team RMS deviation {p['rms_deviation_from_team_mean']:.2f}, "
                f"bench streak {p['longest_bench_streak']}, H/A imbalance {balance}"
            )
            lines.append(
                f"    H/A totals {p['home_total']:.2f}/{p['away_total']:.2f}; "
                f"position time: {json.dumps(p['position_time'], sort_keys=True)}"
            )
    lines.append(f"\nViolations: {len(result['violations'])}")
    for v in result["violations"]:
        lines.append(
            f"  {v['severity']} {v['team']}/{v['player'] or '(roster)'} "
            f"{v['rule']} games={','.join(v['games'])} "
            f"actual={v['actual']:.2f} limit={v['limit']:.2f} excess={v['excess']:.2f}"
        )
    return "\n".join(lines)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "demo-license":
            if not args.holder.strip():
                raise ValueError("holder must not be blank")
            print(issue_license(args.holder, args.expires))
            return 0
        policy = Policy()
        if args.policy:
            with open(args.policy, encoding="utf-8-sig") as stream:
                policy = Policy.from_dict(json.load(stream))
        with open(args.game_log, newline="", encoding="utf-8-sig") as stream:
            rows, errors = parse_log(stream)
        if errors:
            result = dict(errors=errors)
            print(json.dumps(result, indent=2) if args.format == "json" else render_text(result))
            return 2
        teams = {r.team for r in rows}
        if args.multi_team:
            if not valid_license(args.license_key):
                raise ValueError("multi-team mode requires a valid demo --license-key")
        elif len(teams) > 1 or len({r.player for r in rows}) > 15:
            raise ValueError(
                "free tier allows one team with at most 15 players; "
                "use --multi-team and a demo --license-key"
            )
        result = audit(rows, policy, args.unit)
        print(
            json.dumps(result, indent=2, allow_nan=False)
            if args.format == "json"
            else render_text(result)
        )
        return int(
            args.command == "check" and any(v["severity"] == "HIGH" for v in result["violations"])
        )
    except (OSError, ValueError, UnicodeError) as exc:
        if getattr(args, "format", None) == "json":
            print(json.dumps({"errors": [{"line": None, "message": str(exc)}]}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2
