import io
import json
import math
import subprocess
import sys
from datetime import date

import pytest

from benchwatch.cli import main
from benchwatch.core import Policy, Row, audit, audit_team, parse_log
from benchwatch.licensing import issue_license, valid_license

HEADER = "game_id,date,player,minutes_played,home_away,position\n"


def season(a, b=None):
    b = b if b is not None else [20] * len(a)
    return [
        Row("T", f"g{i}", f"2026-09-{i + 1:02}", p, value, "H" if i % 2 == 0 else "A", "Forward")
        for p, values in [("A", a), ("B", b)]
        for i, value in enumerate(values)
    ]


def test_hand_computed_math():
    team = audit_team(season([10, 20], [20, 30]), Policy())
    assert team["team_mean"] == 20
    assert team["roster_variance"] == 25
    assert team["roster_stddev"] == 5
    a = team["players"][0]
    assert (a["total"], a["mean"], a["min"], a["max"]) == (30, 15, 10, 20)
    assert a["variance"] == 25
    assert a["stddev"] == 5
    assert a["deviation_from_team_mean"] == -5
    assert a["mean_squared_deviation_from_team_mean"] == 50
    assert a["rms_deviation_from_team_mean"] == pytest.approx(math.sqrt(50))
    assert a["imbalance_percent"] == pytest.approx(100 * 10 / 15)
    assert a["position_time"] == {"Forward": 30}


@pytest.mark.parametrize(
    "values,expected,ids",
    [
        ([0, 0, 20, 20], 2, ["g0", "g1"]),
        ([20, 20, 0, 0], 2, ["g2", "g3"]),
        ([0, 20, 0, 20], 1, []),
        ([0, 0, 0, 0], 4, ["g0", "g1", "g2", "g3"]),
    ],
)
def test_streak_edges(values, expected, ids):
    team = audit_team(season(values), Policy(bench_games=2))
    assert team["players"][0]["longest_bench_streak"] == expected
    found = [v for v in team["violations"] if v["rule"] == "bench_streak"]
    assert [v["games"] for v in found] == ([ids] if ids else [])
    if found:
        assert found[0]["severity"] == "HIGH"
        assert found[0]["excess"] > 0


def test_exact_boundary_and_fair_season():
    result = audit(season([20] * 4, [20] * 4), Policy(bench_percent=100), "shifts")
    assert result["violations"] == []
    assert result["teams"][0]["players"][0]["longest_bench_streak"] == 0


def test_missing_game_breaks_streak():
    rows = [r for r in season([0, 0, 0]) if not (r.player == "A" and r.game_id == "g1")]
    result = audit_team(rows, Policy(bench_games=2))
    assert result["players"][0]["games_played"] == 2
    assert result["players"][0]["longest_bench_streak"] == 1
    assert not any(v["rule"] == "bench_streak" for v in result["violations"])


def test_equal_weight_baseline_and_uneven_home_away_counts():
    rows = season([20, 20, 20], [10, 10, 10])
    rows = [r for r in rows if r.player == "A" or r.game_id == "g0"]
    team = audit_team(rows, Policy())
    assert team["team_mean"] == 15
    assert team["players"][0]["imbalance_percent"] == 0
    assert team["players"][1]["imbalance_percent"] is None


def test_variance_and_balance_rules_boundaries():
    rows = season([10, 20], [20, 30])
    team = audit_team(rows, Policy(max_roster_variance=24, max_home_away_imbalance_percent=60))
    assert [v["rule"] for v in team["violations"]] == ["roster_variance", "home_away_imbalance"]
    assert team["violations"][0]["excess"] == 1
    assert (
        audit_team(
            rows, Policy(max_roster_variance=25, max_home_away_imbalance_percent=100 * 10 / 15)
        )["violations"]
        == []
    )


def test_zero_season():
    team = audit_team(season([0, 0], [0, 0]), Policy())
    assert team["violations"] == []
    assert team["players"][0]["imbalance_percent"] == 0


