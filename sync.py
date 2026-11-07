#!/usr/bin/env python3
"""Pull workout history from the Concept2 Logbook API into data/workouts.json.

Usage:
    python3 sync.py            # fetch all workouts for the authenticated user
    python3 sync.py --selftest # run offline checks (no network, no token)

The API token is read from, in order:
    1. the C2_TOKEN environment variable
    2. a .token file next to this script (one line, the token)

Generate a token at log.concept2.com:
    Edit Profile -> Applications -> Concept2 Logbook API integration
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

import journey as journey_maths

API_BASE = "https://log.concept2.com/api"
ACCEPT_HEADER = "application/vnd.c2logbook.v1+json"

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR / "data" / "workouts.json"
TOKEN_FILE = SCRIPT_DIR / ".token"
JOURNEYS_FILE = SCRIPT_DIR / "journeys.json"
PROGRESS_FILE = SCRIPT_DIR / "data" / "progress.json"


def get_token():
    token = os.environ.get("C2_TOKEN", "").strip()
    if token:
        return token
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        if token:
            return token
    sys.exit(
        "No API token found.\n"
        "Either set the C2_TOKEN environment variable, or paste your token into\n"
        f"a file at {TOKEN_FILE} (one line, nothing else).\n"
        "Generate one at log.concept2.com under\n"
        "Edit Profile -> Applications -> Concept2 Logbook API integration."
    )


def fetch_page(token, page):
    url = f"{API_BASE}/users/me/results?page={page}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": ACCEPT_HEADER,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 401:
            sys.exit(
                "The API said the token is invalid or expired (HTTP 401).\n"
                "Generate a fresh token at log.concept2.com and try again."
            )
        body = error.read().decode("utf-8", errors="replace")[:500]
        sys.exit(f"API request failed (HTTP {error.code}) for {url}\n{body}")


def extract_results(payload):
    """Pull the list of workouts out of one API response page."""
    return payload.get("data", [])


def total_pages(payload):
    """Read how many pages exist from the response's pagination block."""
    pagination = payload.get("meta", {}).get("pagination", {})
    return pagination.get("total_pages", 1)


def fetch_all(token):
    workouts = []
    page = 1
    while True:
        payload = fetch_page(token, page)
        results = extract_results(payload)
        workouts.extend(results)
        pages = total_pages(payload)
        print(f"  page {page} of {pages}: {len(results)} workouts")
        if page >= pages:
            break
        page += 1
    return workouts


def save(workouts):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(workouts, indent=2))


def summarise(workouts):
    if not workouts:
        return "No workouts found in the Logbook yet."
    metres = sum(w.get("distance", 0) or 0 for w in workouts)
    dates = sorted(w.get("date", "") for w in workouts if w.get("date"))
    first = dates[0][:10] if dates else "?"
    last = dates[-1][:10] if dates else "?"
    return (
        f"{len(workouts)} workouts, {metres:,} metres total, "
        f"from {first} to {last}."
    )


def load_journeys_config():
    try:
        return json.loads(JOURNEYS_FILE.read_text())
    except FileNotFoundError:
        sys.exit("journeys.json is missing — it should sit next to sync.py.")
    except json.JSONDecodeError as e:
        sys.exit(f"journeys.json is not valid JSON: {e}")


def load_active_journey(config):
    active = [j for j in config["journeys"] if j.get("status") == "active"]
    if len(active) != 1:
        sys.exit(
            f"journeys.json must have exactly one active journey, found {len(active)}. "
            'Set exactly one journey\'s "status" to "active".'
        )
    return active[0]


def build_progress(workouts, active, today):
    """Assemble the progress.json structure (spec: v1 section 2 + v1.5 section 2)."""
    total_m = journey_maths.progress_m(
        workouts, active["start_date"], active["rollover_m"]
    )
    route_length = round(journey_maths.route_length_m(active["route"]))
    completed = total_m >= route_length
    eta = journey_maths.eta_date(workouts, total_m, route_length, today)
    pace = journey_maths.avg_pace_per_500(workouts, today)
    streak_now, streak_best = journey_maths.streaks(workouts, today)
    recent = sorted(
        (w for w in workouts if w.get("type") == "rower" and w.get("date")),
        key=lambda w: w["date"],
        reverse=True,
    )[:10]
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rowers": {
            "pete": {
                "journey_id": active["id"],
                "journey_name": active["name"],
                "total_m": total_m,
                "route_length_m": route_length,
                "percent": round(min(100.0, total_m / route_length * 100), 1),
                "position": journey_maths.position_at(active["route"], total_m),
                "eta_date": eta.isoformat() if eta else None,
                "completed": completed,
                "rollover_m": max(0, total_m - route_length),
                "stats": {
                    "week_m": journey_maths.week_m(workouts, today),
                    "prev_week_m": journey_maths.prev_week_m(workouts, today),
                    "avg_pace_per_500": (
                        journey_maths.format_pace(pace) if pace is not None else None
                    ),
                    "streak_days": streak_now,
                    "best_streak_days": streak_best,
                },
                "recent_workouts": [
                    {
                        "date": w.get("date"),
                        "distance": w.get("distance"),
                        "time_formatted": w.get("time_formatted"),
                        "pace": (
                            journey_maths.format_pace(p)
                            if (p := journey_maths.pace_per_500(w)) is not None
                            else None
                        ),
                    }
                    for w in recent
                ],
            }
        },
    }


