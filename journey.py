"""Pure maths for the virtual journey: distances, positions, progress, ETA.

No network or file access here — sync.py does that. Everything takes plain
lists/dicts so the unit tests need no setup.
"""

import math
from datetime import date, timedelta
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")

EARTH_RADIUS_M = 6371000


def haversine_m(a, b):
    """Great-circle distance in metres between two [lat, lng] points."""
    lat1, lng1, lat2, lng2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def route_length_m(route):
    """Total length in metres of a [lat, lng] polyline."""
    return sum(haversine_m(route[i], route[i + 1]) for i in range(len(route) - 1))


def position_at(route, metres):
    """[lat, lng] after travelling `metres` along the polyline.

    Clamps to the start for metres <= 0 and to the end for metres beyond
    the route length. Interpolation within a segment is linear in lat/lng,
    which is accurate at these segment lengths.
    """
    if metres <= 0:
        return list(route[0])
    remaining = metres
    for i in range(len(route) - 1):
        seg = haversine_m(route[i], route[i + 1])
        if remaining <= seg and seg > 0:
            f = remaining / seg
            return [
                route[i][0] + (route[i + 1][0] - route[i][0]) * f,
                route[i][1] + (route[i + 1][1] - route[i][1]) * f,
            ]
        remaining -= seg
    return list(route[-1])


def _workout_day(workout):
    """The date part of a workout's 'date' field ('2026-10-01 07:00:00')."""
    raw = (workout.get("date") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def progress_m(workouts, start_date, rollover_m):
    """Journey progress: rower workouts dated on/after start_date, plus rollover.

    This is THE definition of progress (spec section 2) — position, percent
    and ETA are all derived from it.
    """
    start = date.fromisoformat(start_date)
    total = rollover_m
    for workout in workouts:
        if workout.get("type") != "rower":
            continue
        day = _workout_day(workout)
        if day is None or day < start:
            continue
        total += workout.get("distance", 0) or 0
    return total


def eta_date(workouts, done_m, total_m, today):
    """Projected finish date, from the trailing 14 days' average pace.

    Days without rowing count as zero. Returns None when the trailing
    average is zero, and today when the journey is already complete.
    """
    if done_m >= total_m:
        return today
    window_start = today - timedelta(days=13)  # 14 days including today
    recent = sum(
        (workout.get("distance", 0) or 0)
        for workout in workouts
        if workout.get("type") == "rower"
        and (day := _workout_day(workout)) is not None
        and window_start <= day <= today
    )
    rate = recent / 14
    if rate <= 0:
        return None
    days_left = math.ceil((total_m - done_m) / rate)
    return today + timedelta(days=days_left)


def london_today(now_utc):
    """The current date in Europe/London for an aware UTC datetime.

    Workout dates from the Logbook are the rower's local (UK) time, so
    'today' must be the UK date even when sync runs on a UTC server.
    """
    return now_utc.astimezone(LONDON).date()


def pace_per_500(workout):
    """Seconds per 500 m for one workout, from WORK time ('time', tenths
    of a second, rest excluded) — never from time_formatted, which
    includes rest on interval workouts. None unless both time and
    distance are > 0."""
    work_tenths = workout.get("time") or 0
    distance = workout.get("distance") or 0
    if work_tenths <= 0 or distance <= 0:
        return None
    return work_tenths / 10 / distance * 500


def format_pace(seconds):
    """'2:07.8' — rounds to the nearest 0.1 s FIRST so 119.96 -> '2:00.0'."""
    total = round(seconds, 1)
    minutes = int(total // 60)
    return f"{minutes}:{total - minutes * 60:04.1f}"


def _rower_days_and_metres(workouts):
    """Yield (day, workout) for rower workouts with parseable dates."""
    for workout in workouts:
        if workout.get("type") != "rower":
            continue
        day = _workout_day(workout)
        if day is not None:
            yield day, workout


def _metres_between(workouts, start, end):
    return sum(
        (workout.get("distance", 0) or 0)
        for day, workout in _rower_days_and_metres(workouts)
        if start <= day <= end
    )


def _week_start(day):
    return day - timedelta(days=day.weekday())


def week_m(workouts, today):
    """Metres rowed in the Monday-Sunday week containing today (all-time
    stat: the journey start_date plays no part)."""
    start = _week_start(today)
    return _metres_between(workouts, start, start + timedelta(days=6))


def prev_week_m(workouts, today):
    """Metres rowed in the Monday-Sunday week before today's."""
    start = _week_start(today) - timedelta(days=7)
    return _metres_between(workouts, start, start + timedelta(days=6))


def avg_pace_per_500(workouts, today):
    """Mean seconds per 500 m over the trailing 14 days including today.

    Sums work time and distance only over workouts where both are > 0
    (others are excluded, not fatal). None when the summed distance is 0.
    """
    window_start = today - timedelta(days=13)
    work_s = 0.0
    metres = 0
    for day, workout in _rower_days_and_metres(workouts):
        if not (window_start <= day <= today):
            continue
        work_tenths = workout.get("time") or 0
        distance = workout.get("distance") or 0
        if work_tenths > 0 and distance > 0:
            work_s += work_tenths / 10
            metres += distance
    if metres == 0:
        return None
    return work_s / metres * 500


def streaks(workouts, today):
    """(current, best) streaks of consecutive rowing days.

    Current counts back from today, or from yesterday when today has no
    row yet (stats are a 3-hourly server snapshot; this stops the first
    snapshots after a rowing day showing 0). Best is all-time.
    """
    days = {day for day, _ in _rower_days_and_metres(workouts)}
    if not days:
        return (0, 0)
    anchor = today if today in days else today - timedelta(days=1)
    current = 0
    day = anchor
    while day in days:
        current += 1
        day -= timedelta(days=1)
    best = 0
    for day in days:
        if day - timedelta(days=1) not in days:  # run starts here
            run = 1
            nxt = day + timedelta(days=1)
            while nxt in days:
                run += 1
                nxt += timedelta(days=1)
            best = max(best, run)
    return (current, best)
