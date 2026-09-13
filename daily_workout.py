#!/usr/bin/env python3
"""
daily_workout.py - the shortest, simplest full-body calisthenics routine you
can do every day, chosen from calisthenics.json, muscles.json and
daily_life.json.

How it works
------------
1. Functional units.  Muscles that always appear together across the whole
   exercise database (the three hamstrings, the four erector spinae, the six
   deep hip rotators, ...) are merged into one unit: the 99 muscles that any
   exercise trains become 51 units, named as in calisthenics.md.
   --units parts instead reads the markdown's Works/Also lists, which split
   the deltoid, trapezius and pectoralis major into their parts (56 units).
2. Unit weights.  A unit is weighted by the square root of the number of
   exercises in the database that work it as a primary muscle
   (--weighting freq).  Counting every unit as 1 lets anatomy's naming
   density decide: the foot has more named muscles than the chest.
   The weight is then discounted by the share of the routine's job an
   ordinary day already does for the unit (MUSCLE_LOAD; --daily-life
   scales it, 0 ignores it): how hard and how often daily life loads each
   muscle, judged from gait and daily-activity studies.  Breathing does
   90% of the diaphragm's job, every step 85% of the soleus's, chairs and
   stairs half of the quadriceps', doors and armrests 15% of the chest's.
   A unit takes the mean of its members and its weight is multiplied by
   1 - --daily-life x load.  A muscle the table leaves out falls back to
   its everyday activities in daily_life.json: each activity has a load
   (breathing 1; standing, walking and holding up the head 0.75; rising
   and stairs 0.5; light work 0.25; reflexes 0.1), halved for each place
   it sits below the top of the muscle's list, and the muscle takes the
   largest, bounded by its tier.  The weights are then rescaled so the
   average unit weighs 1: the "weighted unit" that every cost, --min-gain
   and the report count in.
3. Candidates.  Exercises are filtered by level (beginner by default), type
   (dynamic, isometric), category (handstands and arm balances, locomotion,
   tumbling, mobility, cardio and jumps are out by default) and the
   equipment you have.  Identical muscle profiles are merged, keeping the
   best-known exercise (under --time-budget only equally long ones merge); a
   profile is dropped only when another exercise of the same movement
   pattern works the same muscles (any superset with --min-gain 0), is no
   slower and scores at least as well on popularity, level and time.
4. Score of a routine =
       weighted muscle coverage   (primary 1, secondary --secondary-weight)
     + pattern prior              (once per movement pattern present:
                                   --pattern-bonus x share of the database's
                                   programs that use that pattern)
     + popularity                 (per exercise: --popularity x ln(1 + number
                                   of the database's programs, named workouts,
                                   format and modifier examples naming it; a
                                   variant's name counts for the variant only)
     - level cost                 (--level-cost per level above beginner)
     - time cost                  (--exercise-cost per 40 s block: 30 s work
                                   + 10 s transition, work doubled for moves
                                   done one side at a time, +15 s to re-rig
                                   rings or suspension straps)
   with at most one exercise per movement pattern (--repeat-patterns lifts
   this) and an optional --time-budget per round.  Every exercise must keep
   a unique muscle contribution of at least --min-gain, so nothing is ever
   added as filler.
5. Search.  For each size an exact branch-and-bound finds the best routine
   of at most that many exercises; the routine stops growing when no
   exercise's coverage and priors pay for its time.  Exact ties go to the
   easier exercise, then the one needing no equipment, then the
   better-known one, then the one with more headroom (harder exercises) in
   its category.
6. Output.  The routine is printed in circuit order (neighbours, the last
   and the first included, share as few primary muscles as possible), with
   progression steps chained across the category, same-muscle swaps, what
   one more move would add, and a daily protocol quoted from the database's
   formats and programs.

Movement patterns come from the database's categories, with Core split into
anti-extension / flexion / rotation and Posterior chain into hinge /
abduction by group.  The 30 s + 10 s timing is the 7-Minute Workout's, and
so is setting out a chair or a wall once rather than every round.  Giving
each side of a one-sided move its own 30 s (the 7-Minute Workout gives its
side plank a single 30 s station) and the 15 s to re-rig straps are heuristics.

Only the standard library is used.

Examples
--------
    python3 daily_workout.py                       # household equipment
    python3 daily_workout.py --equipment none      # floor only
    python3 daily_workout.py --equipment none,wall,box,door,low-bar,towel,sliders,stick,bar
    python3 daily_workout.py --max-n 4             # at most four moves
    python3 daily_workout.py --time-budget 240     # rounds of 4 minutes
    python3 daily_workout.py --exercise-cost 0     # as many as add coverage (about half a minute)
    python3 daily_workout.py --daily-life 0        # ignore daily life
    python3 daily_workout.py --equipment none,bar --max-level intermediate --require pull-up
    python3 daily_workout.py --json plan.json
"""
from __future__ import annotations

import argparse
import heapq
import itertools
import json
import math
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
LEVELS = ["beginner", "intermediate", "advanced", "elite"]
ALL_TYPES = ["dynamic", "isometric", "plyometric", "conditioning", "locomotion", "mobility", "skill"]
DEFAULT_EQUIPMENT = "none,wall,box,door,low-bar,towel,sliders,stick"
DEFAULT_TYPES = "dynamic,isometric"
# Handstands and arm balances, locomotion, tumbling, mobility, cardio and jumps are not daily strength work.
DEFAULT_EXCLUDE_CATEGORIES = [
    "Handstands, inversions & arm balances",
    "Locomotion & animal movement",
    "Tumbling & acrobatics",
    "Mobility & flexibility",
    "Conditioning & cardio",
    "Plyometrics & jumps",
]
EPS = 1e-9
WORK, TRANSITION, SETUP = 30.0, 10.0, 15.0
BLOCK = WORK + TRANSITION   # seconds per exercise block: 30 s work + 10 s transition (7-Minute Workout)
# Equipment whose straps are re-rigged for each exercise and so costs SETUP seconds a block.
# Everything else is set out once before the first round, as the 7-Minute Workout's chair and
# wall are, and is reached within the transition.
RIGGED = {"rings", "suspension"}
SEARCH_SECONDS = 30.0   # per routine size; the search reports "heuristic" if it runs out
CIRCUIT_MAX = 9         # longer routines keep database order: 9! orders already take about half a second

# Names for multi-muscle units, in the vocabulary of calisthenics.md.
UNIT_NAMES = {
    frozenset({"external-oblique", "internal-oblique"}): "Obliques",
    frozenset({"iliocostalis-lumborum", "iliocostalis-thoracis", "longissimus-thoracis",
               "spinalis-thoracis"}): "Erector spinae",
    frozenset({"splenius-capitis", "splenius-cervicis", "semispinalis-capitis",
               "semispinalis-cervicis"}): "Neck extensors (splenius, semispinalis)",
    frozenset({"rhomboid-major", "rhomboid-minor"}): "Rhomboids",
    frozenset({"abductor-digiti-minimi-foot", "dorsal-interossei-foot"}): "Toe spreaders (foot)",
    frozenset({"flexor-digitorum-brevis", "quadratus-plantae", "lumbricals-foot",
               "flexor-hallucis-brevis"}): "Intrinsic foot muscles",
    frozenset({"abductor-hallucis", "flexor-digitorum-brevis", "quadratus-plantae", "lumbricals-foot",
               "flexor-hallucis-brevis"}): "Intrinsic foot muscles",
    frozenset({"gluteus-medius", "gluteus-minimus"}): "Gluteus medius & minimus",
    frozenset({"piriformis", "superior-gemellus", "obturator-internus", "inferior-gemellus",
               "quadratus-femoris", "obturator-externus"}): "Deep hip rotators",
    frozenset({"psoas-major", "iliacus"}): "Iliopsoas",
    frozenset({"extensor-digitorum-longus", "extensor-hallucis-longus"}): "Long toe extensors",
    frozenset({"fibularis-longus", "fibularis-brevis"}): "Fibularis longus & brevis",
    frozenset({"flexor-hallucis-longus", "flexor-digitorum-longus"}): "Long toe flexors",
    frozenset({"vastus-lateralis", "vastus-intermedius", "vastus-medialis"}): "Vasti (quadriceps)",
    frozenset({"adductor-longus", "adductor-brevis", "adductor-magnus", "pectineus",
               "gracilis"}): "Adductors",
    frozenset({"biceps-femoris", "semitendinosus", "semimembranosus"}): "Hamstrings",
    frozenset({"longus-colli", "longus-capitis"}): "Deep neck flexors (longus colli & capitis)",
    frozenset({"anterior-scalene", "middle-scalene", "posterior-scalene"}): "Scalenes",
    frozenset({"pubococcygeus", "puborectalis", "iliococcygeus", "coccygeus"}): "Pelvic floor",
    frozenset({"flexor-digitorum-superficialis", "flexor-digitorum-profundus",
               "flexor-carpi-radialis", "flexor-carpi-ulnaris",
               "flexor-pollicis-longus"}): "Forearm flexors",
    frozenset({"pronator-teres", "supinator"}): "Pronator teres & supinator",
    frozenset({"extensor-carpi-radialis-longus", "extensor-carpi-radialis-brevis",
               "extensor-carpi-ulnaris", "extensor-digitorum"}): "Forearm extensors",
    frozenset({"lumbricals-hand", "dorsal-interossei-hand", "palmar-interossei"}): "Intrinsic hand muscles",
    frozenset({"infraspinatus", "teres-minor"}): "Infraspinatus & teres minor",
    frozenset({"supraspinatus", "subscapularis"}): "Supraspinatus & subscapularis",
    frozenset({"supraspinatus", "infraspinatus", "teres-minor", "subscapularis"}): "Rotator cuff",
}

