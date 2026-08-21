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
from datetime import datetime, timezone

# Nominal phase voltage assumed when converting between W and A.
# W <-> A 换算时假设的相电压。
#
# State this explicitly: a charging profile carries a number and a unit, but
# never a voltage. Guessing wrong here is how a station ends up over-drawing.
# 必须显式写出来: 充电曲线只带一个数和一个单位，从不带电压。这里猜错，
# 就是充电桩过流的来源。
#
# 230 V is the European single-phase nominal. For three-phase, multiply by the
# phase count: 3 x 230 V x 16 A = 11.04 kW (equivalently sqrt(3) x 400 V x 16 A).
# 230 V 是欧洲单相额定值。三相时乘以相数: 3 x 230 V x 16 A = 11.04 kW
# (等价于 sqrt(3) x 400 V x 16 A)。
NOMINAL_VOLTAGE_V = 230.0

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
    start_schedule: datetime | None = None   # 第 3 层: chargingSchedule.startSchedule
    recurrency_kind: str | None = None       # 第 2 层: Daily | Weekly
    valid_from: datetime | None = None       # 第 2 层
    valid_to: datetime | None = None         # 第 2 层


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


def _dt(value) -> datetime | None:
    """ISO 8601 string -> naive-UTC datetime.
    ISO 8601 字符串 -> naive-UTC datetime。

    Everything inside this module is naive UTC. A CSMS may or may not put an
    offset on `startSchedule`; mixing an aware and a naive datetime raises
    TypeError on subtraction, which would crash the control loop mid-charge.
    Normalising here means `_elapsed_for` never has to think about it.
    本模块内部一律使用 naive-UTC。后台下发的 startSchedule 可能带时区也可能不带，
    而 aware 和 naive 相减会抛 TypeError —— 那会在充电途中让控制循环崩溃。
    在这里归一化，`_elapsed_for` 就不用操心时区。
    """
    if value is None:
        return value
    dt = value if isinstance(value, datetime) else \
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


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
        # NOTE the nesting levels / 注意层级:
        #   startSchedule 在 chargingSchedule 里(第 3 层)
        #   recurrencyKind / validFrom / validTo 在 csChargingProfiles 里(第 2 层)
        start_schedule=_dt(_get(schedule, "startSchedule", "start_schedule")),
        recurrency_kind=_get(cs_charging_profiles, "recurrencyKind", "recurrency_kind"),
        valid_from=_dt(_get(cs_charging_profiles, "validFrom", "valid_from")),
        valid_to=_dt(_get(cs_charging_profiles, "validTo", "valid_to")),
    )

def _elapsed_for(profile, tx_elapsed_s, now) -> float | None:
      """把三种 kind 折算成统一的「相对计划起点的秒数」。
      Relative  → 事务开始到现在
      Absolute  → startSchedule 到现在
      Recurring → 当前周期起点到现在（取模）
      """
      if profile.kind == "Relative":
          return tx_elapsed_s
      if profile.kind == "Absolute":
          if profile.start_schedule is None:
              return None
          return (now - profile.start_schedule).total_seconds()
      if profile.kind == "Recurring":
          period = 86400 if profile.recurrency_kind == "Daily" else 604800
          anchor = profile.start_schedule          # 周期锚点
          return (now - anchor).total_seconds() % period    # ← 取模，就是"每天重来"
      return None

def _active_period(profile: Profile, tx_elapsed_s: float, now=None) -> Period | None:
    """Limit enforced by ONE profile `elapsed_s` seconds into the transaction.
    单条曲线在事务开始 `elapsed_s` 秒时强制的上限。

    Returns None when the schedule has expired (past `duration`) or has not
    started yet.
    当计划已过期(超出 duration)或尚未开始时返回 None。
    """
    if now is not None:
          if profile.valid_from and now < profile.valid_from: return None
          if profile.valid_to   and now > profile.valid_to:   return None

    elapsed = _elapsed_for(profile, tx_elapsed_s, now)
    if elapsed is None:
        return None


    if not profile.periods:
        return None
    if profile.duration is not None and elapsed >= profile.duration:
        return None
    if elapsed < profile.periods[0].start_period:
        return None

    # The active period is the LAST one whose startPeriod is already reached.
    # 生效周期是最后一个 startPeriod 已经到达的周期。
    active = profile.periods[0]
    for p in profile.periods:
        if p.start_period <= elapsed:
            active = p
        else:
            break
    return active