def selftest():
    page_one = {
        "data": [
            {"id": 1, "date": "2026-10-01 07:00:00", "distance": 5000},
            {"id": 2, "date": "2026-10-03 07:10:00", "distance": 2000},
        ],
        "meta": {"pagination": {"total_pages": 2, "current_page": 1}},
    }
    page_two = {
        "data": [{"id": 3, "date": "2026-10-05 07:05:00", "distance": 10000}],
        "meta": {"pagination": {"total_pages": 2, "current_page": 2}},
    }
    empty = {"data": []}

    assert len(extract_results(page_one)) == 2
    assert total_pages(page_one) == 2
    assert total_pages(empty) == 1, "missing pagination must mean one page"

    combined = extract_results(page_one) + extract_results(page_two)
    summary = summarise(combined)
    assert "3 workouts" in summary, summary
    assert "17,000 metres" in summary, summary
    assert "2026-10-01" in summary and "2026-10-05" in summary, summary
    assert summarise([]) == "No workouts found in the Logbook yet."

    active = {
        "id": "t", "name": "Test", "start_date": "2026-10-01",
        "status": "active", "rollover_m": 500,
        "route": [[51.1279, 1.3134], [50.9513, 1.8587]],
    }
    config = {"journeys": [
        {"id": "q", "name": "Queued", "status": "planned",
         "route": [[0.0, 0.0], [1.0, 1.0]]},  # no start_date/rollover_m on purpose
        active,
    ]}
    assert load_active_journey(config) is active, "planned entries without start_date must not break selection"
    workouts = [
        # interval row: 'time' is WORK time (tenths); time_formatted includes rest
        {"date": "2026-10-02 07:00:00", "distance": 5000, "type": "rower",
         "time": 14142, "time_formatted": "35:34.2"},
        # a half-past-midnight London row on the 2nd
        {"date": "2026-10-02 00:30:00", "distance": 1000, "type": "rower",
         "time": 2560, "time_formatted": "4:16.0"},
        # no 'time': excluded from pace, still counts for distance stats
        {"date": "2026-09-20 07:00:00", "distance": 9999, "type": "rower"},
    ]
    progress = build_progress(workouts, active, date(2026, 10, 3))
    pete = progress["rowers"]["pete"]
    assert pete["total_m"] == 6500, pete["total_m"]
    assert pete["route_length_m"] > 41000, "route length must be computed"
    assert pete["completed"] is False
    assert pete["position"] != active["route"][0], "6km in, must have left the start"
    assert len(pete["recent_workouts"]) == 3
    assert pete["recent_workouts"][0]["pace"] == "2:21.4", pete["recent_workouts"][0]
    assert pete["recent_workouts"][0]["time_formatted"] == "35:34.2"  # display shows elapsed time, not work time
    assert pete["recent_workouts"][2]["pace"] is None
    stats = pete["stats"]
    assert stats["week_m"] == 6000, stats          # week Mon 28 Sep - Sun 4 Oct
    assert stats["prev_week_m"] == 0, stats
    assert stats["avg_pace_per_500"] == "2:19.2", stats  # 1670.2s / 6000m * 500
    assert stats["streak_days"] == 1, stats        # rowed yesterday (the 2nd)
    assert stats["best_streak_days"] == 1, stats
    json.dumps(progress, allow_nan=False)           # output must be valid strict JSON

    print(
        "Self-test passed: pagination, extraction, summary and journeys all behave."
    )


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    token = get_token()
    active = load_active_journey(load_journeys_config())
    print("Fetching workout history from the Concept2 Logbook...")
    workouts = fetch_all(token)
    save(workouts)
    print(f"Saved to {DATA_FILE}")
    print(summarise(workouts))
    today = journey_maths.london_today(datetime.now(timezone.utc))
    progress = build_progress(workouts, active, today)
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))
    pete = progress["rowers"]["pete"]
    print(
        f"Journey: {pete['journey_name']} — {pete['percent']}% "
        f"({pete['total_m']:,} of {pete['route_length_m']:,} m), "
        f"ETA {pete['eta_date'] or '—'}"
    )


if __name__ == "__main__":
    main()