# Movement patterns, from the database's categories and groups.
CORE_ANTIEXT = {"Planks & anti-extension", "Hollow body & dead bugs", "Deep core & breathing"}
CORE_FLEX = {"Crunches & sit-ups", "Leg raises & hanging core", "Compression & L-sit"}
HINGE = {"Hinges & back extensions", "Glute bridges & hip thrusts", "Hamstring curls"}
CATEGORY_PATTERN = {
    "Push-ups": "horizontal push",
    "Overhead pressing & handstand push-ups": "vertical push",
    "Handstands, inversions & arm balances": "vertical push",
    "Dips & support": "dip / support",
    "Pull-ups & chin-ups": "vertical pull",
    "Muscle-ups & bar transitions": "vertical pull",
    "Rows": "horizontal pull",
    "Arms, scapula & shoulder health": "scapula / cuff",
    "Levers, planche & flags": "straight-arm static",
    "Legs: squats, lunges & single-leg": "squat / knee",
    "Calves, shins & feet": "calves / feet / balance",
    "Neck": "neck",
    "Grip, wrists & forearms": "grip / forearm",
    "Pilates mat (classical order)": "core: flexion",
}
PULL_PATTERNS = {"horizontal pull", "vertical pull"}
# Generic words the programs use for whole patterns (from the database's group names).  A word inside a
# longer exercise name or word ("row" in "prone row", "plank" in "side plank") does not count.
PATTERN_KEYWORDS = {
    "horizontal push": ["push-up"], "horizontal pull": ["row"], "vertical pull": ["pull-up", "chin-up"],
    "vertical push": ["handstand push-up", "pike push-up"], "dip / support": ["dip"],
    "squat / knee": ["squat", "lunge", "step-up"], "hip hinge": ["hinge", "bridge", "back extension", "hip thrust"],
    "core: flexion": ["sit-up", "crunch", "leg raise", "l-sit"], "core: anti-extension": ["plank", "hollow", "anti-extension"],
    "core: rotation / lateral": ["side plank", "side bridge", "twist", "anti-rotation"], "neck": ["neck"],
    "calves / feet / balance": ["calf raise", "calves"], "grip / forearm": ["grip", "hang"],
}
# Phrases that hold a generic word without its pattern: "100 push-ups in a row", "three grips" (hand positions).
NOT_PATTERN_RE = re.compile(r"\bin a row\b|\bgrips\b", re.I)

# Moves that load one side's prime movers at a time take twice the work time, so each side
# gets its own 30 s: one-sided moves (single-leg, one-arm, side-lying, side planks, flags,
# uneven and archer loading) and moves whose working leg alternates (lunges, step-ups).
EACH_SIDE_RE = re.compile(
    r"\b(?:(?:one|single)[- ](?:leg|arm|hand|foot)|each side|switch sides|per side|other side|side-lying"
    r"|side plank|on one|lunge|split squat|step-up|step-down|pistol|shrimp|archer|copenhagen|clamshell"
    r"|fire hydrant|hip airplane|donkey kick|side crunch|cossack|seated knee lift|shoulder bridge"
    r"|human flag|clutch flag|vertical flag|couch stretch|pigeon|leg swing|(?:shoulder|hip) cars|knee-to-wall)",
    re.I)
# ...except where every rep still works both sides' prime movers: an offset grip, a free leg
# lifted while the arms or trunk work, or a move that crosses to both sides each rep.
BOTH_SIDES_RE = re.compile(
    r"\b(?:switch sides each set|typewriter|around-the-world pull-up|single-leg (?:push-up|plank|stretch)"
    r"|one-leg (?:planche|front lever|l-sit)|plank up-down|shoulder tap|plank hip twist|t push-up|towel wring"
    r"|control balance|kick-up)", re.I)

# Share of the routine's job an ordinary day already does for each muscle, 0-1: how hard
# (fraction of maximum) and how often daily life loads it, judged from gait and
# daily-activity studies for a mostly seated adult with a few thousand steps a day.
MUSCLE_LOAD = [
    # Living itself: 20,000 breaths a day, each at a tenth to a fifth of the diaphragm's maximum
    # force, and continence around the clock.
    (0.9, ["diaphragm"]),
    (0.85, ["pubococcygeus", "puborectalis", "iliococcygeus", "coccygeus",
            # Every step: push-off loads the plantar flexors at about 40% of their maximum,
            # thousands of times a day, and the soleus holds you up whenever you stand.
            "soleus"]),
    (0.75, ["gastrocnemius"]),
    # Every step, lighter: single-leg stance for the hip abductors, heel strike and swing for
    # the shin, arch support for the foot, at a tenth to a quarter of maximum.
    (0.65, ["gluteus-medius", "gluteus-minimus"]),
    (0.6, ["tensor-fasciae-latae", "abductor-hallucis", "flexor-digitorum-brevis", "flexor-hallucis-brevis",
           "lumbricals-foot", "quadratus-plantae", "abductor-digiti-minimi-foot", "dorsal-interossei-foot",
           "tibialis-anterior", "extensor-digitorum-longus", "extensor-hallucis-longus", "fibularis-longus",
           "fibularis-brevis", "flexor-digitorum-longus", "flexor-hallucis-longus", "tibialis-posterior"]),
    # Chairs and stairs: about a hundred efforts a day (some 60 sit-to-stands and a few flights)
    # at a third to a half of maximum (a sit-to-stand loads the knee about like a bodyweight
    # squat).  Posture: the neck muscles hold up the head all day, the scalenes also lift the
    # ribs, lightly, with every breath, and the deep hip rotators steady every step.
    (0.5, ["vastus-lateralis", "vastus-intermedius", "vastus-medialis",
           "semispinalis-capitis", "semispinalis-cervicis", "splenius-capitis", "splenius-cervicis",
           "anterior-scalene", "middle-scalene", "posterior-scalene",
           "piriformis", "superior-gemellus", "obturator-internus", "inferior-gemellus",
           "quadratus-femoris", "obturator-externus"]),
    (0.45, ["gluteus-maximus", "iliocostalis-lumborum", "iliocostalis-thoracis", "longissimus-thoracis",
            "spinalis-thoracis", "multifidus"]),
    # Walking's helpers and the odd bend: swing and stance at 5 to 15% of maximum, bends and lifts
    # at more; grip for the forearm and hand.
    (0.4, ["rectus-femoris", "psoas-major", "iliacus", "quadratus-lumborum",
           "flexor-digitorum-superficialis", "flexor-digitorum-profundus", "flexor-carpi-radialis",
           "flexor-carpi-ulnaris", "flexor-pollicis-longus"]),
    (0.35, ["biceps-femoris", "semitendinosus", "semimembranosus",
            "adductor-longus", "adductor-brevis", "adductor-magnus", "pectineus", "gracilis",
            "transversus-abdominis", "extensor-carpi-radialis-longus", "extensor-carpi-radialis-brevis",
            "extensor-carpi-ulnaris", "extensor-digitorum", "lumbricals-hand", "dorsal-interossei-hand",
            "palmar-interossei"]),
    # Sitting up, coughing and laughing: a few dozen brief hard contractions a day for the
    # abdominals; light carrying, reaching and head turning for the neck, shoulder and forearm.
    (0.3, ["rectus-abdominis", "external-oblique", "internal-oblique", "sternocleidomastoid",
           "trapezius", "levator-scapulae", "brachioradialis", "pronator-teres", "supinator",
           "supraspinatus", "infraspinatus", "teres-minor", "subscapularis"]),
    # Reaching and carrying: a hundred reaches and a few minutes of bags a day at a fifth of maximum.
    # The deep neck flexors only steady the head, which the extensors hold up.
    (0.25, ["deltoid", "serratus-anterior", "biceps-brachii", "brachialis", "longus-colli", "longus-capitis"]),
    (0.2, ["triceps-brachii", "anconeus", "pectoralis-minor"]),
    # Doors and armrests: seconds a day at a tenth of maximum.
    (0.15, ["pectoralis-major", "latissimus-dorsi", "teres-major", "rhomboid-major", "rhomboid-minor"]),
]
MUSCLE_LOAD = {m: v for v, ms in MUSCLE_LOAD for m in ms}

# Fallback for a muscle not in MUSCLE_LOAD: how much of its capacity an ordinary day asks
# of the muscles an everyday activity uses (the keys of meta.activities in daily_life.json),
# 0-1.  After daily_life.md's "Used is not trained": the nonstop functions are worked by
# living itself; sitting, standing, walking and holding up the head give steady work for
# hours or thousands of steps; rising from a chair and stairs are the heaviest everyday
# loads, some 60 sit-to-stands and a few flights a day; the rest is light or brief.
ACTIVITY_LOAD = {
    "heartbeat": 1.0, "breathing": 1.0, "looking": 1.0, "digestion": 1.0, "continence": 1.0,
    "upright": 0.75, "walking": 0.75, "head": 0.75,
    "rising": 0.5, "stairs": 0.5,
    "eating": 0.25, "talking": 0.25, "expression": 0.25, "bending": 0.25, "carrying": 0.25,
    "reaching": 0.25, "pushing": 0.25, "gripping": 0.25, "fine": 0.25, "forced": 0.25,
    "reflexes": 0.1, "sexual": 0.1,
}
ACTIVITY_DECAY = 0.5   # an activity's load halves for each place it sits below the top of a muscle's list
TIER_LOAD = {"nonstop": (1.0, 1.0), "daily": (0.0, 1.0), "occasional": (0.0, 0.1), "barely": (0.0, 0.0)}  # floor, cap


# --------------------------------------------------------------------------- data


def load_data(data_dir: str):
    try:
        with open(os.path.join(data_dir, "calisthenics.json"), encoding="utf-8") as f:
            cal = json.load(f)
        with open(os.path.join(data_dir, "muscles.json"), encoding="utf-8") as f:
            mus = json.load(f)
    except OSError as exc:
        sys.exit("cannot read the data files: %s" % exc)
    return cal, mus


