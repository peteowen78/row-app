import unittest
from datetime import date, datetime, timezone

import journey

DOVER = [51.1279, 1.3134]
CALAIS = [50.9513, 1.8587]


class TestRouteLength(unittest.TestCase):
    def test_zero_length_for_single_point(self):
        self.assertEqual(journey.route_length_m([DOVER]), 0)

    def test_dover_calais_is_about_42km(self):
        # Great-circle distance between these two harbour coordinates.
        length = journey.route_length_m([DOVER, CALAIS])
        self.assertGreater(length, 41000)
        self.assertLess(length, 44000)

    def test_multi_segment_sums(self):
        mid = [51.04, 1.59]
        via_mid = journey.route_length_m([DOVER, mid, CALAIS])
        self.assertAlmostEqual(
            via_mid,
            journey.haversine_m(DOVER, mid) + journey.haversine_m(mid, CALAIS),
            places=6,
        )
        # a detour never shortens the route
        self.assertGreaterEqual(via_mid, journey.route_length_m([DOVER, CALAIS]))


class TestPositionAlongRoute(unittest.TestCase):
    def test_zero_metres_is_the_start(self):
        self.assertEqual(journey.position_at([DOVER, CALAIS], 0), DOVER)

    def test_full_distance_is_the_end(self):
        length = journey.route_length_m([DOVER, CALAIS])
        self.assertEqual(journey.position_at([DOVER, CALAIS], length), CALAIS)

    def test_beyond_the_end_stays_at_the_end(self):
        self.assertEqual(journey.position_at([DOVER, CALAIS], 10**9), CALAIS)

    def test_quarter_way_is_quarter_distance_from_start(self):
        length = journey.route_length_m([DOVER, CALAIS])
        pos = journey.position_at([DOVER, CALAIS], length / 4)
        self.assertAlmostEqual(journey.haversine_m(DOVER, pos), length / 4, delta=20)

    def test_negative_metres_clamps_to_start(self):
        self.assertEqual(journey.position_at([DOVER, CALAIS], -500), DOVER)

    def test_walks_past_intermediate_waypoints(self):
        mid = [51.04, 1.59]
        route = [DOVER, mid, CALAIS]
        first_leg = journey.haversine_m(DOVER, mid)
        # 1km into the second leg must sit past the midpoint, not before it
        lat, lng = journey.position_at(route, first_leg + 1000)
        self.assertGreater(lng, mid[1])
        self.assertAlmostEqual(journey.haversine_m(mid, [lat, lng]), 1000, delta=5)


def w(date_str, distance, wtype="rower"):
    return {"date": date_str, "distance": distance, "type": wtype}


class TestJourneyWorkouts(unittest.TestCase):
    def test_keeps_rower_rows_on_or_after_start_only(self):
        workouts = [
            w("2026-10-25 07:00:00", 4000),            # before start — out
            w("2026-10-26 07:00:00", 5000),            # on start date — in
            w("2026-10-27 18:30:00", 6000),            # after — in
            w("2026-10-27 19:00:00", 7000, "bikeerg"), # not a rower — out
            {"type": "rower", "date": "not a date", "distance": 9000},  # bad date — out
        ]
        kept = journey.journey_workouts(workouts, "2026-10-26")
        self.assertEqual([x["distance"] for x in kept], [5000, 6000])


class TestProgress(unittest.TestCase):
    def test_sums_rower_workouts_on_or_after_start_date(self):
        workouts = [
            w("2026-10-25 07:00:00", 4000),   # before start — ignored
            w("2026-10-26 07:00:00", 5000),   # on start date — counts
            w("2026-10-27 18:30:00", 6000),   # after — counts
        ]
        self.assertEqual(journey.progress_m(workouts, "2026-10-26", 0), 11000)

    def test_ignores_non_rower_types(self):
        workouts = [w("2026-10-26 07:00:00", 5000, "bikeerg")]
        self.assertEqual(journey.progress_m(workouts, "2026-10-26", 0), 0)

    def test_adds_rollover(self):
        workouts = [w("2026-10-26 07:00:00", 5000)]
        self.assertEqual(journey.progress_m(workouts, "2026-10-26", 1200), 6200)

    def test_ignores_workouts_with_bad_date_or_none_distance(self):
        workouts = [
            {"type": "rower", "date": "not a date", "distance": 5000},  # bad date — excluded
            {"type": "rower", "date": "2026-10-26 07:00:00", "distance": None},  # None distance — adds 0
            w("2026-10-26 07:00:00", 5000),
        ]
        self.assertEqual(journey.progress_m(workouts, "2026-10-26", 0), 5000)