@pytest.mark.parametrize(
    "body,message",
    [
        ("g,2026-09-01,A,bad,H,F", "numeric"),
        ("g,2026-09-01,A,nan,H,F", "finite"),
        ("g,2026-09-01,A,inf,H,F", "finite"),
        ("g,2026-09-01,A,-1,H,F", "finite"),
        ("g,2026-09-01,A,1e13,H,F", "finite"),
        ("g,2026-09-01,A,10,X,F", "H or A"),
        ("g,invalid,A,10,H,F", "Invalid isoformat"),
        ("g,20260901,A,10,H,F", "YYYY-MM-DD"),
        ("g,2026-09-01,,10,H,F", "empty"),
        ("g,2026-09-01,A,10,H", "number of fields"),
        ("g,2026-09-01,A,10,H,F,extra", "number of fields"),
        ('"unterminated', "malformed CSV"),
    ],
)
def test_bad_rows(body, message):
    rows, errors = parse_log(io.StringIO(HEADER + body + "\n"))
    assert not rows
    assert message in errors[0]["message"]


def test_collect_errors_and_continue():
    text = HEADER + "g,2026-09-01,A,10,H,F\n" * 2
    text += "g,2026-09-01,B,no,H,F\ng,2026-09-01,C,10,X,F\n"
    text += "g,2026-09-02,D,10,H,F\ng,2026-09-01,E,10,H,F\n"
    rows, errors = parse_log(io.StringIO(text))
    assert [r.player for r in rows] == ["A", "E"]
    assert [e["line"] for e in errors] == [3, 4, 5, 6]


@pytest.mark.parametrize(
    "text,message",
    [
        ("", "missing columns"),
        (HEADER, "no game rows"),
        (HEADER.strip() + ",player\n", "duplicate column"),
        ('"broken', "unexpected end"),
        (HEADER.strip() + ",team\ng,2026-09-01,A,10,H,F,\n", "team is empty"),
    ],
)
def test_headers_and_empty(text, message):
    assert message in parse_log(io.StringIO(text))[1][0]["message"]


def test_optional_position_and_team_scoped_duplicates():
    text = "game_id,date,player,minutes_played,home_away,team\n"
    text += "g,2026-09-01,A,10,H,T1\ng,2026-09-01,A,20,H,T2\n"
    rows, errors = parse_log(io.StringIO(text))
    assert not errors
    result = audit(rows, Policy(), "innings")
    assert result["league"] == {"team_count": 2, "player_count": 2, "total": 30}
    assert result["teams"][0]["players"][0]["position_time"] == {"(unspecified)": 10}


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"typo": 1},
        {"bench_percent": True},
        {"bench_percent": "75"},
        {"bench_percent": -1},
        {"bench_percent": 101},
        {"bench_games": 0},
        {"bench_games": 2.5},
        {"max_roster_variance": float("nan")},
        {"max_roster_variance": float("inf")},
    ],
)
def test_invalid_policy(data):
    with pytest.raises(ValueError):
        Policy.from_dict(data)


def test_policy_defaults():
    assert Policy.from_dict({}) == Policy()


@pytest.fixture
def files(tmp_path):
    log = tmp_path / "log.csv"
    policy = tmp_path / "policy.json"
    log.write_text(HEADER + "g,2026-09-01,A,20,H,F\ng,2026-09-01,B,20,H,F\n")
    policy.write_text("{}")
    return log, policy