def pattern_of(e: dict) -> str:
    c, g = e["category"], e["group"]
    if c == "Core":
        return ("core: anti-extension" if g in CORE_ANTIEXT else
                "core: flexion" if g in CORE_FLEX else "core: rotation / lateral")
    if c == "Posterior chain & hips":
        return "hip hinge" if g in HINGE else "hip abduction / adduction"
    return CATEGORY_PATTERN.get(c, c.lower())


def est_seconds(e: dict) -> float:
    """One block of the exercise inside a circuit."""
    text = " ".join([e["name"], e.get("note", "")] + e.get("aka", []))
    sides = 2 if EACH_SIDE_RE.search(text) and not BOTH_SIDES_RE.search(text) else 1
    rigged = all(any(p in RIGGED for p in alt.split("+")) for alt in e["equipment"])
    return WORK * sides + TRANSITION + (SETUP if rigged else 0.0)


def headroom(e: dict, exercises: list[dict]) -> int:
    """Harder exercises in the same category: a higher level, or a later step in the same group."""
    lvl = LEVELS.index(e["level"])
    return sum(1 for x in exercises if x["category"] == e["category"] and x is not e and
               (LEVELS.index(x["level"]) > lvl or
                (x["group"] == e["group"] and "step" in x and "step" in e and x["step"] > e["step"])))


# ----------------------------------------------------------------- muscle units


def json_profiles(exercises):
    return {e["id"]: (list(e["primary_muscles"]), list(e["secondary_muscles"])) for e in exercises}


MD_NAME_IDS = {
    "Abdominals": ["rectus-abdominis", "external-oblique", "internal-oblique", "transversus-abdominis"],
    "Obliques": ["external-oblique", "internal-oblique"], "Rectus abdominis": ["rectus-abdominis"],
    "Transversus abdominis": ["transversus-abdominis"], "Gluteus maximus": ["gluteus-maximus"],
    "Hip flexors (iliopsoas, rectus femoris)": ["psoas-major", "iliacus", "rectus-femoris"],
    "Iliopsoas": ["psoas-major", "iliacus"], "Deltoid (anterior)": ["deltoid:anterior"],
    "Deltoid (lateral)": ["deltoid:lateral"], "Deltoid (posterior)": ["deltoid:posterior"],
    "Deltoid": ["deltoid:anterior", "deltoid:lateral", "deltoid:posterior"],
    "Triceps brachii": ["triceps-brachii"], "Serratus anterior": ["serratus-anterior"],
    "Latissimus dorsi": ["latissimus-dorsi"], "Hamstrings": ["biceps-femoris", "semitendinosus", "semimembranosus"],
    "Erector spinae": ["iliocostalis-lumborum", "iliocostalis-thoracis", "longissimus-thoracis", "spinalis-thoracis"],
    "Forearm flexors": ["flexor-digitorum-superficialis", "flexor-digitorum-profundus", "flexor-carpi-radialis",
                        "flexor-carpi-ulnaris", "flexor-pollicis-longus"],
    "Pectoralis major": ["pectoralis-major"], "Pectoralis major (clavicular head)": ["pectoralis-major:clavicular"],
    "Quadriceps": ["rectus-femoris", "vastus-lateralis", "vastus-intermedius", "vastus-medialis"],
    "Calves (gastrocnemius, soleus)": ["gastrocnemius", "soleus"], "Gastrocnemius": ["gastrocnemius"], "Soleus": ["soleus"],
    "Biceps brachii": ["biceps-brachii"],
    "Adductors": ["adductor-longus", "adductor-brevis", "adductor-magnus", "pectineus", "gracilis"],
    "Trapezius (lower)": ["trapezius:lower"], "Trapezius (middle)": ["trapezius:middle"], "Trapezius (upper)": ["trapezius:upper"],
    "Gluteus medius & minimus": ["gluteus-medius", "gluteus-minimus"], "Rhomboids": ["rhomboid-major", "rhomboid-minor"],
    "Teres major": ["teres-major"],
    "Neck extensors (splenius, semispinalis)": ["splenius-capitis", "splenius-cervicis", "semispinalis-capitis", "semispinalis-cervicis"],
    "Quadratus lumborum": ["quadratus-lumborum"],
    "Intrinsic hand muscles": ["lumbricals-hand", "dorsal-interossei-hand", "palmar-interossei"],
    "Brachialis": ["brachialis"], "Infraspinatus & teres minor": ["infraspinatus", "teres-minor"],
    "Deep hip rotators": ["piriformis", "superior-gemellus", "obturator-internus", "inferior-gemellus",
                          "quadratus-femoris", "obturator-externus"],
    "Intrinsic foot muscles": ["abductor-hallucis", "flexor-digitorum-brevis", "quadratus-plantae", "lumbricals-foot",
                               "flexor-hallucis-brevis"],
    "Multifidus": ["multifidus"], "Fibularis longus & brevis": ["fibularis-longus", "fibularis-brevis"],
    "Tibialis anterior": ["tibialis-anterior"],
    "Neck flexors (SCM, longus colli & capitis)": ["sternocleidomastoid", "longus-colli", "longus-capitis"],
    "Deep neck flexors (longus colli & capitis)": ["longus-colli", "longus-capitis"],
    "Sternocleidomastoid": ["sternocleidomastoid"],
    "Forearm extensors": ["extensor-carpi-radialis-longus", "extensor-carpi-radialis-brevis", "extensor-carpi-ulnaris",
                          "extensor-digitorum"],
    "Brachioradialis": ["brachioradialis"], "Long toe extensors": ["extensor-digitorum-longus", "extensor-hallucis-longus"],
    "Scalenes": ["anterior-scalene", "middle-scalene", "posterior-scalene"], "Tibialis posterior": ["tibialis-posterior"],
    "Pectoralis minor": ["pectoralis-minor"], "Diaphragm": ["diaphragm"],
    "Long toe flexors": ["flexor-hallucis-longus", "flexor-digitorum-longus"], "Levator scapulae": ["levator-scapulae"],
    "Anconeus": ["anconeus"], "Pelvic floor (levator ani, coccygeus)": ["pubococcygeus", "puborectalis", "iliococcygeus", "coccygeus"],
    "Vastus medialis": ["vastus-medialis"], "Tensor fasciae latae": ["tensor-fasciae-latae"], "Rectus femoris": ["rectus-femoris"],
    "Abductor hallucis": ["abductor-hallucis"], "Abductor digiti minimi (foot)": ["abductor-digiti-minimi-foot"],
    "Dorsal interossei (foot)": ["dorsal-interossei-foot"], "Pronator teres": ["pronator-teres"], "Supinator": ["supinator"],
    "Rotator cuff": ["supraspinatus", "infraspinatus", "teres-minor", "subscapularis"],
}


def markdown_profiles(md_path: str, exercises: list[dict]):
    """Parts-aware primary/secondary ids from calisthenics.md (deltoid, trapezius and
    pectoralis major split into the parts the markdown names)."""
    try:
        with open(md_path, encoding="utf-8") as f:
            md = f.read()
    except OSError as exc:
        sys.exit("--units parts needs calisthenics.md: %s" % exc)

    def split_names(s):
        out, depth, cur = [], 0, ""
        for ch in s:
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            out.append(cur.strip())
        return out

    group_works, entries, cur = {}, {}, None
    for line in md.split("\n"):
        m = re.match(r"^### (.+)$", line)
        if m:
            cur = m.group(1).strip()
            continue
        if cur and line.startswith("Works:"):
            w = re.search(r"Works: (.+?)\.(?: Also: (.+?)\.)? Equipment:", line)
            if w:
                group_works[cur] = (split_names(w.group(1)), split_names(w.group(2)) if w.group(2) else [])
            continue
        m = re.match(r"^- \*\*(.+?)\*\* \*\((.+?)\)\*(?: — (.+))?$", line)
        if m and cur in group_works:
            rest = m.group(3) or ""
            works = also = None
            w = re.search(r"Works: (.+?)\.(?: Also: (.+?)\.)?$", rest)
            if w:
                works, also = split_names(w.group(1)), split_names(w.group(2)) if w.group(2) else None
            else:
                a = re.search(r"(?:^|\. )Also: (.+?)\.$", rest)
                if a:
                    also = split_names(a.group(1))
            entries[(cur, m.group(1))] = (works, also)

    profiles = {}
    for e in exercises:
        key = (e["group"], e["name"])
        if key not in entries or e["group"] not in group_works:
            sys.exit("--units parts: %s not found in calisthenics.md" % e["id"])
        works, also = entries[key]
        gw, ga = group_works[e["group"]]
        wn = works if works is not None else gw
        an = also if also is not None else ga
        try:
            prim = [i for n in wn for i in MD_NAME_IDS[n]]
            sec = [i for n in an for i in MD_NAME_IDS[n] if i not in prim]
        except KeyError as exc:
            sys.exit("--units parts: unknown muscle name %s in calisthenics.md" % exc)
        base = lambda ids: {i.split(":")[0] for i in ids}
        if base(prim) != set(e["primary_muscles"]):
            sys.exit("--units parts: markdown and JSON disagree on %s" % e["id"])
        profiles[e["id"]] = (prim, sec)
    return profiles