class TestEta(unittest.TestCase):
    def test_steady_rate_projects_remaining_days(self):
        today = date(2026, 10, 26)
        # 14,000 m in the last 14 days = 1,000 m/day; 3,000 m left = 3 days
        workouts = [w("2026-10-20 07:00:00", 14000)]
        eta = journey.eta_date(workouts, done_m=0, total_m=3000, today=today)
        self.assertEqual(eta, date(2026, 10, 29))

    def test_no_recent_rowing_means_no_eta(self):
        today = date(2026, 10, 26)
        workouts = [w("2026-09-01 07:00:00", 14000)]  # outside the window
        self.assertIsNone(journey.eta_date(workouts, 0, 3000, today))

    def test_completed_journey_etas_today(self):
        today = date(2026, 10, 26)
        workouts = [w("2026-10-25 07:00:00", 5000)]
        self.assertEqual(journey.eta_date(workouts, 40000, 33000, today), today)

    def test_window_is_exactly_14_days_including_today(self):
        today = date(2026, 10, 26)
        inside = [w("2026-10-13 07:00:00", 14000)]   # 13 days back — counts
        outside = [w("2026-10-12 07:00:00", 14000)]  # 14 days back — doesn't
        self.assertIsNotNone(journey.eta_date(inside, 0, 3000, today))
        self.assertIsNone(journey.eta_date(outside, 0, 3000, today))

    def test_partial_days_round_up(self):
        today = date(2026, 10, 26)
        # 1,000 m/day with 2,500 m left = 2.5 days -> 3 days
        workouts = [w("2026-10-20 07:00:00", 14000)]
        eta = journey.eta_date(workouts, done_m=0, total_m=2500, today=today)
        self.assertEqual(eta, date(2026, 10, 29))

    def test_eta_ignores_non_rower_workouts(self):
        today = date(2026, 10, 26)
        workouts = [w("2026-10-20 07:00:00", 99999, "bikeerg")]
        self.assertIsNone(journey.eta_date(workouts, 0, 3000, today))

    def test_exactly_complete_etas_today(self):
        today = date(2026, 10, 26)
        self.assertEqual(journey.eta_date([], 33000, 33000, today), today)


class TestLondonToday(unittest.TestCase):
    def test_bst_evening_utc_is_next_london_day(self):
        self.assertEqual(
            journey.london_today(datetime(2026, 6, 1, 23, 30, tzinfo=timezone.utc)),
            date(2026, 6, 2),
        )

    def test_winter_evening_stays_same_day(self):
        self.assertEqual(
            journey.london_today(datetime(2026, 1, 10, 23, 30, tzinfo=timezone.utc)),
            date(2026, 1, 10),
        )


class TestPacePer500(unittest.TestCase):
    def test_work_time_not_elapsed_time(self):
        # Real interval row: 5,000 m, work time 14142 tenths (23:34.2),
        # time_formatted "35:34.2" includes rest and must be ignored.
        w = {"time": 14142, "distance": 5000, "time_formatted": "35:34.2"}
        self.assertAlmostEqual(journey.pace_per_500(w), 141.42, places=2)

    def test_missing_or_zero_fields_give_none(self):
        self.assertIsNone(journey.pace_per_500({"distance": 5000}))
        self.assertIsNone(journey.pace_per_500({"time": 1000}))
        self.assertIsNone(journey.pace_per_500({"time": 0, "distance": 5000}))
        self.assertIsNone(journey.pace_per_500({"time": 1000, "distance": 0}))


