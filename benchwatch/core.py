"""CSV validation and precisely defined season metrics (no I/O beyond caller's stream)."""

import csv
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from statistics import fmean, pvariance


@dataclass(frozen=True)
class Row:
    team: str
    game_id: str
    date: str
    player: str
    value: float
    home_away: str
    position: str


@dataclass(frozen=True)
class Policy:
    bench_percent: float = 75
    bench_games: int = 3
    max_roster_variance: float = 100
    max_home_away_imbalance_percent: float = 50

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("policy must be a JSON object")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown policy fields: {', '.join(sorted(unknown))}")
        policy = cls(**data)
        for key, value in asdict(policy).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be numeric")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        if type(policy.bench_games) is not int or policy.bench_games < 1:
            raise ValueError("bench_games must be a positive integer")
        if policy.bench_percent > 100:
            raise ValueError("bench_percent must be at most 100")
        return policy


def parse_log(stream):
    """Return valid rows and collected errors; callers must not audit partial input."""
    rows, errors, seen, games = [], [], set(), {}
    reader = csv.DictReader(stream, strict=True)
    try:
        fields = reader.fieldnames
    except csv.Error as exc:
        return [], [{"line": 1, "message": str(exc)}]
    required = {"game_id", "date", "player", "minutes_played", "home_away"}
    missing = required - set(fields or [])
    if missing:
        return [], [{"line": 1, "message": f"missing columns: {', '.join(sorted(missing))}"}]
    if len(fields) != len(set(fields)):
        return [], [{"line": 1, "message": "duplicate column names"}]
    while True:
        try:
            raw = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            errors.append({"line": reader.line_num, "message": f"malformed CSV: {exc}"})
            continue
        try:
            if None in raw or any(v is None for v in raw.values()):
                raise ValueError("row has the wrong number of fields")
            raw = {k: v.strip() for k, v in raw.items()}
            for key in required:
                if not raw[key]:
                    raise ValueError(f"{key} is empty")
            date.fromisoformat(raw["date"])
            if len(raw["date"]) != 10:
                raise ValueError("date must be YYYY-MM-DD")
            try:
                value = float(raw["minutes_played"])
            except ValueError:
                raise ValueError("minutes_played must be numeric") from None
            if not math.isfinite(value) or value < 0 or value > 1e12:
                raise ValueError("minutes_played must be finite and between 0 and 1e12")
            if raw["home_away"] not in {"H", "A"}:
                raise ValueError("home_away must be H or A")
            team = raw.get("team", "Team")
            if not team:
                raise ValueError("team is empty")
            key = (team, raw["game_id"], raw["player"])
            if key in seen:
                raise ValueError("duplicate (game_id, player) row within team")
            game_key = (team, raw["game_id"])
            metadata = (raw["date"], raw["home_away"])
            if game_key in games and games[game_key] != metadata:
                raise ValueError("conflicting date or home_away for game")
            seen.add(key)
            games[game_key] = metadata
            rows.append(
                Row(
                    team,
                    raw["game_id"],
                    raw["date"],
                    raw["player"],
                    value,
                    raw["home_away"],
                    raw.get("position", ""),
                )
            )
        except ValueError as exc:
            errors.append({"line": reader.line_num, "message": str(exc)})
    if not rows and not errors:
        errors.append({"line": 1, "message": "no game rows"})
    return rows, errors


def audit_team(rows, policy):
    players = sorted({r.player for r in rows})
    games = sorted({(r.date, r.game_id) for r in rows})
    by_player = {p: {r.game_id: r for r in rows if r.player == p} for p in players}
    means = {p: fmean(r.value for r in by_player[p].values()) for p in players}
    team_mean = fmean(means.values())
    roster_variance = pvariance(means.values())
    threshold = team_mean * policy.bench_percent / 100
    violations, summaries = [], []

    def violation(player, ids, rule, severity, actual, limit, excess):
        violations.append(
            dict(
                player=player,
                games=ids,
                rule=rule,
                severity=severity,
                actual=actual,
                limit=limit,
                excess=excess,
            )
        )

    for player in players:
        entries = by_player[player]
        values = [r.value for r in entries.values()]
        streaks, streak = [], []
        for _, gid in games:
            if gid in entries and entries[gid].value < threshold:
                streak.append(gid)
            elif streak:
                streaks.append(streak)
                streak = []
        if streak:
            streaks.append(streak)
        for ids in streaks:
            if len(ids) >= policy.bench_games:
                deficit = threshold - fmean(entries[g].value for g in ids)
                violation(
                    player, ids, "bench_streak", "HIGH", len(ids), policy.bench_games, deficit
                )
        home = [r.value for r in entries.values() if r.home_away == "H"]
        away = [r.value for r in entries.values() if r.home_away == "A"]
        hm, am = fmean(home) if home else None, fmean(away) if away else None
        imbalance = None
        if hm is not None and am is not None:
            imbalance = 100 * abs(hm - am) / ((hm + am) / 2) if hm + am else 0
            if imbalance > policy.max_home_away_imbalance_percent:
                violation(
                    player,
                    [gid for _, gid in games if gid in entries],
                    "home_away_imbalance",
                    "MEDIUM",
                    imbalance,
                    policy.max_home_away_imbalance_percent,
                    imbalance - policy.max_home_away_imbalance_percent,
                )
        positions = defaultdict(float)
        for row in entries.values():
            positions[row.position or "(unspecified)"] += row.value
        deviation = means[player] - team_mean
        summaries.append(
            dict(
                player=player,
                games_played=len(values),
                total=sum(values),
                mean=means[player],
                deviation_from_team_mean=deviation,
                mean_squared_deviation_from_team_mean=fmean((v - team_mean) ** 2 for v in values),
                rms_deviation_from_team_mean=math.sqrt(fmean((v - team_mean) ** 2 for v in values)),
                variance=pvariance(values),
                stddev=math.sqrt(pvariance(values)),
                min=min(values),
                max=max(values),
                longest_bench_streak=max(map(len, streaks), default=0),
                home_total=sum(home),
                away_total=sum(away),
                home_mean=hm,
                away_mean=am,
                imbalance_percent=imbalance,
                position_time=dict(sorted(positions.items())),
            )
        )
    if roster_variance > policy.max_roster_variance:
        violation(
            None,
            [g for _, g in games],
            "roster_variance",
            "HIGH",
            roster_variance,
            policy.max_roster_variance,
            roster_variance - policy.max_roster_variance,
        )
    violations.sort(
        key=lambda v: (
            0 if v["severity"] == "HIGH" else 1,
            -v["excess"],
            v["player"] or "",
            v["rule"],
        )
    )
    return dict(
        team=rows[0].team,
        team_mean=team_mean,
        roster_variance=roster_variance,
        roster_stddev=math.sqrt(roster_variance),
        players=summaries,
        violations=violations,
    )


def audit(rows, policy, unit):
    teams = [
        audit_team([r for r in rows if r.team == team], policy)
        for team in sorted({r.team for r in rows})
    ]
    violations = [dict(v, team=t["team"]) for t in teams for v in t["violations"]]
    violations.sort(
        key=lambda v: (
            0 if v["severity"] == "HIGH" else 1,
            -v["excess"],
            v["team"],
            v["player"] or "",
            v["rule"],
        )
    )
    return dict(
        unit=unit,
        policy=asdict(policy),
        teams=teams,
        violations=violations,
        league=dict(
            team_count=len(teams),
            player_count=sum(len(t["players"]) for t in teams),
            total=sum(p["total"] for t in teams for p in t["players"]),
        ),
        errors=[],
    )