def build_units(exercises, muscles, profiles):
    """Merge muscle ids with identical (exercise, role) membership."""
    membership = defaultdict(set)
    for e in exercises:
        prim, sec = profiles[e["id"]]
        for m in prim:
            membership[m].add((e["id"], "P"))
        for m in sec:
            membership[m].add((e["id"], "S"))
    classes = defaultdict(list)
    for m, s in membership.items():
        classes[frozenset(s)].append(m)

    info = {m["id"]: m for m in muscles}
    region_rank = {}
    for m in muscles:
        region_rank.setdefault(m["region"], len(region_rank))

    def muscle_name(mid):
        base, _, part = mid.partition(":")
        return info[base]["name"] + (" (%s)" % part if part else "")

    units = []
    for members in classes.values():
        members.sort()
        first = info[members[0].split(":")[0]]
        key = frozenset(members)
        if key in UNIT_NAMES:
            name = UNIT_NAMES[key]
        elif len(members) == 1:
            name = muscle_name(members[0])
        else:
            name = ", ".join(muscle_name(m) for m in members[:3]) + (" +%d" % (len(members) - 3) if len(members) > 3 else "")
        units.append({"name": name, "members": members, "region": first["region"], "group": first["group"]})
    units.sort(key=lambda u: (region_rank[u["region"]], u["group"], u["name"]))
    return units


def unit_weights(units, exercises, profiles, mode, loads=None, strength=0.0):
    """Weights with mean 1; loads (0-1 per unit, from daily_loads) times strength come off first."""
    unit_of = {m: i for i, u in enumerate(units) for m in u["members"]}
    freq = [0] * len(units)
    for e in exercises:
        for i in {unit_of[m] for m in profiles[e["id"]][0]}:
            freq[i] += 1
    if mode == "unit":
        w = [1.0] * len(units)
    elif mode == "muscle":
        w = [float(len(u["members"])) for u in units]
    elif mode == "freq":
        w = [math.sqrt(f) for f in freq]
    elif mode == "freq-linear":
        w = [float(f) for f in freq]
    else:
        raise ValueError(mode)
    if loads is not None and strength > 0:
        w = [x * (1.0 - strength * l) for x, l in zip(w, loads)]
    mean = sum(w) / len(w)
    if mean <= 0:
        sys.exit("every unit weight is zero under these settings")
    return [x / mean for x in w], freq


# ------------------------------------------------------------------ daily life


def load_daily_life(data_dir: str):
    """daily_life.json, checked against ACTIVITY_LOAD and MUSCLE_LOAD."""
    try:
        with open(os.path.join(data_dir, "daily_life.json"), encoding="utf-8") as f:
            dl = json.load(f)
    except OSError as exc:
        sys.exit("--daily-life needs daily_life.json (%s); pass --daily-life 0 to ignore it" % exc)
    unknown = sorted(set(dl["meta"]["activities"]) - set(ACTIVITY_LOAD))
    if unknown:
        sys.exit("daily_life.json has activities with no load in ACTIVITY_LOAD: %s" % ", ".join(unknown))
    unknown = sorted(set(MUSCLE_LOAD) - {m["id"] for m in dl["muscles"]})
    if unknown:
        sys.exit("MUSCLE_LOAD names muscles that are not in daily_life.json: %s" % ", ".join(unknown))
    return dl


def muscle_load(entry):
    """(load, activity): the share of the routine's job an ordinary day already does for a muscle,
    0-1, and its main everyday activity.  MUSCLE_LOAD decides; a muscle it leaves out takes the
    largest load among its activities (listed most important first, each place down the list
    halving an activity's load), bounded by its tier."""
    driver = entry["activities"][0] if entry["activities"] else entry["tier"]
    if entry["id"] in MUSCLE_LOAD:
        return MUSCLE_LOAD[entry["id"]], driver
    floor, cap = TIER_LOAD[entry["tier"]]
    best = 0.0
    for k, act in enumerate(entry["activities"]):
        best = max(best, ACTIVITY_LOAD[act] * ACTIVITY_DECAY ** k)
    return min(cap, max(floor, best)), driver


def daily_loads(units, daily):
    """Per unit: the mean load of its member muscles, and the activity of the most-used member."""
    by_id = {m["id"]: m for m in daily["muscles"]}
    loads, drivers = [], []
    for u in units:
        seen = {}
        for m in u["members"]:
            base = m.split(":")[0]
            if base not in seen:
                if base not in by_id:
                    sys.exit("%s is not in daily_life.json" % base)
                seen[base] = muscle_load(by_id[base])
        loads.append(sum(l for l, _ in seen.values()) / len(seen))
        drivers.append(max(seen.values(), key=lambda t: t[0])[1])
    return loads, drivers


# ------------------------------------------------------------- popularity prior


def mention_texts(cal):
    t = []
    for p in cal["programs"]:
        t.append(("program", p["name"], p["summary"]))
    for w in cal["named_workouts"]:
        t.append(("workout", w["name"], w["prescription"] + " " + w.get("note", "")))
    for f in cal["formats"]:
        t.append(("format", f["name"], f.get("example", "")))
    for m in cal["modifiers"]:
        t.append(("modifier", m["name"], m.get("examples", "")))
    return t


def names_rx(names, plural=r"s?"):
    """Any of the names, case-insensitive, hyphen- and space-tolerant, plural allowed except after a
    proper name such as The Hundred ("in the hundreds" is not the Pilates move)."""
    alts = [re.escape(n).replace(r"\-", "[- ]?").replace(r"\ ", "[- ]?") + ("" if n.startswith("The ") else plural)
            for n in sorted(names, key=len, reverse=True)]     # longest first: "Superman lift" before "Superman"
    return re.compile(r"(?<![\w-])(?:" + "|".join(alts) + r")(?![\w-])", re.I)


def longest_matches(rxs, text):
    """(start, end, key) of each match of each regex, less those inside a longer match."""
    found = [(m.start(), m.end(), k) for k, rx in rxs.items() for m in rx.finditer(text)]
    return [f for f in found if not any(a <= f[0] and f[1] <= b and b - a > f[1] - f[0] for a, b, _ in found)]


def count_mentions(exercises, texts):
    """id -> [(kind, name of the program/workout/format/modifier)] naming the exercise or an aka.  A name
    inside a longer one counts only for the longer: "Hindu push-ups" names the Hindu push-up, not the push-up."""
    rxs = {e["id"]: names_rx([e["name"]] + e.get("aka", [])) for e in exercises}
    out = {i: [] for i in rxs}
    for kind, name, txt in texts:
        for i in {k for _, _, k in longest_matches(rxs, txt)}:
            out[i].append((kind, name))
    return out


def pattern_shares(cal, exercises, mentions):
    """Share of the database's programs that name an exercise, or a generic word, of each pattern.
    Generic words count in the program's name as well ("Fighter Pullup Program"), but not inside a
    longer exercise name or word, nor in the phrases of NOT_PATTERN_RE."""
    hit = defaultdict(set)
    for e in exercises:
        for kind, name in mentions[e["id"]]:
            if kind == "program":
                hit[pattern_of(e)].add(name)
    rxs = {("word", p): names_rx(words, r"(?:s|es)?") for p, words in PATTERN_KEYWORDS.items()}
    rxs.update({("exercise", e["id"]): names_rx([e["name"]] + e.get("aka", [])) for e in exercises})
    for prog in cal["programs"]:
        text = NOT_PATTERN_RE.sub(" ", prog["name"] + ". " + prog["summary"])
        for _, _, (kind, p) in longest_matches(rxs, text):
            if kind == "word":
                hit[p].add(prog["name"])
    n = len(cal["programs"])
    return {p: len(s) / n for p, s in hit.items()}, n


# ------------------------------------------------------------------ candidates


@dataclass
class Cand:
    ex: dict
    P: int                 # muscle units worked as primary
    A: int                 # muscle units worked as primary or secondary
    level: int
    pattern: int
    seconds: float
    mentions: int
    mod: float             # modular score: popularity - level cost - time cost
    tie: float             # tie-break, compared only on exact score ties
    headroom: int
    alternatives: list = field(default_factory=list)   # same profile, same level, same pattern

    @property
    def id(self):
        return self.ex["id"]

    @property
    def name(self):
        return self.ex["name"]


def equipment_ok(e, available):
    return any(all(part in available for part in alt.split("+")) for alt in e["equipment"])


def tie_bonus(e, P, A, room):
    """Prefer easier, then equipment-free, then better known, then more headroom, then bigger."""
    return (1e-3 * (3 - LEVELS.index(e["level"]))
            + 3e-4 * (1 if "none" in e["equipment"] else 0)
            + 1e-5 * min(len(e.get("aka", [])), 9)
            + 1e-7 * min(room, 99)
            + 1e-9 * (P.bit_count() + 0.5 * (A ^ P).bit_count()))


MAX_TIE = 3e-3 + 3e-4 + 9e-5 + 9.9e-6 + 1e-7


def make_cand(e, units_of, patterns, exercises, mentions, profiles, cfg):
    prim, sec = profiles[e["id"]]
    P = 0
    for m in prim:
        P |= 1 << units_of[m]
    A = P
    for m in sec:
        A |= 1 << units_of[m]
    lvl = LEVELS.index(e["level"])
    secs = est_seconds(e)
    n_ment = len(mentions[e["id"]])
    mod = (cfg["popularity"] * math.log1p(n_ment) - cfg["level_cost"] * lvl - cfg["exercise_cost"] * secs / BLOCK)
    room = headroom(e, exercises)
    return Cand(e, P, A, lvl, patterns.index(pattern_of(e)), secs, n_ment, mod, tie_bonus(e, P, A, room), room)


