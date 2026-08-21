"""
An OCPP 1.6-J charge point that actually behaves like one.
一个行为接近真实的 OCPP 1.6-J 充电桩。

Run / 运行:
    python -m cp.charge_point --csms ws://localhost:9000 --id CP_1

What it does / 它做了什么:
    BootNotification -> Heartbeat loop -> StatusNotification on every state
    change -> Authorize -> StartTransaction -> periodic MeterValues whose
    power is clamped by any SetChargingProfile the CSMS pushed -> StopTransaction.
    上电报到 -> 心跳循环 -> 每次状态变化上报 StatusNotification -> 鉴权 ->
    开启事务 -> 周期上报 MeterValues(功率受后台下发的 SetChargingProfile 截断)
    -> 结束事务。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import deque
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint
from ocpp.v16 import call, call_result
from ocpp.v16.enums import (
    Action,
    ChargePointErrorCode,
    ChargePointStatus as S,
    ChargingProfileStatus,
    Measurand,
    RegistrationStatus,
    RemoteStartStopStatus,
    ResetStatus,
    UnitOfMeasure,
)

from cp import profile as profile_mod
from cp.state_machine import ConnectorStateMachine

LOG = logging.getLogger("cp")

# Simulated hardware constants.  模拟硬件常量。
NOMINAL_VOLTAGE_V = 230.0
EV_REQUESTED_CURRENT_A = 32.0   # what the car asks for | 车请求的电流
METER_INTERVAL_S = 5.0          # MeterValues cadence   | 电表上报周期

# Reconnect backoff. Doubles on every failure, capped, reset on success.
# 重连退避。每次失败翻倍，有上限，成功后重置。
#
# A flat retry interval is how a fleet takes down its own backend: ten
# thousand stations retrying every second is ten thousand requests per second
# against a CSMS that is already struggling.
# 固定间隔重试正是车队打垮自家后台的方式: 一万台桩每秒重试一次, 就是每秒
# 一万个请求砸向一个本来就在挣扎的后台。
INITIAL_BACKOFF_S = 1.0
MAX_BACKOFF_S = 60.0

# How many metering samples to hold while the link is down.
# 链路断开期间最多缓存多少条计量采样。
#
# At one sample every 5 s this is a bit over 8 hours of outage. A station
# offline longer than that has a problem no queue can fix, and unbounded
# growth would take the station down with an OOM — losing everything instead
# of the oldest part.
# 按 5 秒一条算，这是 8 小时多的断网。断得比这还久的桩有队列解决不了的问题，
# 而无上限增长会让桩因 OOM 死掉 —— 那是丢掉全部，而不是丢掉最旧的一部分。
MAX_OUTBOX = 6000


def utcnow() -> str:
    """OCPP timestamps must be ISO 8601 with a timezone.
    OCPP 时间戳必须是带时区的 ISO 8601。"""
    return datetime.now(timezone.utc).isoformat()


class ChargePoint(OcppChargePoint):
    """One simulated connector.  一个模拟接口。"""

    def __init__(self, cp_id: str, connection):
        super().__init__(cp_id, connection)
        self.sm = ConnectorStateMachine(connector_id=1)
        self.profiles: list[profile_mod.Profile] = []
        self.transaction_id: int | None = None
        self.tx_started_at: float = 0.0
        self.meter_wh: float = 0.0
        self.heartbeat_interval: int = 30
        self._stop = asyncio.Event()

        # Metering survives the link; sending does not.
        # 计量的寿命长于链路; 发送不是。
        self._online = False
        self.outbox: deque[dict] = deque()
        self._dropped = 0

    def rebind(self, connection) -> None:
        """Attach this station to a fresh WebSocket, keeping its state.
        把本桩挂到一条新的 WebSocket 上，保留全部状态。

        Everything that matters survives a reconnect: the meter reading, the
        transaction id, the installed profiles, the connector state. Building
        a new ChargePoint instead would zero the meter, and the first
        MeterValues after reconnect would report 0 Wh — the CSMS would bill
        the difference as if the energy had never been delivered.
        重连后必须存活的东西: 电表读数、事务 id、已安装的曲线、接口状态。
        改为新建一个 ChargePoint 会让电表归零, 重连后第一条 MeterValues 上报
        0 Wh —— 后台会按差值计费, 那段电量等于白送。

        Two things are per-connection and must NOT survive:
        两样东西属于单条连接，绝不能留到下一条:
          `_connection`     the dead socket itself | 已经死掉的 socket
          `_response_queue` replies to requests we will never match again.
                            The library does check `unique_id` and discards a
                            mismatch, but it does so one entry at a time and
                            against the NEW call's timeout budget, logging an
                            error for each. A queue left full of stale replies
                            can time out a legitimate response.
                            属于旧请求的响应。库确实会核对 unique_id 并丢弃不
                            匹配的，但它是逐条丢、而且花的是**新**请求的超时
                            预算，每丢一条还报一次错。残留够多时，一个本来
                            正常的响应就会被拖到超时。
        """
        self._connection = connection
        self._response_queue = asyncio.Queue()
        self._online = True

    # ---------------------------------------------------------------- #
    # Outgoing calls  (charge point -> CSMS)                            #
    # 主动发起的请求 (桩 -> 后台)                                        #
    # ---------------------------------------------------------------- #

    async def boot(self) -> None:
        resp = await self.call(
            call.BootNotification(
                charge_point_vendor="ev-charging-lab",
                charge_point_model="SimCP-1",
                firmware_version="0.1.0",
            )
        )
        LOG.info("BootNotification -> %s (interval=%ss)", resp.status, resp.interval)
        if resp.status != RegistrationStatus.accepted:
            raise SystemExit(f"CSMS rejected boot: {resp.status}")
        self.heartbeat_interval = resp.interval or 30

        # Report where we ACTUALLY are, not where a fresh station would be.
        # On the first boot that is Available; on a reconnect mid-session it
        # is Charging, and claiming Available would both lie to the CSMS and
        # be rejected by our own state machine as an illegal transition.
        # 上报我们**实际**所处的状态，而不是一台刚开机的桩会处于的状态。
        # 首次上电时它是 Available; 充电途中重连时它是 Charging，谎称
        # Available 既欺骗后台，也会被我们自己的状态机判为非法迁移。
        await self.notify_status(self.sm.state)

        # Anything buffered while the link was down goes out now, after the
        # CSMS has accepted this station. Sending it before the boot would be
        # rejected — the backend has not agreed to talk to us yet.
        # 断网期间缓存的东西现在补发，在后台接受本桩之后。上电报到之前发是会被
        # 拒的 —— 后台还没同意和我们对话。
        await self.flush_outbox()

    async def heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.heartbeat_interval)
            await self.call(call.Heartbeat())

    async def notify_status(self, target: S,
                            error_code: ChargePointErrorCode | None = None) -> None:
        """Transition, then tell the CSMS. Order matters — never report a
        state you failed to enter.
        先迁移再上报。顺序很重要 —— 绝不上报一个没能进入的状态。

        `error_code` is passed straight through to the state machine, where
        omitting it means "not reporting a code" and NoError means
        "explicitly reporting no fault" — the latter is what releases a
        latched fault. Defaulting to NoError here would quietly defeat the
        latch, because every status report would look like a reset.
        `error_code` 原样交给状态机: 省略表示"不报告错误码", NoError 表示
        "显式报告无故障" —— 后者才是解除故障闩锁的动作。若在这里默认成
        NoError, 每次状态上报都会被当成一次复位, 闩锁就形同虚设。

        OCPP requires every StatusNotification to carry an errorCode, so
        when the caller reports none we send the latched one.
        OCPP 要求每条 StatusNotification 都带 errorCode, 所以调用方没报
        错误码时, 我们把当前闩着的那个发出去。
        """
        self.sm.to(target, error_code=error_code)
        wire_code = error_code if error_code is not None else self.sm.error_code
        LOG.info("state -> %s (%s)", target.value, wire_code.value)
        await self.call(
            call.StatusNotification(
                connector_id=self.sm.connector_id,
                error_code=wire_code,
                status=target,
                timestamp=utcnow(),
            )
        )

    async def start_transaction(self, id_tag: str = "DEADBEEF") -> None:
        if self.transaction_id is not None:
            LOG.warning("transaction already running, ignoring start")
            return

        await self.notify_status(S.preparing)

        auth = await self.call(call.Authorize(id_tag=id_tag))
        status = auth.id_tag_info["status"]
        LOG.info("Authorize(%s) -> %s", id_tag, status)
        if status != "Accepted":
            await self.notify_status(S.available)
            return

        resp = await self.call(
            call.StartTransaction(
                connector_id=self.sm.connector_id,
                id_tag=id_tag,
                meter_start=int(self.meter_wh),
                timestamp=utcnow(),
            )
        )
        self.transaction_id = resp.transaction_id
        self.tx_started_at = asyncio.get_running_loop().time()
        LOG.info("StartTransaction -> transactionId=%s", self.transaction_id)
        await self.notify_status(S.charging)

    async def stop_transaction(self, reason: str = "Local") -> None:
        if self.transaction_id is None:
            return
        await self.notify_status(S.finishing)
        await self.call(
            call.StopTransaction(
                meter_stop=int(self.meter_wh),
                timestamp=utcnow(),
                transaction_id=self.transaction_id,
                reason=reason,
            )
        )
        LOG.info("StopTransaction(tx=%s, reason=%s)", self.transaction_id, reason)
        self.transaction_id = None
        await self.notify_status(S.available)

    async def meter_loop(self) -> None:
        """The control cycle. This is where smart charging becomes real.
        控制周期。智能充电在这里落地。"""
        while not self._stop.is_set():
            await asyncio.sleep(METER_INTERVAL_S)
            if self.transaction_id is None:
                continue

            elapsed = asyncio.get_running_loop().time() - self.tx_started_at
            # naive UTC, to match what profile._dt() produces.
            # naive UTC，与 profile._dt() 的产出保持一致。
            wall_clock = datetime.now(timezone.utc).replace(tzinfo=None)
            limit_a = profile_mod.effective_limit(self.profiles, elapsed,
                                                  unit="A", now=wall_clock)

            # Below the EV's minimum, trickling is worse than not charging:
            # many BMSs refuse to start, the ones that try cycle the
            # contactors, and the driver waits for nothing. Suspend and say
            # so, then resume when the limit recovers.
            # 低于车的最低充电电流时，涓流比不充更糟: 很多 BMS 根本不启动，
            # 会尝试的那些则在空耗接触器，而车主白等。挂起并明确上报，
            # 等限值回升后再恢复。
            suspended = profile_mod.must_suspend(self.profiles, elapsed,
                                                 unit="A", now=wall_clock)
            if suspended:
                actual_a = 0.0
                if self.sm.state is S.charging:
                    await self.notify_status(S.suspended_evse)
                    LOG.info("t=%5.0fs  limit %.1fA is below the EV minimum -> suspending",
                             elapsed, limit_a if limit_a is not None else -1)
            else:
                actual_a = profile_mod.clamp(EV_REQUESTED_CURRENT_A, limit_a)
                if self.sm.state is S.suspended_evse:
                    await self.notify_status(S.charging)
                    LOG.info("t=%5.0fs  limit recovered to %.1fA -> resuming",
                             elapsed, actual_a)

            power_w = actual_a * NOMINAL_VOLTAGE_V
            self.meter_wh += power_w * METER_INTERVAL_S / 3600.0

            # Keep metering while suspended: the backend still needs to see
            # that the station is alive and delivering nothing.
            # 挂起期间照常上报: 后台仍需要知道桩还活着、只是没在送电。
            if not suspended and limit_a is not None and actual_a < EV_REQUESTED_CURRENT_A:
                LOG.info(
                    "t=%5.0fs  EV wants %.1fA, profile caps at %.1fA -> delivering %.1fA (%.2f kW)",
                    elapsed, EV_REQUESTED_CURRENT_A, limit_a, actual_a, power_w / 1000,
                )

            # Freeze the sample here, at the moment it was taken. Replaying
            # a queued sample with the time of REPLAY would tell the CSMS
            # that a hundred samples happened in the same second and the
            # energy curve becomes meaningless.
            # 在采样的这一刻把时间戳固定下来。用**重放**时刻的时间发出去，
            # 等于告诉后台上百次采样发生在同一秒，电量曲线就废了。
            sample = {
                "timestamp": utcnow(),
                "sampledValue": [
                    {
                        "value": f"{self.meter_wh:.1f}",
                        "measurand": Measurand.energy_active_import_register,
                        "unit": UnitOfMeasure.wh,
                    },
                    {
                        "value": f"{power_w:.1f}",
                        "measurand": Measurand.power_active_import,
                        "unit": UnitOfMeasure.w,
                    },
                    {
                        "value": f"{actual_a:.1f}",
                        "measurand": Measurand.current_import,
                        "unit": UnitOfMeasure.a,
                    },
                ],
            }
            await self.send_meter_values(sample)

    # ---------------------------------------------------------------- #
    # Offline buffer  |  离线缓冲                                        #
    # ---------------------------------------------------------------- #

    async def send_meter_values(self, sample: dict) -> None:
        """Send one metering sample, or hold it until the link is back.
        发出一条计量采样，链路不通就先存着。

        The energy was physically delivered whether or not the network was
        up. Dropping the sample does not un-deliver it — it just means the
        CSMS never learns about it and nobody can bill for it.
        不管网通不通，那度电都已经物理上送出去了。丢掉采样并不能把电收回来,
        只是后台永远不知道有过这件事, 也就没人能为它计费。
        """
        if self._online:
            try:
                await self.call(call.MeterValues(
                    connector_id=self.sm.connector_id,
                    transaction_id=self.transaction_id,
                    meter_value=[sample],
                ))
                return
            except (websockets.exceptions.WebSocketException, OSError,
                    asyncio.TimeoutError):
                # The link died between the check and the send. Fall through
                # and queue it rather than losing it to the race.
                # 检查和发送之间链路死了。落到下面入队，别因为这个竞态丢数据。
                self._online = False

        self._enqueue(sample)

    def _enqueue(self, sample: dict) -> None:
        """Buffer a sample, dropping the OLDEST first when full.
        缓存一条采样，满了先丢**最旧**的。

        Newest-first would be easier, but the newest sample is the one that
        matters: the CSMS bills on the last reading it sees, so losing the
        tail costs money while losing the head only costs curve resolution.
        丢最新的更好写，但最新那条恰恰最重要: 后台按看到的最后一个读数计费,
        丢尾部是丢钱, 丢头部只是丢曲线的分辨率。
        """
        if len(self.outbox) >= MAX_OUTBOX:
            self.outbox.popleft()
            self._dropped += 1
            if self._dropped % 100 == 1:
                # Never truncate billing data silently.
                # 绝不静默丢弃计费数据。
                LOG.error("outbox full (%d), dropped %d oldest samples",
                          MAX_OUTBOX, self._dropped)
        self.outbox.append(sample)

        # Log at 1, 2, 4, 8, 16 ... so a short outage still shows its shape
        # and an eight-hour one costs 13 lines instead of 5760. What matters
        # is that the order of magnitude changed, not that one more arrived.
        # 在 1, 2, 4, 8, 16 ... 时打印: 短断网仍能看出过程, 八小时的断网只花
        # 13 行而不是 5760 行。值得报告的是"数量级变了", 不是"又多了一条"。
        #
        # `(n & (n - 1)) == 0` is true exactly for powers of two: n - 1
        # borrows the single set bit and fills the lower ones, so the AND
        # clears. The parentheses are not needed in Python, where `&` binds
        # tighter than `==`, but they are in C — and this loop is the kind
        # of thing that gets ported to firmware.
        # `(n & (n - 1)) == 0` 恰好在 n 是 2 的幂时成立: n-1 借走那唯一的 1
        # 并把低位填满, 相与即为 0。Python 里 `&` 优先级高于 `==`, 括号本可
        # 省略, 但 C 里不行 —— 而这类循环正是会被搬进固件的东西。
        n = len(self.outbox)
        if (n & (n - 1)) == 0:
            LOG.info("offline: %d samples buffered, meter=%.1fWh",
                     n, self.meter_wh)

    async def flush_outbox(self) -> None:
        """Replay buffered samples, oldest first, after BootNotification.
        重连并上电报到之后，按从旧到新的顺序重放缓存的采样。

        Each sample carries the timestamp of when it was TAKEN, so the CSMS
        reconstructs the real curve rather than a spike at replay time.
        每条采样都带着**采样那一刻**的时间戳，后台重建出的是真实曲线，
        而不是重放时刻的一根尖峰。

        `transaction_id` is filled in HERE, not when the sample was taken:
        if StartTransaction had not landed yet there was no id to record.
        `transaction_id` 是在**这里**才填的，不是采样时: 如果那时
        StartTransaction 还没送达，根本没有 id 可记。
        """
        if not self.outbox:
            return
        total = len(self.outbox)
        LOG.info("replaying %d buffered samples", total)
        sent = 0
        while self.outbox:
            sample = self.outbox[0]
            try:
                await self.call(call.MeterValues(
                    connector_id=self.sm.connector_id,
                    transaction_id=self.transaction_id,
                    meter_value=[sample],
                ))
            except (websockets.exceptions.WebSocketException, OSError,
                    asyncio.TimeoutError):
                # Link died mid-replay. Stop; the rest stays queued, in
                # order, for the next reconnect.
                # 重放中途断了。停下，剩下的按原顺序留在队列里等下次重连。
                self._online = False
                LOG.warning("replay interrupted after %d/%d", sent, total)
                return
            self.outbox.popleft()
            sent += 1
        LOG.info("replayed %d samples, meter=%.1fWh", sent, self.meter_wh)

    # ---------------------------------------------------------------- #
    # Incoming calls  (CSMS -> charge point)                            #
    # 被动接收的请求 (后台 -> 桩)                                        #
    # ---------------------------------------------------------------- #

    @on(Action.set_charging_profile)
    def on_set_charging_profile(self, connector_id, cs_charging_profiles, **kwargs):
        """The message this whole stage is about.  整个阶段 1 的核心消息。"""
        try:
            parsed = profile_mod.parse(cs_charging_profiles)
        except (KeyError, ValueError, TypeError) as exc:
            LOG.error("malformed charging profile: %s", exc)
            return call_result.SetChargingProfile(status=ChargingProfileStatus.rejected)

        # Replace any profile with the same id, then install.
        # 先移除同 id 的旧曲线，再安装新曲线。
        self.profiles = [p for p in self.profiles if p.profile_id != parsed.profile_id]
        self.profiles.append(parsed)
        LOG.info(
            "SetChargingProfile accepted: id=%s purpose=%s stack=%s periods=%s",
            parsed.profile_id, parsed.purpose, parsed.stack_level, len(parsed.periods),
        )
        return call_result.SetChargingProfile(status=ChargingProfileStatus.accepted)

    @on(Action.remote_start_transaction)
    def on_remote_start(self, id_tag, **kwargs):
        if self.transaction_id is not None:
            return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.rejected)
        asyncio.create_task(self.start_transaction(id_tag))
        return call_result.RemoteStartTransaction(status=RemoteStartStopStatus.accepted)

    @on(Action.remote_stop_transaction)
    def on_remote_stop(self, transaction_id, **kwargs):
        if transaction_id != self.transaction_id:
            return call_result.RemoteStopTransaction(status=RemoteStartStopStatus.rejected)
        asyncio.create_task(self.stop_transaction(reason="Remote"))
        return call_result.RemoteStopTransaction(status=RemoteStartStopStatus.accepted)

    @on(Action.reset)
    def on_reset(self, type, **kwargs):
        LOG.warning("Reset(%s) requested", type)
        return call_result.Reset(status=ResetStatus.accepted)

    # ------------------------------------------------------------------
    # TODO(you) — Stage 1, day 4
    # TODO(你) — 阶段 1 第 4 天
    # ------------------------------------------------------------------
    # 1. @on(Action.get_configuration) / change_configuration
    #    Back them with a real key/value store and honour readonly keys.
    #    用真实的键值存储实现，并遵守 readonly 键。
    # 2. @on(Action.trigger_message)
    #    Let the CSMS ask for an out-of-band StatusNotification / MeterValues.
    #    让后台可以主动索取一次 StatusNotification / MeterValues。
    # 3. @on(Action.clear_charging_profile) — see profile.py TODO 4.
    # 4. Offline behaviour: queue transaction messages while the WebSocket is
    #    down and replay them on reconnect. This is what separates a demo
    #    from a product, and CSMS operators will ask about it.
    #    离线行为: WebSocket 断开时把事务消息排队，重连后重放。这是 demo
    #    和产品的分水岭，后台运营方一定会问。


async def main() -> None:
    ap = argparse.ArgumentParser(description="Simulated OCPP 1.6 charge point")
    ap.add_argument("--csms", default="ws://localhost:9000", help="CSMS websocket base URL")
    ap.add_argument("--id", default="CP_1", help="charge point identity")
    ap.add_argument("--autostart", type=float, default=3.0,
                    help="seconds before starting a local transaction (0 = wait for RemoteStart)")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop the transaction after N seconds (0 = run forever)")
    ap.add_argument("--no-reconnect", action="store_true",
                    help="exit on the first disconnect, the pre-TODO-4 behaviour")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(name)-6s %(message)s")

    uri = f"{args.csms.rstrip('/')}/{args.id}"

    # The station outlives any single connection. Creating it here, outside
    # the reconnect loop, is what keeps the meter reading and the transaction
    # across a dropped socket.
    # 充电桩的寿命长于任何一条连接。把它建在重连循环之外，才能让电表读数和
    # 事务跨越一次断线活下来。
    cp: ChargePoint | None = None
    meter_task: asyncio.Task | None = None
    scenario_done = False
    backoff = INITIAL_BACKOFF_S

    while True:
        try:
            LOG.info("connecting to %s", uri)
            async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
                if cp is None:
                    cp = ChargePoint(args.id, ws)
                    cp._online = True
                else:
                    cp.rebind(ws)
                    LOG.info("reconnected, meter=%.1fWh tx=%s state=%s",
                             cp.meter_wh, cp.transaction_id, cp.sm.state.value)

                # Reset only after a socket is actually open, not after
                # connect() is merely attempted.
                # 只有 socket 真的打开之后才重置，不是"尝试过 connect"就重置。
                backoff = INITIAL_BACKOFF_S

                async def scenario() -> None:
                    nonlocal scenario_done
                    # OCPP requires BootNotification on every new connection,
                    # not just the first — the CSMS may have restarted and
                    # forgotten us.
                    # OCPP 要求每条新连接都发 BootNotification，不只是第一条 ——
                    # 后台可能重启过、已经把我们忘了。
                    await cp.boot()

                    if scenario_done:
                        return
                    if args.autostart > 0:
                        await asyncio.sleep(args.autostart)
                        await cp.start_transaction()   # no-op if one is running
                    if args.duration > 0:
                        await asyncio.sleep(args.duration)
                        await cp.stop_transaction()
                        cp._stop.set()
                    scenario_done = True

                # `asyncio.gather` stops WAITING on the first exception but
                # does not CANCEL the others — they keep running as orphans,
                # and the next loop iteration starts a second set. Two
                # meter_loops means MeterValues at twice the rate and an
                # energy register that climbs twice as fast as the real
                # power: the station over-bills, and it gets worse with
                # every reconnect.
                # `asyncio.gather` 遇到第一个异常只是"不再等待"，并不会**取消**
                # 其他协程 —— 它们变成游离任务继续跑，而下一轮循环又起一套。
                # 两个 meter_loop 意味着 MeterValues 频率翻倍、电表增速是真实
                # 功率的两倍: 桩会多计费, 而且每重连一次就更严重。
                # The meter belongs to the STATION, not to the link. A
                # dropped socket does not stop the car drawing current, so
                # this task is started once and outlives every connection;
                # while offline it keeps accumulating and buffers each
                # sample. Putting it in the per-connection set is what made
                # the station under-report energy across an outage.
                # 电表属于**桩**，不属于链路。socket 断了车照样在取电，所以这个
                # 任务只启动一次、比每一条连接都活得久; 离线期间它继续累计并把
                # 每条采样缓存起来。把它放进"每连接一套"的任务里，正是断网期间
                # 少计电量的原因。
                if meter_task is None:
                    meter_task = asyncio.create_task(cp.meter_loop())

                tasks = [asyncio.create_task(c) for c in (
                    cp.start(),            # message pump | 消息泵
                    scenario(),
                    cp.heartbeat_loop(),
                )]
                try:
                    done, _ = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_EXCEPTION)
                    for t in done:
                        if t.exception() is not None:
                            raise t.exception()
                finally:
                    for t in tasks:
                        t.cancel()
                    # cancel() only REQUESTS cancellation. Await them so the
                    # next iteration cannot start while an old loop is still
                    # alive and writing to the connection we just rebound.
                    # cancel() 只是**请求**取消。必须等它们真的结束，否则下一轮
                    # 开始时旧循环可能还活着，还在往我们刚换好的连接上写。
                    await asyncio.gather(*tasks, return_exceptions=True)

        except websockets.exceptions.InvalidURI:
            # A malformed --csms. Retrying can never fix a typo.
            # --csms 写错了。重试永远修不好一个拼写错误。
            raise

        except (websockets.exceptions.WebSocketException, OSError,
                asyncio.TimeoutError) as exc:
            # Everything transient lives under these three:
            # 所有"暂时性"的失败都在这三类之下:
            #   ConnectionClosed{OK,Error}  socket opened, then died
            #                               连上过，然后死了
            #   InvalidMessage              TCP accepted but no HTTP response
            #                               — exactly what a container restart
            #                               looks like: docker forwards the
            #                               port before the app listens
            #                               TCP 被接受但没有 HTTP 响应 —— 容器
            #                               重启时就长这样: docker 先转发端口,
            #                               应用还没开始监听
            #   InvalidStatus               a real HTTP status, e.g. SteVe's
            #                               404 for an unregistered station
            #                               真实的 HTTP 状态码, 比如 SteVe 对
            #                               未注册充电桩回的 404
            #   OSError                     never opened (refused, DNS)
            #                               压根没开起来(拒绝连接、DNS)
            #   TimeoutError                handshake hung
            #                               握手卡住
            #
            # Retrying a 404 forever is deliberate: an operator may register
            # the station at any moment, and a charge point that gives up is
            # a charge point someone has to drive out and reboot.
            # 对 404 也一直重试是有意的: 运维随时可能把这台桩注册上, 而一台
            # 放弃重试的桩意味着有人要开车过去手动重启它。
            if cp is not None:
                # Stop trying to write to a socket that is gone; meter_loop
                # will buffer instead of raising into the void.
                # 别再往一个已经没了的 socket 上写; meter_loop 会转为缓存,
                # 而不是对着空气抛异常。
                cp._online = False
            if args.no_reconnect or cp is not None and cp._stop.is_set():
                raise
            LOG.warning("connection lost (%s: %s) — retrying in %.0fs",
                        type(exc).__name__, exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)


# ----------------------------------------------------------------------
# TODO(you) — TODO 4, the rest of it.  Reconnecting is only the first half.
# TODO(你) — TODO 4 的后半截。重连只解决了一半。
# ----------------------------------------------------------------------
# Reconnect alone leaves the two sides disagreeing. Verified against SteVe:
# kill the CSMS mid-transaction and its `transaction` row keeps
# stop_timestamp = NULL forever — the session can never be billed, and the
# connector stays "in use" so the backend will not hand it to the next
# driver. Meanwhile the car really did charge; that energy exists nowhere.
# 只做重连会让两边的世界观分叉。已在 SteVe 上验证: 事务中途杀掉后台，它的
# transaction 行会永远停在 stop_timestamp = NULL —— 这次会话无法计费，而且
# 接口一直显示占用，后台不会分配给下一个车主。与此同时车真的在充电，
# 那段电量不存在于任何记录里。
#
# a) Queue instead of dropping.
#    `self.call()` raises ConnectionClosed while offline. Catch it for the
#    messages that carry billing data (StartTransaction, MeterValues,
#    StopTransaction) and append them to a deque instead. Heartbeat and
#    StatusNotification are worthless once stale — drop those.
#    离线时 `self.call()` 会抛 ConnectionClosed。对承载计费数据的消息
#    (StartTransaction / MeterValues / StopTransaction) 捕获它并压入队列;
#    Heartbeat 和 StatusNotification 过期就没有价值，直接丢弃。
#
# b) Replay in order, after BootNotification.
#    The CSMS must accept the boot before it will accept anything else.
#    重放必须排在 BootNotification 之后 —— 后台接受上电报到之前不收别的。
#
# c) Keep the ORIGINAL timestamps.
#    Each queued message already carries the `utcnow()` from the moment it
#    was built, and that is the whole point: replaying 120 MeterValues with
#    the time of replay would tell the CSMS that 120 samples happened in the
#    same second, and the energy curve becomes meaningless. Do not rebuild
#    the payload on the way out.
#    每条排队的消息里已经带着构造那一刻的 utcnow()，这正是关键: 用重放时刻
#    的时间戳发出 120 条 MeterValues，等于告诉后台这 120 次采样发生在同一秒,
#    电量曲线就废了。出队时不要重新构造 payload。
#
# d) The hard one: a StartTransaction that never landed.
#    No response means no transactionId, so every MeterValues queued behind
#    it has nothing to reference. The queue cannot be a plain FIFO of
#    finished payloads — the transactionId has to be filled in at send time,
#    once the replayed StartTransaction finally returns one.
#    最难的一块: StartTransaction 根本没送达。没有响应就没有 transactionId,
#    排在它后面的每条 MeterValues 都无处引用。所以队列不能是"成品报文的
#    简单 FIFO" —— transactionId 必须在发送那一刻才填进去，等重放的
#    StartTransaction 真正返回一个 id 之后。
#
# e) Bound the queue.
#    A station offline for a week must not exhaust its RAM. Decide what to
#    drop first and say so in a log line — silent truncation of billing data
#    is worse than the outage.
#    队列要有上限。离线一周的桩不能把内存吃光。决定先丢什么，并且打日志
#    说出来 —— 静默丢弃计费数据比断网本身更糟。


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
