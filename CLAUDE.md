# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

One script, `daily_workout.py`, that picks the shortest daily full-body calisthenics routine from three hand-built JSON databases in this directory. Python 3.10 or newer (it relies on `int.bit_count()`), standard library only. There is no build step, test suite or linter: the check is to run the script and read the report.

## Commands

    python3 daily_workout.py                         # defaults: beginner level, household equipment
    python3 daily_workout.py --equipment none        # floor only
    python3 daily_workout.py --equipment none,bar    # pull-up bar
    python3 daily_workout.py --units parts           # rebuild muscle profiles from calisthenics.md; exits if it disagrees with the JSON
    python3 daily_workout.py --json plan.json        # also write the result as JSON
    python3 daily_workout.py --help                  # every option, with defaults

The three equipment runs above are the routines quoted in the README. If data or scoring changes, re-run them and update the README. `--dir` points the script at another copy of the data files. A default run takes well under a second; the last line reports the node count, `(time limit hit: some sizes are heuristic)` means branch-and-bound exceeded `SEARCH_SECONDS` for a size and that row is not proven optimal, and `(stopped at --max-n: ...)` means the size cap, not the costs, ended the routine.

## Data model

Three JSON files joined by muscle `id` (a slug of the muscle's name), each with a hand-written Markdown twin for people:

- `muscles.json`: 300 muscles with `region`, `group`, `paired`, `variable`, `parts`, `aka`. The order regions first appear here is the order units are listed in the report.
- `daily_life.json`: the same 300 entries in the same order, each with a `tier` (`nonstop`, `daily`, `occasional`, `barely`) and `activities`, keys of `meta.activities` listed most important first.
- `calisthenics.json`: 690 exercises with `category`, `group`, `level`, `type`, `equipment` (a list of alternatives, `+` joining items needed together, keys of `meta.equipment`), `primary_muscles`, `secondary_muscles`, `step` (present only in groups flagged `progression: true` under `categories`), `aka`, `note`. Also `modifiers`, `formats`, `named_workouts` and `programs`, which the optimizer mines for its popularity and pattern priors and quotes in the report.

Conventions that are easy to break:

- JSON muscle lists are fully expanded. "Abdominals" in the Markdown is four ids in the JSON, "Hamstrings" three, "Forearm flexors" five, "Deltoid (anterior)" is `deltoid`. `MD_NAME_IDS` in the script maps every Markdown muscle name to ids; a new name in `calisthenics.md` must be added there or `--units parts` exits.
- `markdown_profiles()` parses `calisthenics.md` with regexes. Group headers must read `Works: A, B. Also: C. Equipment: X.` and entries must read `- **Name** *(level · tags · equipment)* — note. Also called: .... Works: .... Also: ....`, with the ` — ` separator and the trailing periods intact. An entry that omits Works or Also inherits its group's list, so editing a group line changes every entry that does not restate it. Entry names and `###` group headings must equal `name` and `group` in the JSON. The consistency check compares only the Works list against `primary_muscles`; secondaries are not checked.
- `print_report()` looks up formats and programs by name (`Autoregulation`, `Grease the Groove`, `Superset`, `7-Minute Workout`, `Minimalist Routine`, `Recommended Routine`, `RCAF 5BX`), quotes the first six modifiers in file order, and extracts sentences from three program summaries with regexes (`\d+[–-]\d+ rounds.*?failure`, `Tempo[^.]*\.`, `Move up[^.]*\.`). Renaming or rewording those entries breaks the report.
- Load-time checks: every key of `daily_life.json` `meta.activities` needs an entry in `ACTIVITY_LOAD`; every id in `MUSCLE_LOAD` must exist in `daily_life.json`; every muscle an exercise names must exist in `daily_life.json`; equipment, type and category names are validated against `meta.equipment`, `ALL_TYPES` and `categories`.
- `meta.counts` in each JSON, the count tables at the top of each Markdown file, and the totals in the README and the script docstring are typed by hand, not computed; update them when entries change.
- Wording matters to the scoring. `aka` entries feed the popularity prior (program, workout, format and modifier texts are regex-matched against name and aliases, case-insensitive, plural and hyphen tolerant; a match inside a longer one counts only for the longer, and names starting "The " take no plural) and the tie-break. Any name, note or alias matching `EACH_SIDE_RE` (one-leg, each side, lunge, split squat, step-up, pistol, side plank, clamshell, ...) doubles the exercise's work time, unless `BOTH_SIDES_RE` also matches (an offset grip, a free leg lifted while the arms or trunk work, a move that crosses to both sides each rep). `PATTERN_KEYWORDS` are matched in program names and summaries, but not inside a longer exercise name or keyword, nor in `NOT_PATTERN_RE` phrases ("in a row", "grips").

## How the optimizer works

`main()` runs these stages in order; each feeds the next.

1. **Units** (`build_units`). Muscles with an identical set of (exercise, role) memberships across the whole database merge into one unit, named from `UNIT_NAMES` when the member set matches and otherwise from the first member. Units are bit positions: each exercise becomes two bitmasks, `P` (primary units) and `A` (primary or secondary). `--units parts` splits deltoid, trapezius and pectoralis major into the parts the Markdown names.
2. **Weights** (`unit_weights`, `daily_loads`, `muscle_load`). Weight is the square root of the number of exercises using the unit as primary (`--weighting`), times `1 - daily_life × load`, normalized to mean 1. The load is the hand-tuned `MUSCLE_LOAD` table when the muscle is listed there; otherwise the largest `ACTIVITY_LOAD` of its activities, halved per position down the list (`ACTIVITY_DECAY`) and clamped by `TIER_LOAD`. A unit takes the mean over its members. This discount is why calves, feet, diaphragm and pelvic floor carry almost no weight by default and why the region line can show `Pelvis & perineum 0.0/0.0`.
3. **Priors** (`count_mentions`, `pattern_shares`, `pattern_of`). Popularity is ln(1 + mentions). A movement pattern's prior is the share of programs naming any exercise of that pattern or one of its `PATTERN_KEYWORDS`. Patterns come from `CATEGORY_PATTERN`, with Core split by group into anti-extension / flexion / rotation and Posterior chain into hinge / abduction.
4. **Candidates** (`build_candidates`, `est_seconds`). Filter by level, type, category (`DEFAULT_EXCLUDE_CATEGORIES` drops handstands and arm balances, cardio, locomotion, tumbling, mobility and plyometrics) and equipment; merge exercises sharing `(P, A, level, pattern)` (and seconds, under `--time-budget`), keeping the best modular score as representative and the rest as `alternatives` (the report's swaps); drop a profile only when another of its pattern works the same units (any superset at `--min-gain 0`, since a bigger profile can push another member below the minimum), is no slower and scores at least as well. Time per block is 30 s work + 10 s transition, work doubled for each-side moves, plus 15 s when every equipment option includes an item of `RIGGED` (rings, suspension).
5. **Scoring** (`Scorer`). Value = weighted coverage (a unit counts fully as primary and at `--secondary-weight` as secondary only) + pattern prior once per pattern present + per-exercise modular term (popularity − level cost − time cost). Coverage sums come from 16-bit lookup tables over the bitmasks. `can_add()` enforces one exercise per pattern, the optional `--time-budget`, and that every exercise keeps a unique muscle gain of at least `--min-gain`. Exact ties go to `tie_bonus()`: easier, then equipment-free, then more aliases, then more headroom in its category.
6. **Search** (`solve_size`, `branch_and_bound`). For each size up to `--max-n`: greedy, then local search, then exact branch-and-bound with a per-size time limit, warm-started from the previous size. The loop stops when the best routine stops growing. `--require` exercises form a fixed base, and their profiles are removed from the pool.
7. **Report** (`circuit_order`, `progression`, `print_report`, `write_json`). Circuit order minimizes shared primary units between neighbours, the last and first included since rounds repeat, by brute force over permutations (database order above `CIRCUIT_MAX` = 9 exercises). "What one more move would add" lists only moves `can_add()` accepts. Easier and harder steps come from `step` within the group, with harder steps continuing into later progression groups of the same category. The protocol section is quoted from the database's formats and programs.