def build_candidates(exercises, units, patterns, mentions, profiles, cfg, *, max_level, types, available,
                     exclude_cats, exclude_ids):
    units_of = {m: i for i, u in enumerate(units) for m in u["members"]}
    raw = [e for e in exercises
           if LEVELS.index(e["level"]) <= max_level and e["type"] in types and e["category"] not in exclude_cats
           and e["id"] not in exclude_ids and equipment_ok(e, available)]
    cands = [make_cand(e, units_of, patterns, exercises, mentions, profiles, cfg) for e in raw]

    # merge identical profiles; the representative has the best modular score, then tie-break.
    # Under --time-budget only equally long ones merge: a faster member may fit a budget its
    # representative doesn't
    budget = cfg["time_budget"] is not None
    groups = defaultdict(list)
    for c in cands:
        groups[(c.P, c.A, c.level, c.pattern, c.seconds if budget else 0.0)].append(c)
    classes = []
    for members in groups.values():
        members.sort(key=lambda c: (-c.mod, -c.tie))
        rep = members[0]
        rep.alternatives = members[1:]
        classes.append(rep)

    # drop a profile only if a candidate of the same pattern covers a superset and is at
    # least as good on every other term.  Under --min-gain the superset must be the same
    # units: extra coverage can take another exercise's unique gain below the minimum
    same_units = cfg["min_gain"] > 0
    kept = []
    for c in classes:
        dominated = any(d is not c and d.pattern == c.pattern
                        and ((d.P, d.A) == (c.P, c.A) if same_units else (d.P & c.P == c.P and d.A & c.A == c.A))
                        and d.seconds <= c.seconds and d.mod >= c.mod - EPS
                        and (d.mod > c.mod + EPS or d.tie >= c.tie) for d in classes)
        if not dominated:
            kept.append(c)
    kept.sort(key=lambda c: (-(c.P.bit_count() + 0.5 * (c.A ^ c.P).bit_count()), -c.mod, c.name))
    return kept, classes, len(raw)


# --------------------------------------------------------------------- scoring


