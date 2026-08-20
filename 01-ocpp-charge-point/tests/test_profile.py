"""Unit tests for smart-charging evaluation.  智能充电求值的单元测试。

Run / 运行:  python -m unittest discover -s tests -v
"""

import unittest

from cp import profile as P

# The exact payload our toy CSMS pushes (camelCase, as it goes on the wire).
# 玩具后台下发的原始报文 (camelCase，线上格式)。
WIRE_CAMEL = {
    "chargingProfileId": 100,
    "stackLevel": 1,
    "chargingProfilePurpose": "TxProfile",
    "chargingProfileKind": "Relative",
    "chargingSchedule": {
        "chargingRateUnit": "A",
        "chargingSchedulePeriod": [
            {"startPeriod": 0, "limit": 32.0},
            {"startPeriod": 30, "limit": 16.0},
            {"startPeriod": 60, "limit": 8.0},
        ],
    },
}

# The same payload as the `ocpp` library hands it to a handler (snake_case).
# 同一份报文，`ocpp` 库交给 handler 时的样子 (snake_case)。
WIRE_SNAKE = {
    "charging_profile_id": 100,
    "stack_level": 1,
    "charging_profile_purpose": "TxProfile",
    "charging_profile_kind": "Relative",
    "charging_schedule": {
        "charging_rate_unit": "A",
        "charging_schedule_period": [
            {"start_period": 0, "limit": 32.0},
            {"start_period": 30, "limit": 16.0},
            {"start_period": 60, "limit": 8.0},
        ],
    },
}


class TestParse(unittest.TestCase):
    def test_both_key_styles_parse_identically(self):
        """camelCase and snake_case must produce the same Profile.
        两种键风格必须解析出完全相同的 Profile。"""
        self.assertEqual(P.parse(WIRE_CAMEL), P.parse(WIRE_SNAKE))

    def test_periods_are_sorted(self):
        shuffled = dict(WIRE_CAMEL)
        shuffled["chargingSchedule"] = dict(WIRE_CAMEL["chargingSchedule"])
        shuffled["chargingSchedule"]["chargingSchedulePeriod"] = [
            {"startPeriod": 60, "limit": 8.0},
            {"startPeriod": 0, "limit": 32.0},
            {"startPeriod": 30, "limit": 16.0},
        ]
        self.assertEqual([p.start_period for p in P.parse(shuffled).periods], [0, 30, 60])

    def test_missing_schedule_raises(self):
        with self.assertRaises(KeyError):
            P.parse({"chargingProfileId": 1, "stackLevel": 0,
                     "chargingProfilePurpose": "TxProfile",
                     "chargingProfileKind": "Relative"})


class TestLimitAt(unittest.TestCase):
    def setUp(self):
        self.p = P.parse(WIRE_CAMEL)

    def test_step_boundaries(self):
        """The active period is the last one already reached.
        生效周期是最后一个已到达的周期 —— 边界值最容易写错。"""
        cases = [(0, 32.0), (29.9, 32.0), (30, 16.0), (59.9, 16.0), (60, 8.0), (999, 8.0)]
        for elapsed, expected in cases:
            with self.subTest(elapsed=elapsed):
                self.assertEqual(P.limit_at(self.p, elapsed), expected)

    def test_expires_after_duration(self):
        d = dict(WIRE_CAMEL)
        d["chargingSchedule"] = dict(WIRE_CAMEL["chargingSchedule"], duration=90)
        p = P.parse(d)
        self.assertEqual(P.limit_at(p, 89), 8.0)
        self.assertIsNone(P.limit_at(p, 90))


class TestEffectiveLimit(unittest.TestCase):
    @staticmethod
    def make(profile_id, stack, purpose, limit):
        return P.parse({
            "chargingProfileId": profile_id,
            "stackLevel": stack,
            "chargingProfilePurpose": purpose,
            "chargingProfileKind": "Relative",
            "chargingSchedule": {
                "chargingRateUnit": "A",
                "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit}],
            },
        })

    def test_higher_stack_level_wins_within_a_purpose(self):
        ps = [self.make(1, 0, "TxProfile", 32.0), self.make(2, 5, "TxProfile", 10.0)]
        self.assertEqual(P.effective_limit(ps, 0), 10.0)

    def test_tx_profile_beats_tx_default(self):
        ps = [self.make(1, 0, "TxDefaultProfile", 16.0), self.make(2, 0, "TxProfile", 25.0)]
        self.assertEqual(P.effective_limit(ps, 0), 25.0)

    def test_station_max_is_a_hard_ceiling(self):
        """ChargePointMaxProfile caps everything, even a more specific profile.
        ChargePointMaxProfile 压过一切，即便更具体的 profile 要得更多。"""
        ps = [self.make(1, 0, "TxProfile", 32.0), self.make(2, 0, "ChargePointMaxProfile", 20.0)]
        self.assertEqual(P.effective_limit(ps, 0), 20.0)

    def test_no_profiles_means_no_limit(self):
        self.assertIsNone(P.effective_limit([], 0))

    def test_clamp(self):
        self.assertEqual(P.clamp(32.0, 16.0), 16.0)
        self.assertEqual(P.clamp(10.0, 16.0), 10.0)
        self.assertEqual(P.clamp(32.0, None), 32.0)


# ----------------------------------------------------------------------
# TODO(you) — add these once you implement the TODOs in cp/profile.py
# TODO(你) — 在你实现 cp/profile.py 的 TODO 之后补上这些测试
# ----------------------------------------------------------------------
# * test_recurring_daily_night_tariff  — 22:00-06:00 only, 11 kW
# * test_watts_to_amps_single_phase    — 11040 W @ 230 V 1-phase == 48 A
# * test_below_min_charging_rate_suspends
# * test_clear_charging_profile_by_purpose


if __name__ == "__main__":
    unittest.main()