def limit_at(profile: Profile, tx_elapsed_s: float, now=None) -> float | None:
    """Limit enforced by ONE profile, in that profile's own unit.
    单条曲线强制的上限，单位是曲线自己的单位。"""
    active = _active_period(profile, tx_elapsed_s, now)
    return None if active is None else active.limit


def _convert(value: float, from_unit: str, to_unit: str,
             phases: int | None, voltage: float) -> float:
    """Convert a charging rate between watts and amps.
    在瓦特和安培之间换算充电速率。

        P = U * I * numberPhases

    `phases` defaults to 1 when the period does not say. Defaulting to 3 would
    inflate an amps limit threefold — the dangerous direction, so we do not.
    period 没写 numberPhases 时按单相。默认成三相会把安培上限放大三倍 ——
    那是危险的方向，所以不这么做。
    """
    if from_unit == to_unit:
        return value
    n = phases or 1
    if from_unit == "W" and to_unit == "A":
        return value / (voltage * n)
    if from_unit == "A" and to_unit == "W":
        return value * voltage * n
    raise ValueError(f"cannot convert {from_unit} -> {to_unit}")


def _resolve(profiles: list[Profile], elapsed_s: float, unit: str,
             voltage: float, now: datetime | None
             ) -> tuple[float, Profile, Period] | None:
    """Pick the governing profile and its limit, converted into `unit`.
    选出生效的曲线及其上限，并换算到 `unit`。

    Returns (limit, winning_profile, its_active_period), or None when no
    profile applies right now.
    返回 (上限, 生效的曲线, 该曲线当前生效的周期)，当前没有曲线适用时返回 None。

    Both `effective_limit` and `must_suspend` need this. Keeping the
    selection in one place means the two can never disagree about which
    profile is in charge.
    `effective_limit` 和 `must_suspend` 都需要它。把"选谁"集中在一处，
    两者就不可能对"哪条曲线说了算"产生分歧。

    Rule / 规则:
        1. Group by purpose. Within a purpose, the highest `stackLevel` wins.
           按 purpose 分组，组内 stackLevel 最高者生效。
        2. Across purposes, the more specific purpose wins, but
           ChargePointMaxProfile always acts as a hard ceiling on top.
           跨 purpose 时更具体者生效，但 ChargePointMaxProfile 始终作为硬上限。

    `now` must be forwarded all the way down: an Absolute or Recurring
    schedule is meaningless without wall-clock time.
    `now` 必须一路传到底: Absolute / Recurring 曲线离开墙钟时间就没有意义。
    """
    winners: dict[str, tuple[int, float, Profile, Period]] = {}
    for p in profiles:
        active = _active_period(p, elapsed_s, now)
        if active is None:
            continue

        # Convert into the caller's unit before comparing anything.
        # 比较之前先统一到调用方要求的单位。
        #
        # The profile's unit is what the CSMS chose; `unit` is what this
        # station reasons in (amps for AC, watts for DC). They are unrelated,
        # and silently skipping a mismatch means the station ignores a limit
        # the backend did send.
        # 曲线的单位由后台决定, `unit` 是本桩内部使用的单位(交流用安培,
        # 直流用瓦特)。两者无关, 静默跳过不匹配的曲线, 等于桩无视了后台
        # 确实下发过的限制。
        value = _convert(active.limit, p.unit, unit, active.number_phases, voltage)
        best = winners.get(p.purpose)
        if best is None or p.stack_level > best[0]:
            winners[p.purpose] = (p.stack_level, value, p, active)

    if not winners:
        return None

    # Most specific purpose that produced a value.  产生了值的最具体 purpose。
    chosen_purpose = max(winners, key=lambda k: PURPOSE_RANK.get(k, -1))
    _, limit, winner, active = winners[chosen_purpose]

    # ChargePointMaxProfile is a ceiling, never a floor.
    # ChargePointMaxProfile 只做上限，不做下限。
    #
    # The ceiling can lower the number, but it does not become "the profile
    # in charge" — minChargingRate still belongs to the chosen purpose.
    # 天花板可以压低数值，但它不因此成为"说了算的那条曲线"——
    # minChargingRate 仍然属于胜出的那个 purpose。
    ceiling = winners.get("ChargePointMaxProfile")
    if ceiling is not None:
        limit = min(limit, ceiling[1])
    return limit, winner, active


