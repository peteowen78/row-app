import unittest
from datetime import date

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


if __name__ == "__main__":
    unittest.main()