class Scorer:
    """Weighted coverage over muscle units plus a per-pattern prior and per-exercise modular terms."""

    def __init__(self, weights, sec_w, pattern_weights, cfg):
        self.w = weights
        self.sec_w = sec_w
        self.pw = pattern_weights
        self.one_per_pattern = cfg["one_per_pattern"]
        self.budget = cfg["time_budget"]
        self.min_gain = cfg["min_gain"]
        self.uniform = all(abs(x - weights[0]) < 1e-12 for x in weights)
        if not self.uniform:
            self.tables = []
            for c in range((len(weights) + 15) // 16):
                tbl = [0.0] * 65536
                for v in range(1, 65536):
                    low = v & -v
                    idx = c * 16 + low.bit_length() - 1
                    tbl[v] = tbl[v ^ low] + (weights[idx] if idx < len(weights) else 0.0)
                self.tables.append(tbl)

    def wsum(self, mask):
        if self.uniform:
            return mask.bit_count() * self.w[0]
        s = 0.0
        for t in self.tables:
            s += t[mask & 0xFFFF]
            mask >>= 16
        return s

    def coverage(self, prim, anym):
        return self.sec_w * self.wsum(anym) + (1 - self.sec_w) * self.wsum(prim)

    def pattern_value(self, pmask):
        return sum(self.pw[i] for i in range(len(self.pw)) if pmask >> i & 1)

    def muscle_gain(self, c, prim, anym):
        return self.sec_w * self.wsum(c.A & ~anym) + (1 - self.sec_w) * self.wsum(c.P & ~prim)

    def gain(self, c, prim, anym, pmask, mg=None):
        mg = self.muscle_gain(c, prim, anym) if mg is None else mg
        return mg + (0.0 if pmask >> c.pattern & 1 else self.pw[c.pattern]) + c.mod

    # --- routines as sets -------------------------------------------------
    def state(self, chosen, base):
        prim, anym, pmask, secs = base
        for c in chosen:
            prim |= c.P
            anym |= c.A
            pmask |= 1 << c.pattern
            secs += c.seconds
        return prim, anym, pmask, secs

    def value(self, chosen, base):
        prim, anym, pmask, _ = self.state(chosen, base)
        return (self.coverage(prim, anym) - self.coverage(base[0], base[1])
                + self.pattern_value(pmask) - self.pattern_value(base[2]) + sum(c.mod for c in chosen))

    def tie(self, chosen):
        return sum(c.tie for c in chosen)

    def unique_gain(self, c, others, base):
        prim, anym, _, _ = self.state([x for x in others if x is not c], base)
        return self.muscle_gain(c, prim, anym)

    def node(self, chosen, base):
        """State of a routine plus, for each member, the masks of everything else: what
        can_add() needs to test candidates cheaply."""
        st = self.state(chosen, base)
        others = []
        for x in chosen:
            oprim, oany, _, _ = self.state([y for y in chosen if y is not x], base)
            others.append((x, oprim, oany, x.P & ~oprim, x.A & ~oany))
        return st, others

    def can_add(self, c, node, mg=None):
        """Constraints: one per pattern, time budget, and every exercise (the new one
        included) keeps a unique muscle contribution of at least min_gain."""
        (prim, anym, pmask, secs), others = node
        if self.one_per_pattern and pmask >> c.pattern & 1:
            return False
        if self.budget is not None and secs + c.seconds > self.budget + EPS:
            return False
        if (self.muscle_gain(c, prim, anym) if mg is None else mg) < self.min_gain - EPS:
            return False
        for x, oprim, oany, uP, uA in others:
            if (uA & c.A) or (uP & c.P):          # c touches something only x covered
                if self.muscle_gain(x, oprim | c.P, oany | c.A) < self.min_gain - EPS:
                    return False
        return True


# ---------------------------------------------------------------------- search


def better(val, tie, best_val, best_tie):
    return val > best_val + EPS or (abs(val - best_val) <= EPS and tie > best_tie + 1e-15)


def greedy(cands, sc, n, base):
    chosen = []
    for _ in range(n):
        node = sc.node(chosen, base)
        prim, anym, pmask, _ = node[0]
        best, bg, bt = None, EPS, 0.0
        for c in cands:
            if c in chosen:
                continue
            g = sc.gain(c, prim, anym, pmask)
            if (g > bg + EPS or (abs(g - bg) <= EPS and c.tie > bt)) and sc.can_add(c, node):
                best, bg, bt = c, g, c.tie
        if best is None:
            break
        chosen.append(best)
    return chosen


def local_search(cands, sc, chosen, n, base):
    chosen = list(chosen)
    val, tie = sc.value(chosen, base), sc.tie(chosen)
    improved = True
    while improved:
        improved = False
        for i in range(len(chosen)):
            rest = chosen[:i] + chosen[i + 1:]
            rv, rt = sc.value(rest, base), sc.tie(rest)
            if better(rv, rt, val, tie):                       # dropping helps
                chosen, val, tie, improved = rest, rv, rt, True
                break
            node = sc.node(rest, base)
            for c in cands:
                if c in rest or not sc.can_add(c, node):
                    continue
                cv, ct = sc.value(rest + [c], base), rt + c.tie
                if better(cv, ct, val, tie):
                    chosen, val, tie, improved = rest + [c], cv, ct, True
                    break
            if improved:
                break
        if not improved and len(chosen) < n:                    # adding helps
            node = sc.node(chosen, base)
            for c in cands:
                if c in chosen or not sc.can_add(c, node):
                    continue
                cv, ct = sc.value(chosen + [c], base), tie + c.tie
                if better(cv, ct, val, tie):
                    chosen, val, tie, improved = chosen + [c], cv, ct, True
                    break
    return chosen


def branch_and_bound(cands, sc, n, base, incumbent, seconds=SEARCH_SECONDS):
    """Exact best feasible set of at most n candidates.  Returns (set, value, exact, nodes);
    exact is False if the time limit stopped the search (the set is then the best found)."""
    best_val, best_tie, best_set = sc.value(incumbent, base), sc.tie(incumbent), list(incumbent)
    nodes, N, exact, deadline = 0, len(cands), True, time.time() + seconds

    def rec(start, chosen, val, tie):
        nonlocal best_val, best_tie, best_set, nodes, exact
        nodes += 1
        if nodes % 256 == 0 and time.time() > deadline:
            exact = False
            return
        if better(val, tie, best_val, best_tie):
            best_val, best_tie, best_set = val, tie, list(chosen)
        k = n - len(chosen)
        if k == 0:
            return
        node = sc.node(chosen, base)
        prim, anym, pmask, _ = node[0]
        gl = []
        for i in range(start, N):
            c = cands[i]
            mg = sc.muscle_gain(c, prim, anym)
            g = sc.gain(c, prim, anym, pmask, mg)
            if g > EPS and sc.can_add(c, node, mg):
                gl.append((g, i))
        if not gl:
            return
        gl.sort(reverse=True)
        top = val + sum(g for g, _ in gl[:k])
        if top < best_val - EPS or (top <= best_val + EPS and tie + k * MAX_TIE <= best_tie):
            return
        # child i may only use candidates with a higher index: bound with the best k-1 of those
        heap, hsum, suffix = [], 0.0, {}
        for g, i in sorted(gl, key=lambda t: t[1], reverse=True):
            suffix[i] = hsum
            if k > 1:
                if len(heap) < k - 1:
                    heapq.heappush(heap, g)
                    hsum += g
                elif g > heap[0]:
                    hsum += g - heapq.heapreplace(heap, g)
        for g, i in gl:
            top = val + g + suffix[i]
            if top < best_val - EPS or (top <= best_val + EPS and tie + k * MAX_TIE <= best_tie):
                continue
            c = cands[i]
            chosen.append(c)
            rec(i + 1, chosen, val + g, tie + c.tie)
            chosen.pop()
            if not exact:
                return

    rec(0, [], 0.0, 0.0)
    return best_set, best_val, exact, nodes


def solve_size(cands, sc, n, base, warm=None):
    inc = local_search(cands, sc, greedy(cands, sc, n, base), n, base)
    if warm is not None and better(sc.value(warm, base), sc.tie(warm), sc.value(inc, base), sc.tie(inc)):
        inc = list(warm)
    return branch_and_bound(cands, sc, n, base, inc)


def neighbour_shared(order):
    """Primary units shared by consecutive exercises, the last and the first included: rounds repeat."""
    pairs = zip(order, order[1:] + order[:1]) if len(order) > 2 else zip(order, order[1:])
    return sum((x.P & y.P).bit_count() for x, y in pairs)


def circuit_order(chosen, db_order):
    """Order so that neighbours share as few primary units as possible; database order breaks ties
    (permutations of a sorted list come in lexicographic order, and min() keeps the first minimum)."""
    in_db_order = sorted(chosen, key=lambda c: db_order[c.id])
    if len(chosen) > CIRCUIT_MAX:
        return in_db_order
    return list(min(itertools.permutations(in_db_order), key=neighbour_shared))


# ---------------------------------------------------------------------- report


def unit_names(mask, units):
    return [units[i]["name"] for i in range(len(units)) if mask >> i & 1]


def equipment_text(e, meta_eq, available=None):
    alts = e["equipment"]
    if available is not None:
        have = [a for a in alts if all(p in available for p in a.split("+"))]
        alts = have or alts
    words = [" + ".join(meta_eq.get(p, p) for p in a.split("+")) for a in alts if a != "none"]
    if "none" in alts:
        return "no equipment" + (" (or %s)" % " / ".join(words) if words else "")
    return " / ".join(words)


def progression(e, exercises, cal, meta_eq):
    """Easier and harder steps; harder steps continue into the category's later progression groups."""
    if "step" not in e:
        return [], []
    steps = sorted((x for x in exercises if x["group"] == e["group"] and "step" in x), key=lambda x: x["step"])
    easier = [x for x in steps if x["step"] < e["step"]][-3:]
    harder = [x for x in steps if x["step"] > e["step"]][:3]
    cat = next(c for c in cal["categories"] if c["name"] == e["category"])
    names = [g["name"] for g in cat["groups"]]
    for g in cat["groups"][names.index(e["group"]) + 1:]:
        if len(harder) >= 3:
            break
        if g.get("progression"):
            more = sorted((x for x in exercises if x["group"] == g["name"] and "step" in x), key=lambda x: x["step"])
            harder += more[:3 - len(harder)]

    def fmt(x):
        tag = x["level"]
        if x["equipment"] != ["none"]:
            tag += ", " + equipment_text(x, meta_eq)
        if x["group"] != e["group"]:
            tag += "; " + x["group"]
        return "%s (%s)" % (x["name"], tag)
    return [fmt(x) for x in easier], [fmt(x) for x in harder]


def sentence(text, pattern):
    m = re.search(pattern, text)
    return m.group(0) if m else text


def fmt_seconds(s):
    return "%d s" % round(s) if s < 90 else "%d min %02d s" % divmod(round(s), 60)


def print_report(ctx):
    a, cal, units = ctx["args"], ctx["cal"], ctx["units"]
    sc, rows, chosen = ctx["sc"], ctx["rows"], ctx["chosen"]
    exercises, meta_eq = cal["exercises"], cal["meta"]["equipment"]
    base, w_total = ctx["base"], sum(sc.w)
    print("=" * 78)
    print("DAILY FULL-BODY CALISTHENICS - optimised from calisthenics.json + muscles.json")
    print("=" * 78)
    print("Data       : %d exercises; %d muscles, %d of them trained by some exercise, grouped into"
          % (len(exercises), ctx["n_muscles"], ctx["n_trained"]))
    print("             %d functional units (%s); unit weights: %s" % (len(units), a.units_desc, a.weighting_desc))
    if ctx["loads"] is not None:
        names = {k: v.split(":")[0] for k, v in ctx["daily"]["meta"]["activities"].items()}
        print("Daily life : weight x (1 - %.2g x load), the load being the share of the routine's job an ordinary day"
              " already does for the unit, by its main everyday activity (daily_life.json)" % a.daily_life)
        classes = defaultdict(lambda: defaultdict(list))
        for i, u in enumerate(units):
            classes[round(1 - a.daily_life * ctx["loads"][i], 2)][ctx["drivers"][i]].append(u["name"])
        for mult in sorted(classes):
            print("             x%-4s %s" % ("%.2g" % mult, "; ".join(
                "%s: %s" % (names.get(act, act), ", ".join(us)) for act, us in classes[mult].items())))
    print("Filters    : level <= %s | types: %s | equipment: %s"
          % (LEVELS[a.max_level_idx], ", ".join(a.types_list), ", ".join(a.equipment_list)))
    print("             excluded categories: %s" % ("; ".join(a.excluded_categories) or "none"))
    if a.exclude:
        print("             excluded exercises: %s" % ", ".join(a.exclude))
    if ctx["required"]:
        print("             required exercises: %s" % ", ".join(c.name for c in ctx["required"]))
    print("Candidates : %d exercises -> %d distinct profiles -> %d after dropping dominated ones"
          % (ctx["n_raw"], ctx["n_classes"], ctx["n_pool"]))
    print("Score      : coverage (primary 1, secondary %.2g) + pattern prior (x%.2g) + popularity (x%.2g)"
          % (a.secondary_weight, a.pattern_bonus, a.popularity))
    print("             - %.2g per level above beginner - %.2g per %s block%s; min unique gain %.2g;"
          " %s" % (a.level_cost, a.exercise_cost, fmt_seconds(BLOCK),
                   "" if a.time_budget is None else "; round budget %s" % fmt_seconds(a.time_budget),
                   a.min_gain, "one exercise per pattern" if not a.repeat_patterns else "patterns may repeat"))
    shares = ctx["shares"]
    present = sorted({c.pattern for c in ctx["pool"]} | {c.pattern for c in ctx["required"]},
                     key=lambda i: -shares.get(ctx["patterns"][i], 0))
    print("Patterns   : share of the %d programs using each pattern: %s"
          % (ctx["n_programs"], ", ".join("%s %.0f%%" % (ctx["patterns"][i], 100 * shares.get(ctx["patterns"][i], 0))
                                          for i in present)))
    print("Reachable  : %.1f of %.1f weighted units under these filters." % (sc.coverage(ctx["reach_P"], ctx["reach_A"]), w_total))
    print()

    print("BEST ROUTINE BY SIZE (the routine grows while each move earns more than it costs)")
    exact_all = all(r["exact"] for r in rows)
    print("  n  coverage        round   score%s  exercises" % ("" if exact_all else "  exact"))
    for r in rows:
        print("  %d  %5.1f (%3.0f%%)  %8s  %6.1f%s  %s%s" % (
            r["n"], r["cov"], 100 * r["cov"] / w_total, fmt_seconds(r["seconds"]), r["value"],
            "" if exact_all else ("   yes " if r["exact"] else "   no  "), ", ".join(c.name for c in r["set"]),
            "  <- recommended" if r is rows[-1] else ""))
    print()

    if not chosen:
        print("No exercise pays for its time under these costs; lower --exercise-cost or --min-gain.")
        return
    print("=" * 78)
    shared = neighbour_shared(chosen)
    print("THE ROUTINE  (%d exercise%s, one round about %s%s)"
          % (len(chosen), "" if len(chosen) == 1 else "s", fmt_seconds(sum(c.seconds for c in chosen)),
             "" if len(chosen) < 2 else "; database order" if len(chosen) > CIRCUIT_MAX else
             "; order alternates muscles" if shared == 0 else "; neighbours share as few muscles as they can"))
    print("=" * 78)
    total = sc.value(chosen, base)
    for i, c in enumerate(chosen, 1):
        e = c.ex
        lose = total - sc.value([x for x in chosen if x is not c], base)
        print("%d. %s   [%s | %s | %s | %s]" % (i, e["name"].upper(), e["level"], e["type"],
                                                equipment_text(e, meta_eq, a.equipment_list), fmt_seconds(c.seconds)))
        print("   %s" % e["note"])
        if e.get("aka"):
            print("   Also called: %s" % ", ".join(e["aka"]))
        print("   Works : %s" % ", ".join(unit_names(c.P, units)))
        sec = unit_names(c.A & ~c.P, units)
        if sec:
            print("   Also  : %s" % ", ".join(sec))
        print("   Worth : %.1f weighted units only it covers; dropping it costs %.1f score; named in %d of the"
              " database's %d program, workout, format and modifier entries; %d harder exercises in %s"
              % (sc.unique_gain(c, chosen, base), lose, c.mentions, ctx["n_texts"], c.headroom, e["category"]))
        easier, harder = progression(e, exercises, cal, meta_eq)
        if easier:
            print("   Easier: %s" % "; ".join(easier))
        if harder:
            print("   Harder: %s" % "; ".join(harder))
        swaps = [x for x in c.alternatives if not (x.ex["group"] == e["group"] and "step" in x.ex and "step" in e)]
        if swaps:
            print("   Same muscles, swap freely: %s" % ", ".join(x.name for x in swaps[:6]))
        print()

    prim, anym, pmask, _ = sc.state(chosen, base)
    weighted = [i for i in range(len(units)) if sc.w[i] > 0]    # a unit no exercise works as primary weighs 0
    full = [units[i]["name"] for i in weighted if prim >> i & 1]
    part = [units[i]["name"] for i in weighted if (anym >> i & 1) and not (prim >> i & 1)]
    miss = [units[i]["name"] for i in weighted if not (anym >> i & 1) and (ctx["reach_A"] >> i & 1)]
    unreach = [units[i]["name"] for i in weighted if not (ctx["reach_A"] >> i & 1)]
    cov = sc.coverage(prim, anym)
    print("COVERAGE: %.1f of %.1f weighted units (%.0f%%) - %d units worked as primary, %d only as secondary,"
          " %d untouched%s" % (cov, w_total, 100 * cov / w_total, len(full), len(part), len(miss) + len(unreach),
                               "" if len(weighted) == len(units) else ", %d with no weight" % (len(units) - len(weighted))))
    region = defaultdict(lambda: [0.0, 0.0])
    for i, u in enumerate(units):
        region[u["region"]][1] += sc.w[i]
        region[u["region"]][0] += sc.w[i] * (1.0 if prim >> i & 1 else (a.secondary_weight if anym >> i & 1 else 0.0))
    print("  By region      : " + " | ".join("%s %.1f/%.1f" % (r, v[0], v[1]) for r, v in region.items()))
    print("  Patterns       : " + ", ".join(ctx["patterns"][i] for i in range(len(ctx["patterns"])) if pmask >> i & 1))
    if part:
        print("  Secondary only : %s" % ", ".join(part))
    if miss:
        print("  Not covered    : %s" % ", ".join(miss))
    if unreach:
        print("  Unreachable under these filters: %s" % ", ".join(unreach))
    if not any(ctx["patterns"][c.pattern] in PULL_PATTERNS for c in ctx["pool"] + ctx["required"]):
        print("  Note: no pulling exercise fits these filters. A door frame gives the door-frame row and a sturdy"
              " table the table row (--equipment door or low-bar).")
    print()

    nxt = ctx["next"]
    if nxt:
        print("WHAT ONE MORE MOVE WOULD ADD (best candidates given the routine; net = score change)")
        for c, mg, g in nxt:
            print("  %-34s +%.1f weighted units, %s, %s, net %+.1f" % (
                c.name, mg, ctx["patterns"][c.pattern], fmt_seconds(c.seconds), g))
        print()

    fmt = {f["name"]: f for f in cal["formats"]}
    prog = {p["name"]: p for p in cal["programs"]}
    mods = [m["name"] for m in cal["modifiers"]]
    print("HOW TO RUN IT EVERY DAY (quoted from the database's formats and programs)")
    print("  Round    : %.0f s of work, then %.0f s to move to the next exercise - the 7-Minute Workout's timing"
          " (%s)." % (WORK, TRANSITION, prog["7-Minute Workout"]["origin"]))
    print("             Each-side moves do both sides. One round of %s takes about %s."
          % ("this move" if len(chosen) == 1 else "these %d moves" % len(chosen),
             fmt_seconds(sum(c.seconds for c in chosen))))
    print("  Rounds   : Minimalist Routine (%s): \"%s\"" % (prog["Minimalist Routine"]["origin"],
          sentence(prog["Minimalist Routine"]["summary"], r"\d+[–-]\d+ rounds.*?failure")))
    print("  Effort   : Autoregulation: %s" % fmt["Autoregulation"]["description"])
    print("  Progress : Recommended Routine (%s): \"%s\"" % (prog["Recommended Routine"]["origin"],
          sentence(prog["Recommended Routine"]["summary"], r"Tempo[^.]*\.")))
    print("             RCAF 5BX: \"%s\"" % sentence(prog["RCAF 5BX"]["summary"], r"Move up[^.]*\."))
    print("             Change these before changing the exercise: %s." % ", ".join(mods[:6]))
    print("  Busy days: Grease the Groove: %s" % fmt["Grease the Groove"]["description"])
    print("  Variety  : swap among the same-muscle alternatives above, or take one movement a day"
          " (Convict Conditioning's Veterano).")
    if len(chosen) > CIRCUIT_MAX:
        print("  Order    : database order; the order of more than %d moves is not optimised." % CIRCUIT_MAX)
    elif len(chosen) > 1:
        print("  Order    : %s, after the Superset format (%s)."
              % ("neighbours%s share no primary muscle" % (", the last and the first included," if len(chosen) > 2 else "")
                 if shared == 0 else
                 "neighbours share %d primary muscle unit%s, the fewest any order allows" % (shared, "" if shared == 1 else "s"),
                 fmt["Superset"]["description"].rstrip(".").lower()))


def write_json(path, ctx):
    a, sc, units = ctx["args"], ctx["sc"], ctx["units"]

    def cand_json(c):
        return {"id": c.id, "name": c.name, "level": c.ex["level"], "type": c.ex["type"], "pattern": ctx["patterns"][c.pattern],
                "equipment": c.ex["equipment"], "seconds": c.seconds, "mentions": c.mentions, "headroom": c.headroom,
                "note": c.ex["note"], "primary_units": unit_names(c.P, units), "secondary_units": unit_names(c.A & ~c.P, units),
                "alternatives": [x.id for x in c.alternatives]}
    out = {
        "settings": {k: getattr(a, k) for k in ["equipment_list", "max_level", "types_list", "excluded_categories", "exclude",
                                                 "require", "weighting", "units", "daily_life", "secondary_weight", "pattern_bonus",
                                                 "popularity", "level_cost", "exercise_cost", "time_budget", "min_gain",
                                                 "repeat_patterns", "max_n"]},
        "units": [{"name": u["name"], "region": u["region"], "members": u["members"], "weight": round(sc.w[i], 4),
                   "primary_exercises": ctx["freq"][i],
                   "daily_load": None if ctx["loads"] is None else round(ctx["loads"][i], 3),
                   "daily_activity": None if ctx["drivers"] is None else ctx["drivers"][i]} for i, u in enumerate(units)],
        "patterns": {p: round(ctx["shares"].get(p, 0.0), 3) for p in ctx["patterns"]},
        "rows": [{"n": r["n"], "coverage": round(r["cov"], 3), "seconds": r["seconds"], "score": round(r["value"], 3),
                  "exact": r["exact"], "exercises": [c.id for c in r["set"]]} for r in ctx["rows"]],
        "routine": {"seconds_per_round": sum(c.seconds for c in ctx["chosen"]),
                    "coverage": round(sc.coverage(*sc.state(ctx["chosen"], ctx["base"])[:2]), 3),
                    "exercises": [cand_json(c) for c in ctx["chosen"]]},
        "next": [{"id": c.id, "name": c.name, "coverage_gain": round(mg, 3), "net": round(g, 3)} for c, mg, g in ctx["next"]],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


# ------------------------------------------------------------------------ main


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Equipment keys are the keys of meta.equipment in calisthenics.json (none, wall, bar, low-bar, rings, "
               "suspension, dip-bars, parallettes, box, pole, rope, sliders, ab-wheel, towel, door, ...).\n"
               "Categories excluded by default: " + "; ".join(DEFAULT_EXCLUDE_CATEGORIES))
    p.add_argument("--dir", default=HERE, help="directory holding calisthenics.json, muscles.json and daily_life.json")
    p.add_argument("--equipment", default=DEFAULT_EQUIPMENT,
                   help="comma-separated equipment keys you have, or 'all' (default: %(default)s)")
    p.add_argument("--max-level", default="beginner", choices=LEVELS, help="hardest level allowed (default: %(default)s)")
    p.add_argument("--types", default=DEFAULT_TYPES, help="comma-separated exercise types allowed, or 'all' (default: %(default)s)")
    p.add_argument("--exclude-category", action="append", default=[], help="drop a category on top of the defaults (repeatable)")
    p.add_argument("--include-category", action="append", default=[], help="re-admit a category excluded by default, or 'all' (repeatable)")
    p.add_argument("--exclude", action="append", default=[], help="exercise id to drop (repeatable)")
    p.add_argument("--require", action="append", default=[], help="exercise id that must be in the routine (repeatable)")
    p.add_argument("--units", default="json", choices=["json", "parts"],
                   help="'json': muscle ids from calisthenics.json; 'parts': the markdown's part-aware lists (default: %(default)s)")
    p.add_argument("--weighting", default="freq", choices=["freq", "freq-linear", "unit", "muscle"],
                   help="unit weight: sqrt of primary-use count (freq, default), the count itself, 1 per unit, or 1 per muscle")
    p.add_argument("--daily-life", type=float, default=1.0,
                   help="scale on the share of its weight a unit loses for the work daily life already does: 1 takes "
                        "the full share, 0 ignores daily life (default: %(default)s)")
    p.add_argument("--secondary-weight", type=float, default=0.5, help="credit for a unit worked only as a secondary muscle (default: %(default)s)")
    p.add_argument("--pattern-bonus", type=float, default=2.0,
                   help="units credited per movement pattern, times the share of programs using it (default: %(default)s)")
    p.add_argument("--popularity", type=float, default=0.5,
                   help="units credited per exercise, times ln(1 + mentions in the database) (default: %(default)s)")
    p.add_argument("--level-cost", type=float, default=1.0, help="units charged per difficulty level above beginner (default: %(default)s)")
    p.add_argument("--exercise-cost", type=float, default=2.0, help="units charged per %d s block of round time (default: %%(default)s)" % BLOCK)
    p.add_argument("--time-budget", type=float, help="hard cap on seconds per round")
    p.add_argument("--min-gain", type=float, default=0.5,
                   help="weighted units of muscle coverage every exercise must contribute on its own (default: %(default)s)")
    p.add_argument("--repeat-patterns", action="store_true", help="allow more than one exercise per movement pattern")
    p.add_argument("--max-n", type=int, default=16, help="most exercises in the routine (default: %(default)s)")
    p.add_argument("--json", metavar="PATH", help="also write the result as JSON")
    a = p.parse_args(argv)
    a.max_level_idx = LEVELS.index(a.max_level)
    nums = [a.daily_life, a.secondary_weight, a.pattern_bonus, a.popularity, a.level_cost, a.exercise_cost,
            a.min_gain, 0.0 if a.time_budget is None else a.time_budget]
    checks = [(not all(math.isfinite(x) for x in nums), "numeric options must be finite numbers"),
              (a.max_n < 1, "--max-n must be at least 1"),
              (a.exercise_cost < 0 or a.level_cost < 0 or a.pattern_bonus < 0 or a.popularity < 0 or a.min_gain < 0,
               "costs, bonuses and --min-gain must not be negative"),
              (not 0 <= a.secondary_weight <= 1, "--secondary-weight must be between 0 and 1"),
              (not 0 <= a.daily_life <= 1, "--daily-life must be between 0 and 1"),
              (a.time_budget is not None and a.time_budget <= 0, "--time-budget must be positive")]
    for bad, msg in checks:
        if bad:
            p.error(msg)
    return a


def explain_missing(rid, exercises, a, excl):
    e = next((x for x in exercises if x["id"] == rid), None)
    if e is None:
        return "no exercise with this id in calisthenics.json"
    reasons = []
    if LEVELS.index(e["level"]) > a.max_level_idx:
        reasons.append("level is %s (raise --max-level)" % e["level"])
    if e["type"] not in a.types_list:
        reasons.append("type is %s (add it to --types)" % e["type"])
    if e["category"] in excl:
        reasons.append("category '%s' is excluded (--include-category)" % e["category"])
    if not equipment_ok(e, set(a.equipment_list)):
        reasons.append("needs %s (add it to --equipment)" % " or ".join(e["equipment"]))
    if rid in a.exclude:
        reasons.append("it is also in --exclude")
    return "; ".join(reasons) or "filtered out"


def main(argv=None):
    a = parse_args(argv)
    cal, mus = load_data(a.dir)
    exercises, muscles, meta_eq = cal["exercises"], mus["muscles"], cal["meta"]["equipment"]
    ids = {e["id"] for e in exercises}

    a.equipment_list = list(meta_eq) if a.equipment == "all" else [x.strip() for x in a.equipment.split(",") if x.strip()]
    bad = [x for x in a.equipment_list if x not in meta_eq]
    if bad:
        sys.exit("unknown equipment: %s (valid: %s)" % (", ".join(bad), ", ".join(meta_eq)))
    if "none" not in a.equipment_list:
        a.equipment_list.insert(0, "none")
    a.types_list = ALL_TYPES if a.types == "all" else [x.strip() for x in a.types.split(",") if x.strip()]
    bad = [x for x in a.types_list if x not in ALL_TYPES]
    if bad:
        sys.exit("unknown type: %s (valid: %s)" % (", ".join(bad), ", ".join(ALL_TYPES)))
    a.exclude, a.require = list(dict.fromkeys(a.exclude)), list(dict.fromkeys(a.require))
    bad = [x for x in a.exclude + a.require if x not in ids]
    if bad:
        sys.exit("unknown exercise id: %s" % ", ".join(bad))
    both = set(a.exclude) & set(a.require)
    if both:
        sys.exit("both required and excluded: %s" % ", ".join(sorted(both)))
    all_cats = [c["name"] for c in cal["categories"]]
    excl = set(DEFAULT_EXCLUDE_CATEGORIES) | set(a.exclude_category)
    if "all" in a.include_category:
        excl -= set(DEFAULT_EXCLUDE_CATEGORIES)
    excl -= set(a.include_category)
    bad = sorted({x for x in DEFAULT_EXCLUDE_CATEGORIES + a.exclude_category if x not in all_cats}
                 | {x for x in a.include_category if x not in all_cats + ["all"]})
    if bad:
        sys.exit("unknown category: %s\nvalid: %s" % ("; ".join(bad), "; ".join(all_cats)))
    both = set(a.exclude_category) & set(a.include_category)
    if both:
        sys.exit("both included and excluded: %s" % "; ".join(sorted(both)))
    a.excluded_categories = [c for c in all_cats if c in excl]

    # units, weights, priors
    profiles = markdown_profiles(os.path.join(a.dir, "calisthenics.md"), exercises) if a.units == "parts" else json_profiles(exercises)
    units = build_units(exercises, muscles, profiles)
    daily = load_daily_life(a.dir) if a.daily_life > 0 else None
    loads, drivers = daily_loads(units, daily) if daily else (None, None)
    weights, freq = unit_weights(units, exercises, profiles, a.weighting, loads, a.daily_life)
    a.units_desc = "muscles that always co-occur" + (", parts from calisthenics.md" if a.units == "parts" else "")
    a.weighting_desc = ({"freq": "sqrt of primary-use count", "freq-linear": "primary-use count",
                         "unit": "1 per unit", "muscle": "1 per muscle"}[a.weighting]
                        + ("" if loads is None else ", less daily life's work (--daily-life %.2g)" % a.daily_life))
    texts = mention_texts(cal)
    mentions = count_mentions(exercises, texts)
    shares, n_programs = pattern_shares(cal, exercises, mentions)
    patterns = sorted({pattern_of(e) for e in exercises})
    pattern_weights = [a.pattern_bonus * shares.get(p, 0.0) for p in patterns]
    cfg = {"popularity": a.popularity, "level_cost": a.level_cost, "exercise_cost": a.exercise_cost,
           "one_per_pattern": not a.repeat_patterns, "time_budget": a.time_budget, "min_gain": a.min_gain}

    pool, classes, n_raw = build_candidates(
        exercises, units, patterns, mentions, profiles, cfg, max_level=a.max_level_idx, types=set(a.types_list),
        available=set(a.equipment_list), exclude_cats=excl, exclude_ids=set(a.exclude))
    if not classes:
        sys.exit("no candidate exercises under these filters")
    sc = Scorer(weights, a.secondary_weight, pattern_weights, cfg)

    # required exercises: looked up among all profiles, including dominated ones
    required = []
    for rid in a.require:
        hit = next((c for c in classes if c.id == rid or any(x.id == rid for x in c.alternatives)), None)
        if hit is None:
            sys.exit("--require %s: %s" % (rid, explain_missing(rid, exercises, a, excl)))
        if hit in required:
            sys.exit("--require %s: same muscle profile as %s; keep one" % (rid, hit.id))
        if hit.id != rid:                                   # promote the alternative to representative
            members = [hit] + hit.alternatives
            hit = next(x for x in members if x.id == rid)
            hit.alternatives = [x for x in members if x is not hit]
        required.append(hit)
    if len(required) > a.max_n:
        sys.exit("%d required exercises but --max-n is %d" % (len(required), a.max_n))
    if len({c.pattern for c in required}) < len(required) and not a.repeat_patterns:
        sys.exit("two required exercises share a movement pattern; add --repeat-patterns")
    empty = (0, 0, 0, 0.0)
    if a.time_budget is not None and sum(c.seconds for c in required) > a.time_budget:
        sys.exit("the required exercises alone exceed the time budget")
    if a.time_budget is not None and not required and min(c.seconds for c in pool) > a.time_budget + EPS:
        sys.exit("--time-budget %s is shorter than the quickest exercise (%s)"
                 % (fmt_seconds(a.time_budget), fmt_seconds(min(c.seconds for c in pool))))
    base = sc.state(required, empty)
    req_keys = {(c.P, c.A, c.level, c.pattern) for c in required}
    pool = [c for c in pool if (c.P, c.A, c.level, c.pattern) not in req_keys]

    reach_P = reach_A = 0
    for c in classes:
        reach_P |= c.P
        reach_A |= c.A

    # grow the routine one size at a time; stop when nothing more pays for itself
    db_order = {e["id"]: i for i, e in enumerate(exercises)}
    t0 = time.time()
    rows, warm = [], None
    for n in range(max(1, len(required)), a.max_n + 1):
        k = n - len(required)
        s, exact, nodes = [], True, 0
        if k:
            s, _, exact, nodes = solve_size(pool, sc, k, base, warm)
        full = required + s
        prim, anym, pmask, secs = sc.state(full, empty)
        rows.append({"n": n, "set": sorted(full, key=lambda c: db_order[c.id]), "cov": sc.coverage(prim, anym),
                     "seconds": secs, "value": sc.value(full, empty), "exact": exact, "nodes": nodes})
        warm = s
        if len(full) < n:
            if len(rows) > 1 and {c.id for c in full} == {c.id for c in rows[-2]["set"]}:
                rows.pop()
            break
    elapsed = time.time() - t0
    chosen = circuit_order(rows[-1]["set"], db_order)

    # the best candidates the routine could still take (can_add: pattern, round budget, unique
    # gains), by muscle coverage gain
    prim, anym, pmask, _ = sc.state(chosen, empty)
    node = sc.node([c for c in chosen if c not in required], base)
    nxt = []
    for c in pool:
        if c in chosen or not sc.can_add(c, node):
            continue
        mg = sc.muscle_gain(c, prim, anym)
        nxt.append((c, mg, sc.gain(c, prim, anym, pmask)))
    nxt.sort(key=lambda t: -t[1])

    ctx = {"args": a, "cal": cal, "units": units, "sc": sc, "rows": rows, "chosen": chosen, "base": empty, "pool": pool,
           "required": required, "n_raw": n_raw, "n_classes": len(classes), "n_pool": len(pool),
           "n_muscles": len(muscles), "n_trained": sum(len(u["members"]) for u in units), "patterns": patterns,
           "shares": shares, "n_programs": n_programs, "n_texts": len(texts), "reach_P": reach_P, "reach_A": reach_A,
           "next": nxt[:3], "freq": freq, "daily": daily, "loads": loads, "drivers": drivers}
    print_report(ctx)
    print("\nSearch: %d candidates, sizes %d-%d, %s nodes, %.1f s%s%s"
          % (len(pool), rows[0]["n"], rows[-1]["n"], "{:,}".format(sum(r["nodes"] for r in rows)), elapsed,
             "" if all(r["exact"] for r in rows) else "  (time limit hit: some sizes are heuristic)",
             "  (stopped at --max-n: a longer routine may score higher)" if len(rows[-1]["set"]) == a.max_n else ""))
    if a.json:
        write_json(a.json, ctx)
        print("JSON written to %s" % a.json)


if __name__ == "__main__":
    main()
