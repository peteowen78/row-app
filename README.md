# Row App

Pulls Pete's rowing workout history from the Concept2 Online Logbook
(the cloud service ErgData syncs every workout to).

## One-time setup

1. Log in at https://log.concept2.com
2. Go to **Edit Profile → Applications → Concept2 Logbook API integration**
3. Generate a long-lived token
4. Paste the token into a file called `.token` in this folder
   (one line, nothing else — it is gitignored and never committed)

## Pulling your workouts

```bash
python3 sync.py
```

This downloads every workout to `data/workouts.json` (also gitignored —
it's personal fitness data) and prints a summary: how many workouts,
total metres, date range. Run it again any time; it re-fetches the full
history, so it is always up to date.

## Checking the script without a token

```bash
python3 sync.py --selftest
```

Runs offline checks of the pagination and summary logic.

## What's in each workout record

Date, distance (metres), time, workout type and source (e.g. ErgData).
Per-stroke detail (pace, stroke rate, heart rate) is available from a
separate API endpoint per workout — not pulled yet, planned for a later
version.

## Switching to the next journey (manual, v1)

When a journey completes, `data/progress.json` reports `rollover_m`.
In `journeys.json`: set the finished journey's status to `"completed"`,
set the next journey's status to `"active"`, copy the reported surplus
into its `rollover_m`, and set its `start_date` to the day AFTER the
workout that completed the old journey (so that workout isn't counted
twice). Run `python3 sync.py` to confirm.
