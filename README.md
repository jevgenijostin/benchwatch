# BenchWatch

**Did every kid actually get fair playing time?** BenchWatch audits a season's game
log against an explicit league policy. Parents, recreational coaches, and league
operators get reproducible per-player metrics and severity-ranked violations.
All auditing and demo licensing run locally with the Python standard library;
there are no network calls, telemetry, or runtime dependencies.

## Install and run

Requires Python 3.11 or newer. From this repository:

```console
python -m pip install -e .
benchwatch report examples/fair.csv
benchwatch report examples/fair.csv --policy examples/policy.json --format json
benchwatch report examples/fair.csv --unit innings
benchwatch check examples/fair.csv --policy examples/policy.json
```

`python -m benchwatch` also works. `--unit minutes|innings|shifts` changes labels
only; the numeric CSV column remains `minutes_played` in all three modes.
Counts may be fractional. Policy thresholds retain the same numeric values.

Example text output (excerpt):

```text
BenchWatch | minutes
League: 1 team(s), 2 player(s)

Team Team | mean 20.00 minutes; roster variance 0.00 minutes^2
  Alex: total 60.00, mean 20.00, games 3, min/max 20.00/20.00, variance/stddev 0.00/0.00, team deviation +0.00, team RMS deviation 0.00, bench streak 0, H/A imbalance 0.00%

Violations: 0
```

The complete text report includes every player's home/away totals and position
time. JSON includes all metrics, the effective policy, team reports, league
counts and total time, and a combined severity-ranked violation list.

Exit codes:

- `0`: successful report; or a check with no HIGH violations.
- `1`: `check` found at least one HIGH violation.
- `2`: invalid CSV, policy, license, CLI arguments, or file access error.

`report` exits zero even when it finds violations. `check` requires `--policy`.
Both commands accept `--format`, `--unit`, `--multi-team`, and `--license-key`.
CSV errors include line numbers (the ending physical line for multiline records).
All recoverable row errors are collected; each invalid row contributes its first
validation error. Invalid input produces errors only, never a partial audit.
An unclosed quoted field can consume the remainder of a CSV file, preventing
recovery of subsequent records. JSON mode emits errors as `{"errors": [...]}`;
argument-parser usage errors retain argparse's standard stderr output.

## CSV format

```csv
game_id,date,player,minutes_played,home_away,position
G1,2026-09-01,Alex,20,H,Forward
G1,2026-09-01,Sam,20,H,Defender
G2,2026-09-08,Alex,20,A,Defender
G2,2026-09-08,Sam,20,A,Forward
```

Required columns: `game_id`, `date` (`YYYY-MM-DD`), `player`, `minutes_played`,
and `home_away` (`H` or `A`). Optional columns: `position`, `team`.
UTF-8, with or without a BOM, is supported. Values are trimmed; identifiers remain
case-sensitive. Extra named columns are ignored. Missing/duplicate headers,
wrong field counts, invalid dates, nonnumeric/nonfinite/negative values, values
above 1e12, duplicate `(team, game_id, player)` records, empty identifiers, and
conflicting game dates/venues are rejected. The numeric upper bound protects
squared calculations from overflow and is far beyond realistic sports counts.
Omitted team defaults to `Team`; an explicitly empty team is invalid.

One file represents **one season**. Multiple teams use a `team` column. A player
name identifies a player within a team; use unique stable names or identifiers.
Game IDs must be unique within a team's season. Games sort by date, then game ID
lexicographically for ties; use sortable IDs for same-day games.

## Metrics and policy semantics

For player p with recorded game values x, `mean` is sum(x)/number of recorded
games. The **team mean** is the equally weighted mean of the players' season
means. It does not give extra baseline weight to players with more recorded games.

- `total`, `min`, `max`, and `games_played` use recorded rows, including zero values.
- `variance` is population variance around the player's own mean (divide by N);
  `stddev` is its square root. One recorded game has variance zero.
- `deviation_from_team_mean` is the signed player mean minus team mean.
  `mean_squared_deviation_from_team_mean` averages `(x - team_mean)^2`;
  `rms_deviation_from_team_mean` is its square root. These measure deviation from
  the shared baseline and are distinct from within-player variance.
- `roster_variance` is population variance of player means, and `roster_stddev`
  is its square root. Variance is measured in squared units.