def effective_limit(profiles: list[Profile], elapsed_s: float, unit: str = "A",
                    voltage: float = NOMINAL_VOLTAGE_V,
                    now: datetime | None = None) -> float | None:
    """Combine all installed profiles into one number, in `unit`.
    把所有已安装的曲线合并成一个数，单位是 `unit`。"""
    resolved = _resolve(profiles, elapsed_s, unit, voltage, now)
    return None if resolved is None else resolved[0]


def must_suspend(profiles: list[Profile], elapsed_s: float, unit: str = "A",
                 voltage: float = NOMINAL_VOLTAGE_V,
                 now: datetime | None = None) -> bool:
    """True when the effective limit is below what charging is worth.
    当生效上限低到不值得充电时返回 True。

    OCPP 1.6 `minChargingRate` is the lowest rate the EV supports. Below it
    many BMSs refuse to start at all, and a station that trickles anyway
    just cycles its contactors while the driver waits for nothing. The
    station should report SuspendedEVSE instead, so the backend and the
    driver both learn that no useful power is available right now.
    OCPP 1.6 的 minChargingRate 是车支持的最低速率。低于它，很多 BMS 根本
    不启动充电; 桩若仍然涓流, 只是在空耗接触器寿命而车主白等。桩应该上报
    SuspendedEVSE, 让后台和车主都知道当前给不出有意义的功率。

    Returns False when nothing applies: no profile, or a profile that does
    not declare a minimum. The field is optional, and absent means "no
    lower bound", not "zero".
    没有曲线、或曲线没声明最低值时返回 False。该字段可选，缺省表示
    "没有下限"，不是"下限为零"。
    """
    resolved = _resolve(profiles, elapsed_s, unit, voltage, now)
    if resolved is None:
        return False
    limit, winner, active = resolved

    min_rate = winner.min_charging_rate
    if min_rate is None:
        return False

    # minChargingRate sits next to chargingRateUnit inside chargingSchedule,
    # so it carries the PROFILE's unit — convert it too, or a watts profile
    # compares amps against watts and suspends forever.
    # minChargingRate 与 chargingRateUnit 同在 chargingSchedule 里，
    # 用的是**曲线自己的**单位 —— 也必须换算，否则瓦特曲线会拿安培和瓦特
    # 相比，永远挂起。
    min_rate = _convert(min_rate, winner.unit, unit, active.number_phases, voltage)
    return limit < min_rate


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
# 2. Unit conversion W <-> A.  --- DONE / 已完成 ---
#    See `_convert()` and `effective_limit(..., voltage=)`.
#    A CSMS may send W while the station reasons in A. Convert with
#    P = U * I * numberPhases (and sqrt(3) for 3-phase line-to-line).
#    State your assumed nominal voltage explicitly — getting this wrong is
#    how real stations end up over-drawing.
#    后台可能下发 W 而桩内部按 A 计算。用 P = U * I * numberPhases 换算
#    (三相线电压还要乘 sqrt(3))。务必显式写明假设的额定电压 —— 这里搞错
#    正是真实充电桩过流的常见原因。
#
# 3. minChargingRate.  --- DONE / 已完成 ---
#    See `must_suspend()`. Still to wire into charge_point.meter_loop().
#    If the effective limit falls below minChargingRate the station should
#    suspend (StatusNotification -> SuspendedEVSE) rather than trickle.
#    若生效上限低于 minChargingRate，桩应该挂起(上报 SuspendedEVSE)
#    而不是涓流充电。
#
# 4. ClearChargingProfile.
#    Implement removal by id / connector / purpose / stackLevel filter.
#    实现按 id / 接口 / purpose / stackLevel 过滤的删除逻辑。
