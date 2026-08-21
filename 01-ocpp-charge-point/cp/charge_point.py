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
        await self.notify_status(S.available)

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

            await self.call(
                call.MeterValues(
                    connector_id=self.sm.connector_id,
                    transaction_id=self.transaction_id,
                    meter_value=[
                        {
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
                    ],
                )
            )

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
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(name)-6s %(message)s")

    uri = f"{args.csms.rstrip('/')}/{args.id}"
    LOG.info("connecting to %s", uri)
    async with websockets.connect(uri, subprotocols=["ocpp1.6"]) as ws:
        cp = ChargePoint(args.id, ws)

        async def scenario() -> None:
            await cp.boot()
            if args.autostart > 0:
                await asyncio.sleep(args.autostart)
                await cp.start_transaction()
            if args.duration > 0:
                await asyncio.sleep(args.duration)
                await cp.stop_transaction()
                cp._stop.set()

        await asyncio.gather(
            cp.start(),            # message pump | 消息泵
            scenario(),
            cp.heartbeat_loop(),
            cp.meter_loop(),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