@pytest.mark.parametrize("unit", ["minutes", "innings", "shifts"])
def test_cli_json_fair(files, capsys, unit):
    log, policy = files
    assert (
        main(["check", str(log), "--policy", str(policy), "--unit", unit, "--format", "json"]) == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["unit"] == unit
    assert result["violations"] == []


def test_cli_high_and_report(files, capsys):
    log, policy = files
    log.write_text(HEADER + "g,2026-09-01,A,0,H,F\ng,2026-09-01,B,100,H,F\n")
    assert main(["check", str(log), "--policy", str(policy)]) == 1
    assert "HIGH" in capsys.readouterr().out
    assert main(["report", str(log)]) == 0
    assert "roster_variance" in capsys.readouterr().out


def test_cli_medium_only(files, capsys):
    log, policy = files
    log.write_text(HEADER + "g,2026-09-01,A,0,H,F\nh,2026-09-02,A,20,A,F\n")
    assert main(["check", str(log), "--policy", str(policy)]) == 0
    assert "MEDIUM" in capsys.readouterr().out


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_cli_invalid_rows(files, capsys, fmt):
    log, _ = files
    log.write_text(HEADER + "bad\nwrong\n")
    assert main(["report", str(log), "--format", fmt]) == 2
    output = capsys.readouterr().out
    assert ("errors" if fmt == "json" else "ERROR line") in output
    assert "teams" not in output


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_cli_missing_file(capsys, fmt):
    assert main(["report", "missing-file.csv", "--format", fmt]) == 2
    captured = capsys.readouterr()
    assert ("errors" in captured.out) if fmt == "json" else ("ERROR" in captured.err)


def test_cli_invalid_policy(files, capsys):
    log, policy = files
    policy.write_text("{broken")
    assert main(["report", str(log), "--policy", str(policy)]) == 2
    assert "ERROR" in capsys.readouterr().err


def test_license_validation():
    key = issue_license("League", "2030-01-01")
    assert valid_license(key, date(2030, 1, 1))
    assert not valid_license(key, date(2030, 1, 2))
    assert not valid_license(key + "x")
    assert not valid_license("garbage")
    assert not valid_license("x.y.z")
    assert not valid_license(issue_license("", "2030-01-01"))


def test_cli_license_and_gate(files, capsys):
    log, _ = files
    log.write_text(HEADER.strip() + ",team\ng,2026-09-01,A,20,H,F,T1\ng,2026-09-01,A,20,H,F,T2\n")
    assert main(["report", str(log)]) == 2
    assert "free tier" in capsys.readouterr().err
    assert main(["report", str(log), "--multi-team"]) == 2
    assert "license" in capsys.readouterr().err
    assert main(["demo-license", "--holder", "League", "--expires", "2099-12-31"]) == 0
    key = capsys.readouterr().out.strip()
    assert main(["report", str(log), "--multi-team", "--license-key", key]) == 0
    assert "2 team(s)" in capsys.readouterr().out
    assert main(["demo-license", "--holder", "", "--expires", "2099-12-31"]) == 2
    assert main(["demo-license", "--holder", "L", "--expires", "oops"]) == 2


def test_roster_limit(files):
    log, _ = files
    log.write_text(HEADER + "".join(f"g,2026-09-01,P{i},20,H,F\n" for i in range(16)))
    assert main(["report", str(log)]) == 2
    key = issue_license("L", "2099-12-31")
    assert main(["report", str(log), "--multi-team", "--license-key", key]) == 0


def test_real_module_exit_codes(files):
    log, policy = files
    for args, expected in [
        (["report", str(log)], 0),
        (["check", str(log)], 2),
        (["report", "missing.csv"], 2),
    ]:
        result = subprocess.run(
            [sys.executable, "-m", "benchwatch", *args], capture_output=True, text=True
        )
        assert result.returncode == expected
    log.write_text(HEADER + "g,2026-09-01,A,0,H,F\ng,2026-09-01,B,100,H,F\n")
    assert (
        subprocess.run(
            [sys.executable, "-m", "benchwatch", "check", str(log), "--policy", str(policy)],
            capture_output=True,
        ).returncode
        == 1
    )


def test_extremely_large_policy_number_is_rejected():
    with pytest.raises(ValueError, match="finite"):
        Policy.from_dict({"max_roster_variance": 10**1000})


def test_two_streaks_are_separate_and_games_sort_chronologically():
    rows = season([0, 0, 20, 0, 0])
    team = audit_team(list(reversed(rows)), Policy(bench_games=2))
    streaks = [v["games"] for v in team["violations"] if v["rule"] == "bench_streak"]
    assert streaks == [["g0", "g1"], ["g3", "g4"]]


def test_league_violations_are_globally_severity_ranked():
    from dataclasses import replace

    medium = [replace(r, team="A") for r in season([0, 20], [0, 20])]
    high = [replace(r, team="Z") for r in season([0, 0], [100, 100])]
    result = audit(medium + high, Policy(bench_games=2), "minutes")
    severities = [v["severity"] for v in result["violations"]]
    assert severities == ["HIGH", "HIGH", "MEDIUM", "MEDIUM"]
    assert result["violations"][0]["team"] == "Z"


def test_position_distribution():
    from dataclasses import replace

    rows = season([10, 20])
    rows[1] = replace(rows[1], position="Defender")
    assert audit_team(rows, Policy())["players"][0]["position_time"] == {
        "Defender": 20,
        "Forward": 10,
    }
