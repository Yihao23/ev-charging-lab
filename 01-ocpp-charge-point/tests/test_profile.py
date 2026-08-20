"""Unit tests for smart-charging evaluation.  智能充电求值的单元测试。

Run / 运行:  python -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime

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


class TestRecurring(unittest.TestCase):
    """Daily recurring tariff: cheap between 22:00 and 06:00.
    每日重复电价：22:00-06:00 走优惠(限流)，其余时段不限。

    Assumed API / 假设的接口:
        limit_at(profile, tx_elapsed_s, now=datetime) -> float | None
    A Recurring profile does not depend on how long the transaction has run,
    so `tx_elapsed_s` is passed as 0.
    Recurring 曲线与事务已经跑了多久无关，所以 tx_elapsed_s 传 0。

    Limits are in amps, three-phase assumed: 3 x 230 V x 16 A = 11.04 kW.
    限值单位是安培，假设三相：3 x 230 V x 16 A = 11.04 kW。
    Say the assumption out loud — an unstated phase count is how real
    stations end up over-drawing.
    假设要写出来 —— 不写清几相，正是真实充电桩过流的来源。
    """

    # One day = 86400 s. startPeriod is seconds-of-day from the anchor.
    # 一天 86400 秒。startPeriod 是相对锚点的"当天第几秒"。
    NIGHT_TARIFF = {
        "chargingProfileId": 200,
        "stackLevel": 1,
        "chargingProfilePurpose": "TxProfile",
        "chargingProfileKind": "Recurring",
        "recurrencyKind": "Daily",
        "chargingSchedule": {
            "startSchedule": "2026-08-20T00:00:00",   # 周期锚点：某天 00:00
            "chargingRateUnit": "A",
            "chargingSchedulePeriod": [
                {"startPeriod": 0,     "limit": 16.0},   # 00:00-06:00  夜间优惠
                {"startPeriod": 21600, "limit": 32.0},   # 06:00-22:00  日间
                {"startPeriod": 79200, "limit": 16.0},   # 22:00-24:00  夜间优惠
            ],
        },
    }

    def setUp(self):
        self.profile = P.parse(self.NIGHT_TARIFF)

    def _limit(self, now):
        return P.limit_at(self.profile, 0, now=now)

    def test_parses_recurring_metadata(self):
        """kind / recurrencyKind / startSchedule must survive parsing.
        三个字段必须被解析出来 —— 注意它们不在同一层。"""
        self.assertEqual(self.profile.kind, "Recurring")
        self.assertEqual(self.profile.recurrency_kind, "Daily")
        self.assertEqual(self.profile.start_schedule, datetime(2026, 8, 20, 0, 0, 0))

    def test_evening_peak_is_unrestricted(self):
        self.assertEqual(self._limit(datetime(2026, 8, 20, 21, 59)), 32.0)

    def test_night_tariff_applies(self):
        self.assertEqual(self._limit(datetime(2026, 8, 20, 22, 1)), 16.0)

    def test_wraps_across_midnight(self):
        """05:59 the next day is still the night tariff.
        次日 05:59 仍是夜间价 —— 这条验证 % 86400 写对了。

        The two tests above could pass by accident even if `kind` were
        ignored. This one cannot.
        上面两条就算完全不处理 kind 也可能碰巧通过，这一条不会。
        """
        self.assertEqual(self._limit(datetime(2026, 8, 21, 5, 59)), 16.0)

    def test_morning_returns_to_normal(self):
        self.assertEqual(self._limit(datetime(2026, 8, 21, 6, 1)), 32.0)

    def test_boundaries(self):
        """Which side of 06:00 and 22:00 each instant falls on.
        06:00 和 22:00 整点归哪一段 —— 边界值最容易写错。"""
        cases = [
            (datetime(2026, 8, 20,  5, 59, 59), 16.0),
            (datetime(2026, 8, 20,  6,  0,  0), 32.0),
            (datetime(2026, 8, 20, 21, 59, 59), 32.0),
            (datetime(2026, 8, 20, 22,  0,  0), 16.0),
        ]
        for now, expected in cases:
            with self.subTest(now=now.strftime("%H:%M:%S")):
                self.assertEqual(self._limit(now), expected)

    def test_a_week_later_still_works(self):
        """A Daily profile keeps working far from its anchor.
        Daily 曲线离锚点一周之后照样生效，不会因为"过期"而失效。"""
        self.assertEqual(self._limit(datetime(2026, 8, 27, 23, 0)), 16.0)

    def test_effective_limit_forwards_now(self):
        """RED until `effective_limit` accepts and forwards `now`.
        在 `effective_limit` 接受并转发 `now` 之前，这条是红的。

        Why this test exists / 为什么需要这条:
            The unit tests above call `limit_at` directly, so they pass.
            But `charge_point.py` goes through `effective_limit`, which
            currently drops `now` — a Recurring profile crashes on a real
            station with `TypeError: unsupported operand type(s) for -:
            'NoneType' and 'datetime.datetime'`.
            上面几条直接调 limit_at 所以是绿的，但 charge_point.py 走的是
            effective_limit，它现在把 now 丢掉了 —— 真桩上跑 Recurring 会崩。
            单元测试绿 != 系统能跑。

        Assumed API / 假设的接口:
            effective_limit(profiles, elapsed_s, unit="A", now=None)
        """
        night = datetime(2026, 8, 20, 22, 1)
        day = datetime(2026, 8, 20, 12, 0)
        self.assertEqual(P.effective_limit([self.profile], 0, now=night), 16.0)
        self.assertEqual(P.effective_limit([self.profile], 0, now=day), 32.0)



class TestUnitConversion(unittest.TestCase):
    """TODO 2 — W <-> A conversion.  RED until `effective_limit` converts.
    TODO 2 —— W <-> A 换算。在 effective_limit 实现换算之前是红的。

    Today `effective_limit` does `if p.unit != unit: continue`, so a profile
    sent in watts is silently dropped and the station delivers whatever the
    car asked for. A 3680 W cap becomes 32 A = 7360 W — double.
    现在 effective_limit 遇到单位不匹配直接 continue，瓦特曲线被静默丢弃，
    桩按车的请求全量输出。3680 W 的上限变成 32 A = 7360 W，翻倍。

    Assumed API / 假设的接口:
        effective_limit(profiles, elapsed_s, unit="A", now=None, voltage=230.0)
    Conversion / 换算:
        1-phase : P = U * I
        3-phase : P = 3 * U_phase * I   (== sqrt(3) * U_line * I)
    `numberPhases` comes from the period; assume 1 when absent.
    numberPhases 取自 period，缺省按单相处理。
    """

    NOMINAL_V = 230.0

    @staticmethod
    def make(unit, limit, phases=None):
        period = {"startPeriod": 0, "limit": limit}
        if phases is not None:
            period["numberPhases"] = phases
        return P.parse({
            "chargingProfileId": 1, "stackLevel": 0,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Relative",
            "chargingSchedule": {"chargingRateUnit": unit,
                                 "chargingSchedulePeriod": [period]},
        })

    def test_watts_to_amps_single_phase(self):
        """3680 W @ 230 V 1-phase == 16 A."""
        w = self.make("W", 3680.0, phases=1)
        self.assertEqual(P.effective_limit([w], 0, unit="A"), 16.0)

    def test_watts_to_amps_three_phase(self):
        """11040 W @ 230 V 3-phase == 16 A.  三相同样的电流，功率是三倍。"""
        w = self.make("W", 11040.0, phases=3)
        self.assertEqual(P.effective_limit([w], 0, unit="A"), 16.0)

    def test_missing_number_phases_defaults_to_single(self):
        """No numberPhases -> assume 1 phase. Assuming 3 would UNDER-limit
        the station by 3x, which is the dangerous direction.
        没有 numberPhases 就按单相。按三相会把限值放大 3 倍 —— 那是危险的方向。"""
        w = self.make("W", 3680.0)
        self.assertEqual(P.effective_limit([w], 0, unit="A"), 16.0)

    def test_amps_to_watts(self):
        """Conversion must work the other way too.  反方向也要能换算。"""
        a = self.make("A", 16.0, phases=1)
        self.assertEqual(P.effective_limit([a], 0, unit="W"), 3680.0)

    def test_matching_units_are_untouched(self):
        """No conversion when the units already match.  单位一致时不做任何换算。"""
        a = self.make("A", 16.0)
        self.assertEqual(P.effective_limit([a], 0, unit="A"), 16.0)

    def test_mixed_units_are_compared_correctly(self):
        """A watts profile and an amps profile must compete on equal terms.
        瓦特曲线和安培曲线必须能公平比较 —— 这正是静默跳过所掩盖的问题。

        3680 W == 16 A, and a 25 A station-max ceiling is higher, so 16 A wins.
        """
        w = self.make("W", 3680.0, phases=1)
        ceiling = P.parse({
            "chargingProfileId": 2, "stackLevel": 0,
            "chargingProfilePurpose": "ChargePointMaxProfile",
            "chargingProfileKind": "Relative",
            "chargingSchedule": {"chargingRateUnit": "A",
                                 "chargingSchedulePeriod": [
                                     {"startPeriod": 0, "limit": 25.0}]},
        })
        self.assertEqual(P.effective_limit([w, ceiling], 0, unit="A"), 16.0)


class TestMinChargingRate(unittest.TestCase):
    """TODO 3 — minChargingRate.  RED until the check exists.
    TODO 3 —— 最低充电电流。在实现检查之前是红的。

    `min_charging_rate` is parsed today but never read. If the effective
    limit drops below it, the station must suspend (StatusNotification ->
    SuspendedEVSE) rather than trickle: many BMSs refuse to start at a very
    low current, and repeated start/stop wears out the contactors.
    min_charging_rate 现在只被解析、从没被读过。若生效上限低于它，桩应该挂起
    并上报 SuspendedEVSE，而不是涓流充电 —— 很多 BMS 在极低电流下根本不启动，
    反复启停还会磨损接触器。

    Assumed API / 假设的接口:
        must_suspend(profiles, elapsed_s, unit="A", now=None) -> bool
    A separate pure function keeps `effective_limit`'s contract intact
    (it still returns "the limit", never a magic sentinel).
    单独一个纯函数，这样 effective_limit 的契约不变 —— 它永远返回"上限"，
    不返回魔法哨兵值。
    """

    @staticmethod
    def make(limit, min_rate=None):
        sched = {"chargingRateUnit": "A",
                 "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit}]}
        if min_rate is not None:
            sched["minChargingRate"] = min_rate
        return P.parse({
            "chargingProfileId": 1, "stackLevel": 0,
            "chargingProfilePurpose": "TxProfile",
            "chargingProfileKind": "Relative",
            "chargingSchedule": sched,
        })

    def test_parses_min_charging_rate(self):
        """This one is already GREEN — parse reads it, nothing uses it.
        这条已经是绿的 —— parse 读到了，只是没人用。"""
        self.assertEqual(self.make(6.0, min_rate=10.0).min_charging_rate, 10.0)

    def test_below_min_charging_rate_suspends(self):
        """limit 6 A < minChargingRate 10 A -> suspend."""
        p = self.make(6.0, min_rate=10.0)
        self.assertTrue(P.must_suspend([p], 0))

    def test_at_min_charging_rate_does_not_suspend(self):
        """Exactly at the minimum is still acceptable.  正好等于最低值仍可充电。"""
        p = self.make(10.0, min_rate=10.0)
        self.assertFalse(P.must_suspend([p], 0))

    def test_above_min_charging_rate_does_not_suspend(self):
        p = self.make(16.0, min_rate=10.0)
        self.assertFalse(P.must_suspend([p], 0))

    def test_no_min_charging_rate_never_suspends(self):
        """The field is optional; absent means no lower bound.
        该字段可选，缺省表示没有下限。"""
        p = self.make(6.0)
        self.assertFalse(P.must_suspend([p], 0))

    def test_no_profiles_never_suspends(self):
        self.assertFalse(P.must_suspend([], 0))


# * test_watts_to_amps_single_phase    — 11040 W @ 230 V 1-phase == 48 A
# * test_below_min_charging_rate_suspends
# * test_clear_charging_profile_by_purpose


if __name__ == "__main__":
    unittest.main()
