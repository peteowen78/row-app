"""Pure maths for the virtual journey: distances, positions, progress, ETA.

No network or file access here — sync.py does that. Everything takes plain
lists/dicts so the unit tests need no setup.
"""

import math
from datetime import date, timedelta

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
