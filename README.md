# Daily calisthenics

`daily_workout.py` picks the shortest full-body bodyweight routine you can do every day from the exercise and muscle data in this directory.

## Files

- `calisthenics.json`, `calisthenics.md`: 690 bodyweight exercises with level, type, equipment, and the muscles they work, plus the workout formats, named workouts, and programs built from them.
- `muscles.json`, `muscles.md`: 300 muscles of the human body.
- `daily_workout.py`: the optimizer. Python 3.10 or newer, no packages.

## Run

    python3 daily_workout.py

It prints the routine in circuit order with progression steps, swaps, what one more move would add, and a daily protocol quoted from the database. The defaults (beginner level, household equipment) give seven moves in under five minutes per round: push-up, forearm plank, inverted row, sit-up, superman, calf raise, squat.

## Options

    --equipment none            floor only; default none,wall,box,door,low-bar,towel; or all
    --max-level intermediate    allow harder exercises
    --max-n 5                   at most five moves
    --time-budget 240           rounds of at most four minutes
    --exercise-cost 3           charge more per move, for a shorter routine
    --require pull-up           force an exercise in; --exclude drops one
    --json plan.json            also write the result as JSON

`python3 daily_workout.py --help` lists the rest.

## How it chooses

The script merges muscles that always appear together into functional units and weights each unit by how often the database works it as a primary mover. A routine scores its weighted coverage, plus a small bonus for well-known exercises and movement patterns, minus its difficulty and time. An exact search finds the best routine of each size and stops when no move pays for its 40 seconds.
