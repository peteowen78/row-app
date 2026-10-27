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


def load_active_journey():
    config = json.loads(JOURNEYS_FILE.read_text())
    active = [j for j in config["journeys"] if j["status"] == "active"]
    if len(active) != 1:
        sys.exit(
            f"journeys.json must have exactly one active journey, found {len(active)}."
        )
    return active[0]


def build_progress(workouts, active, today):
    """Assemble the progress.json structure (spec section 2)."""
    total_m = journey_maths.progress_m(
        workouts, active["start_date"], active["rollover_m"]
    )
    route_length = round(journey_maths.route_length_m(active["route"]))
    completed = total_m >= route_length
    eta = journey_maths.eta_date(workouts, total_m, route_length, today)
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
                "recent_workouts": [
                    {
                        "date": w.get("date"),
                        "distance": w.get("distance"),
                        "time_formatted": w.get("time_formatted"),
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
    workouts = [
        {"date": "2026-10-02 07:00:00", "distance": 5000, "type": "rower",
         "time_formatted": "20:53.1"},
        {"date": "2026-09-20 07:00:00", "distance": 9999, "type": "rower"},
    ]
    progress = build_progress(workouts, active, date(2026, 10, 3))
    pete = progress["rowers"]["pete"]
    assert pete["total_m"] == 5500, pete["total_m"]
    assert pete["route_length_m"] > 41000, "route length must be computed"
    assert pete["completed"] is False
    assert pete["position"] != active["route"][0], "5.5km in, must have left Dover"
    assert len(pete["recent_workouts"]) == 2

    print(
        "Self-test passed: pagination, extraction, summary and journeys all behave."
    )


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    token = get_token()
    print("Fetching workout history from the Concept2 Logbook...")
    workouts = fetch_all(token)
    save(workouts)
    print(f"Saved to {DATA_FILE}")
    print(summarise(workouts))
    active = load_active_journey()
    progress = build_progress(workouts, active, date.today())
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))
    pete = progress["rowers"]["pete"]
    print(
        f"Journey: {pete['journey_name']} — {pete['percent']}% "
        f"({pete['total_m']:,} of {pete['route_length_m']:,} m), "
        f"ETA {pete['eta_date'] or '—'}"
    )


if __name__ == "__main__":
    main()
