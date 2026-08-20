"""
Smart-charging profile evaluation (OCPP 1.6 SetChargingProfile).
智能充电曲线求值 (OCPP 1.6 SetChargingProfile)。

This is the highest-value file in Stage 1.
这是阶段 1 中含金量最高的文件。

The interview question it answers / 它回答的面试题:
    "A CSMS tells the station the grid can only give 50 kW. How does that
     number reach the car?"
    "后台告诉充电桩电网只能给 50 kW。这个数字是怎么传到车上的?"

    CSMS --SetChargingProfile--> station.  The station stores the profile,
    evaluates it every control cycle, and clamps whatever the EV asked for.
    Under ISO 15118 the clamped value is what goes back in
    CurrentDemandRes.EVSEMaximumCurrentLimit.
    后台下发 SetChargingProfile，桩存下曲线，每个控制周期求值一次，
    并对车请求的功率做上限截断。在 ISO 15118 里，这个截断后的值就是
    CurrentDemandRes.EVSEMaximumCurrentLimit 回给车的内容。

No I/O here on purpose — everything is unit-testable.
这里刻意不做任何 I/O，全部可单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass

# Precedence defined by OCPP 1.6 §3.13. Higher number wins.
# OCPP 1.6 §3.13 定义的优先级，数字越大优先级越高。
PURPOSE_RANK = {
    "ChargePointMaxProfile": 0,  # station-wide ceiling   | 全桩上限
    "TxDefaultProfile": 1,       # default for any tx     | 任意事务的默认值
    "TxProfile": 2,              # this transaction only  | 仅当前事务
}


@dataclass(frozen=True)
class Period:
    """One `chargingSchedulePeriod` entry.  一条 chargingSchedulePeriod。"""

    start_period: int  # seconds after schedule start | 相对计划起点的秒数
    limit: float       # in chargingRateUnit          | 单位见 chargingRateUnit
    number_phases: int | None = None


@dataclass(frozen=True)
class Profile:
    """A flattened `csChargingProfiles` object.  扁平化后的 csChargingProfiles。"""

    profile_id: int
    stack_level: int
    purpose: str                 # ChargePointMaxProfile | TxDefaultProfile | TxProfile
    kind: str                    # Absolute | Recurring | Relative
    unit: str                    # "W" or "A"
    periods: tuple[Period, ...]
    duration: int | None = None  # schedule length in seconds | 计划总时长(秒)
    min_charging_rate: float | None = None


def _get(d: dict, *names, default=None):
    """Read a key that may arrive in camelCase or snake_case.
    读取一个可能是 camelCase 也可能是 snake_case 的键。

    Why / 为什么:
        OCPP-J puts camelCase on the wire, but the `ocpp` Python library
        snake_cases every nested key before handing the payload to a handler.
        A raw JSON log you replay later still has camelCase. Tolerating both
        means the same parser works live and offline.
        OCPP-J 线上传的是 camelCase，但 `ocpp` 这个 Python 库在把 payload
        交给 handler 之前会把所有嵌套键转成 snake_case。而你之后回放的原始
        JSON 日志仍然是 camelCase。两种都兼容，同一个解析器就能既在线用、
        也离线用。
    """
    for n in names:
        if n in d:
            return d[n]
    return default


def parse(cs_charging_profiles: dict) -> Profile:
    """Turn a `csChargingProfiles` object into a `Profile`.
    把 csChargingProfiles 对象转成 `Profile`。"""
    schedule = _get(cs_charging_profiles, "chargingSchedule", "charging_schedule")
    if schedule is None:
        raise KeyError("chargingSchedule")

    raw_periods = _get(schedule, "chargingSchedulePeriod", "charging_schedule_period", default=[])
    periods = tuple(
        Period(
            start_period=int(_get(p, "startPeriod", "start_period", default=0)),
            limit=float(p["limit"]),
            number_phases=_get(p, "numberPhases", "number_phases"),
        )
        for p in sorted(
            raw_periods,
            key=lambda p: _get(p, "startPeriod", "start_period", default=0),
        )
    )
    return Profile(
        profile_id=int(_get(cs_charging_profiles, "chargingProfileId", "charging_profile_id")),
        stack_level=int(_get(cs_charging_profiles, "stackLevel", "stack_level", default=0)),
        purpose=_get(cs_charging_profiles, "chargingProfilePurpose", "charging_profile_purpose"),
        kind=_get(cs_charging_profiles, "chargingProfileKind", "charging_profile_kind"),
        unit=_get(schedule, "chargingRateUnit", "charging_rate_unit", default="A"),
        periods=periods,
        duration=schedule.get("duration"),
        min_charging_rate=_get(schedule, "minChargingRate", "min_charging_rate"),
    )


def limit_at(profile: Profile, elapsed_s: float) -> float | None:
    """Limit enforced by ONE profile `elapsed_s` seconds into the transaction.
    单条曲线在事务开始 `elapsed_s` 秒时强制的上限。

    Returns None when the schedule has expired (past `duration`) or has not
    started yet.
    当计划已过期(超出 duration)或尚未开始时返回 None。
    """
    if not profile.periods:
        return None
    if profile.duration is not None and elapsed_s >= profile.duration:
        return None
    if elapsed_s < profile.periods[0].start_period:
        return None

    # The active period is the LAST one whose startPeriod is already reached.
    # 生效周期是最后一个 startPeriod 已经到达的周期。
    active = profile.periods[0]
    for p in profile.periods:
        if p.start_period <= elapsed_s:
            active = p
        else:
            break
    return active.limit


def effective_limit(profiles: list[Profile], elapsed_s: float, unit: str = "A") -> float | None:
    """Combine all installed profiles into one number.
    把所有已安装的曲线合并成一个数。

    Rule / 规则:
        1. Group by purpose. Within a purpose, the highest `stackLevel` wins.
           按 purpose 分组，组内 stackLevel 最高者生效。
        2. Across purposes, the more specific purpose wins, but
           ChargePointMaxProfile always acts as a hard ceiling on top.
           跨 purpose 时更具体者生效，但 ChargePointMaxProfile 始终作为硬上限。
    """
    winners: dict[str, tuple[int, float]] = {}
    for p in profiles:
        if p.unit != unit:
            # TODO(you): implement W <-> A conversion, see below.
            # TODO(你): 实现 W <-> A 换算，见文末。
            continue
        value = limit_at(p, elapsed_s)
        if value is None:
            continue
        best = winners.get(p.purpose)
        if best is None or p.stack_level > best[0]:
            winners[p.purpose] = (p.stack_level, value)

    if not winners:
        return None

    # Most specific purpose that produced a value.  产生了值的最具体 purpose。
    chosen_purpose = max(winners, key=lambda k: PURPOSE_RANK.get(k, -1))
    limit = winners[chosen_purpose][1]

    # ChargePointMaxProfile is a ceiling, never a floor.
    # ChargePointMaxProfile 只做上限，不做下限。
    ceiling = winners.get("ChargePointMaxProfile")
    if ceiling is not None:
        limit = min(limit, ceiling[1])
    return limit


def clamp(requested: float, limit: float | None) -> float:
    """What the station actually delivers.  桩实际输出的值。"""
    if limit is None:
        return requested
    return min(requested, limit)


# ----------------------------------------------------------------------
# TODO(you) — Stage 1, day 3-4.  Each item is a real interview talking point.
# TODO(你) — 阶段 1 第 3-4 天。每一项都是真实的面试谈资。
# ----------------------------------------------------------------------
# 1. kind == "Recurring" (recurrencyKind Daily / Weekly).
#    `limit_at` currently treats every profile as Relative/Absolute. Add
#    `validFrom` / `validTo` and fold wall-clock time into the period lookup.
#    Test it with a profile that only allows 11 kW between 22:00 and 06:00.
#    目前 `limit_at` 把所有曲线都当作 Relative/Absolute 处理。请加入
#    validFrom / validTo，并把墙钟时间折算进周期查找。用一条"仅 22:00-06:00
#    允许 11 kW"的曲线来测试。
#
# 2. Unit conversion W <-> A.
#    A CSMS may send W while the station reasons in A. Convert with
#    P = U * I * numberPhases (and sqrt(3) for 3-phase line-to-line).
#    State your assumed nominal voltage explicitly — getting this wrong is
#    how real stations end up over-drawing.
#    后台可能下发 W 而桩内部按 A 计算。用 P = U * I * numberPhases 换算
#    (三相线电压还要乘 sqrt(3))。务必显式写明假设的额定电压 —— 这里搞错
#    正是真实充电桩过流的常见原因。
#
# 3. minChargingRate.
#    If the effective limit falls below minChargingRate the station should
#    suspend (StatusNotification -> SuspendedEVSE) rather than trickle.
#    若生效上限低于 minChargingRate，桩应该挂起(上报 SuspendedEVSE)
#    而不是涓流充电。
#
# 4. ClearChargingProfile.
#    Implement removal by id / connector / purpose / stackLevel filter.
#    实现按 id / 接口 / purpose / stackLevel 过滤的删除逻辑。