class TestFormatPace(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(journey.format_pace(127.84), "2:07.8")

    def test_rounding_carries_into_minutes(self):
        self.assertEqual(journey.format_pace(119.96), "2:00.0")

    def test_under_a_minute(self):
        self.assertEqual(journey.format_pace(26.6), "0:26.6")


def tw(date_str, distance, time_tenths):
    return {"date": date_str, "distance": distance, "type": "rower",
            "time": time_tenths}


class TestWeekMetres(unittest.TestCase):
    # 2026-10-03 is a Saturday; its week is Mon 2026-09-28 .. Sun 2026-10-04.
    def test_counts_only_the_monday_to_sunday_week(self):
        workouts = [
            w("2026-09-27 09:00:00", 1000),  # Sunday before — prev week
            w("2026-09-28 09:00:00", 2000),  # Monday — this week
            w("2026-10-03 09:00:00", 3000),  # today — this week
            w("2026-10-04 09:00:00", 4000),  # Sunday — this week (future-dated is fine)
            w("2026-10-05 09:00:00", 5000),  # next Monday — out
        ]
        today = date(2026, 10, 3)
        self.assertEqual(journey.week_m(workouts, today), 9000)
        self.assertEqual(journey.prev_week_m(workouts, today), 1000)

    def test_monday_today_starts_a_fresh_week(self):
        workouts = [w("2026-10-04 09:00:00", 4000)]  # Sunday
        self.assertEqual(journey.week_m(workouts, date(2026, 10, 5)), 0)
        self.assertEqual(journey.prev_week_m(workouts, date(2026, 10, 5)), 4000)

    def test_sunday_today_is_the_last_day_of_its_week(self):
        workouts = [
            w("2026-09-28 09:00:00", 2000),  # Monday — this week
            w("2026-10-04 23:00:00", 4000),  # Sunday (today) — this week
            w("2026-10-05 09:00:00", 5000),  # next Monday — out
        ]
        self.assertEqual(journey.week_m(workouts, date(2026, 10, 4)), 6000)

    def test_ignores_non_rower_and_bad_dates(self):
        workouts = [
            w("2026-10-03 09:00:00", 3000, "bikeerg"),
            {"type": "rower", "date": "not a date", "distance": 9000},
        ]
        self.assertEqual(journey.week_m(workouts, date(2026, 10, 3)), 0)


class TestAvgPace(unittest.TestCase):
    def test_sums_work_time_over_distance(self):
        today = date(2026, 10, 3)
        workouts = [
            tw("2026-10-02 07:00:00", 5000, 14142),  # interval: work time only
            tw("2026-10-02 00:30:00", 1000, 2560),
        ]
        # (14142+2560)/10 s over 6000 m -> 139.183 s per 500 m
        self.assertAlmostEqual(
            journey.avg_pace_per_500(workouts, today), 139.183, places=2)

    def test_rows_without_time_are_excluded_not_fatal(self):
        today = date(2026, 10, 3)
        workouts = [
            tw("2026-10-02 07:00:00", 1000, 2500),
            w("2026-10-01 07:00:00", 9999),  # no time — excluded from pace
            tw("2026-10-02 08:00:00", 0, 3000),  # time but no distance — excluded
        ]
        self.assertAlmostEqual(
            journey.avg_pace_per_500(workouts, today), 125.0, places=2)

    def test_none_when_no_usable_rows_in_window(self):
        today = date(2026, 10, 3)
        self.assertIsNone(journey.avg_pace_per_500([w("2026-10-02 07:00:00", 9999)], today))
        self.assertIsNone(journey.avg_pace_per_500(
            [tw("2026-09-01 07:00:00", 5000, 12000)], today))  # outside 14 days

    def test_window_is_14_days_including_today(self):
        today = date(2026, 10, 14)
        inside = [tw("2026-10-01 07:00:00", 500, 1000)]   # 13 days back
        outside = [tw("2026-09-30 07:00:00", 500, 1000)]  # 14 days back
        self.assertIsNotNone(journey.avg_pace_per_500(inside, today))
        self.assertIsNone(journey.avg_pace_per_500(outside, today))

    def test_today_counts_and_tomorrow_does_not(self):
        today = date(2026, 10, 3)
        self.assertIsNotNone(journey.avg_pace_per_500(
            [tw("2026-10-03 06:00:00", 500, 1000)], today))
        self.assertIsNone(journey.avg_pace_per_500(
            [tw("2026-10-04 06:00:00", 500, 1000)], today))


class TestStreaks(unittest.TestCase):
    def test_rowed_today_and_yesterday(self):
        workouts = [w("2026-10-02 08:00:00", 1000), w("2026-10-03 08:00:00", 1000)]
        self.assertEqual(journey.streaks(workouts, date(2026, 10, 3)), (2, 2))

    def test_not_rowed_yet_today_counts_from_yesterday(self):
        workouts = [w("2026-10-01 08:00:00", 1000), w("2026-10-02 08:00:00", 1000)]
        self.assertEqual(journey.streaks(workouts, date(2026, 10, 3)), (2, 2))

    def test_gap_breaks_current_but_best_remembers(self):
        workouts = [
            w("2026-09-01 08:00:00", 1000), w("2026-09-02 08:00:00", 1000),
            w("2026-09-03 08:00:00", 1000),  # best run of 3
            w("2026-10-03 08:00:00", 1000),
        ]
        self.assertEqual(journey.streaks(workouts, date(2026, 10, 3)), (1, 3))

    def test_two_days_ago_is_no_current_streak(self):
        workouts = [w("2026-10-01 08:00:00", 1000)]
        self.assertEqual(journey.streaks(workouts, date(2026, 10, 3)), (0, 1))

    def test_empty_history(self):
        self.assertEqual(journey.streaks([], date(2026, 10, 3)), (0, 0))


if __name__ == "__main__":
    unittest.main()
