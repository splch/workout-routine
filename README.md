# Daily calisthenics

`daily_workout.py` picks the shortest full-body bodyweight routine you can do every day from the exercise and muscle data in this directory.

## Files

- `calisthenics.json`, `calisthenics.md`: 690 bodyweight exercises with level, type, equipment, and the muscles they work, plus the workout formats, named workouts, and programs built from them.
- `muscles.json`, `muscles.md`: 300 muscles of the human body.
- `daily_life.json`, `daily_life.md`: how much an ordinary day without exercise works each of those muscles, and which everyday activities do it. The optimizer discounts the muscles daily life already works.
- `daily_workout.py`: the optimizer. Python 3.10 or newer, no packages.

## Run

    python3 daily_workout.py

It prints the routine in circuit order with progression steps, swaps, what one more move would add, and a daily protocol quoted from the database. The defaults (beginner level, household equipment) give six moves in about four minutes per round: push-up, forearm plank, squat, inverted row, sit-up, superman. Every step already does most of the calves' training, so the calf raise that a blank slate (`--daily-life 0`) would add does not pay its way.

Every workout: 30 s on each move, 10 s to get to the next, two to six rounds, stopping a rep or two short of failure.

- No equipment (`--equipment none`): push-up, forearm plank, squat, prone row, flutter kick, superman.
- Household items (default, with or without a pull-up bar): push-up, forearm plank, squat, inverted row, sit-up, superman.
- Pull-up bar (`--equipment none,bar`): push-up, assisted pull-up, prone row, sit-up, superman, forearm plank, squat.

## Options

    --equipment none            floor only; default none,wall,box,door,low-bar,towel; or all
    --max-level intermediate    allow harder exercises
    --max-n 5                   at most five moves
    --time-budget 240           rounds of at most four minutes
    --exercise-cost 3           charge more per move, for a shorter routine
    --daily-life 0.5            halve the daily-life discount; 0 ignores it
    --require pull-up           force an exercise in; --exclude drops one
    --json plan.json            also write the result as JSON

`python3 daily_workout.py --help` lists the rest.

## How it chooses

The script merges muscles that always appear together into functional units and weights each unit by how often the database works it as a primary mover, less the share of the routine's job an ordinary day already does for it. That share is judged per muscle from how hard and how often daily life loads it (the `MUSCLE_LOAD` table in the script): breathing does 90% of the diaphragm's job, every step 85% of the soleus's and 60% of the shin and foot muscles', chairs and stairs half of the quadriceps', sitting up and coughing 30% of the abdominals', doors and armrests 15% of the chest's and lats'. The activities in `daily_life.json` say which everyday movements are responsible and stand in for any muscle the table leaves out. A routine scores its weighted coverage, plus a small bonus for well-known exercises and movement patterns, minus its difficulty and time. An exact search finds the best routine of each size and stops when no move pays for its 40 seconds.