- A bench game is **strictly below** `team_mean * bench_percent / 100`.
  A missing player row breaks a streak; it does not imply zero playing time.
  Explicit zero rows count. Streaks use the full chronological team schedule.
- Home/away totals and per-game means are separate. Imbalance is
  `100 * abs(home_mean - away_mean) / ((home_mean + away_mean) / 2)`.
  Equal zero means give 0%; a missing venue gives null/n/a and no balance violation.
  The metric ranges from 0% to 200% and adjusts for unequal venue game counts.
- `position_time` sums time by the supplied position. Blank or absent position is
  `(unspecified)`. A single row cannot split time among multiple positions.

Default policy (all fields optional in a supplied JSON object):

```json
{
  "bench_percent": 75,
  "bench_games": 3,
  "max_roster_variance": 100,
  "max_home_away_imbalance_percent": 50
}
```

Unknown keys, booleans, nonnumeric values, negative values, and nonfinite numbers
are rejected. `bench_percent` must be 0–100; `bench_games` must be a positive integer.

| Rule | Severity | Trigger | Meaning of `excess` |
| --- | --- | --- | --- |
| `bench_streak` | HIGH | At least `bench_games` consecutive below-threshold games | Threshold minus average time during the streak, in selected units |
| `roster_variance` | HIGH | Roster variance strictly exceeds maximum | Variance minus maximum, in squared units |
| `home_away_imbalance` | MEDIUM | Imbalance strictly exceeds maximum | Imbalance minus maximum, in percentage points |

Each maximal qualifying streak generates one violation containing all its games;
`actual`/`limit` are its length/required length. Other rules use their metric and
configured maximum as `actual`/`limit`. Roster violations have `player: null` and
list all team games. Home/away violations list the player's recorded games.
Violations sort by severity, descending numeric excess, then deterministic
identifiers. Excesses have different units across rules, so ordering within a
severity is a display convention, not a universal harm score.

## Free tier and demo paid mode

The free tier audits one team of up to 15 distinct players, one season per file.
The demo paid mode removes the roster cap and generates a combined cross-team /
league report with separate team baselines, league totals, and ranked violations.
There is no account, payment, subscription, or purchase flow.

Generate an HMAC-SHA256 signed **demo** key locally:

```console
benchwatch demo-license --holder "Example Rec League" --expires 2099-12-31
benchwatch report league.csv --multi-team --license-key "PASTE_KEY_HERE" --format json
benchwatch check league.csv --policy examples/policy.json --multi-team --license-key "PASTE_KEY_HERE"
```

Add a `team` column to `league.csv`; the same game/player identifiers may recur
in different teams. Expiry is inclusive and uses the machine's local date.
The signing secret is intentionally public in the source, and anyone can issue
keys or bypass local checks. **This is a demonstration of product gating, not
real payment processing or production license security.** Commercial payment
integration, secure issuance, and entitlement management are follow-up work.
No payment processing is built into this MVP.

## Limitations

- Trusts the CSV as ground truth: no video, attendance, substitution, injury,
  eligibility, schedule completeness, or coach-intent verification.
- Missing rows are unknown participation. Uneven attendance can affect season
  totals; mean-based fairness cannot establish equal opportunity by itself.
- Does not infer absent players, normalize different game lengths, handle
  overtime rules, or judge whether a league's configured policy is appropriate.
- No automatic season detection or splitting: submit one season per file.
- Positions are one label per player-game; no within-game position timelines.
- Uses ordinary floating-point arithmetic without rounding before comparisons.
  Equality is permitted at policy limits, subject to floating-point precision.
- Batch processing loads the log in memory; this is designed for youth-league
  season files, not streaming or very large datasets.
- No web UI, storage service, roster sync, real payment processing, or network use.
  Installation and GitHub CI may download development tooling.

## Development

```console
python -m pip install -e ".[dev]"
python -m pytest --cov=benchwatch --cov-report=term-missing --cov-fail-under=90
python -m ruff check .
```

Tests cover hand-computed variance and deviation, streak boundaries and missing
rows, fair-season true negatives, policy violations, malformed CSV collection,
license gates, and CLI exit codes. GitHub Actions runs both checks on Ubuntu and
Windows with Python 3.11, 3.12, and 3.13. Runtime code uses only the standard
library. Development tools are optional dependencies. Licensed under MIT.
