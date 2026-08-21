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

    async def notify_status(self, target: S) -> None:
        """Transition, then tell the CSMS. Order matters — never report a
        state you failed to enter.
        先迁移再上报。顺序很重要 —— 绝不上报一个没能进入的状态。"""
        self.sm.to(target)
        LOG.info("state -> %s", target.value)
        await self.call(
            call.StatusNotification(
                connector_id=self.sm.connector_id,
                error_code=ChargePointErrorCode.no_error,
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
            actual_a = profile_mod.clamp(EV_REQUESTED_CURRENT_A, limit_a)
            power_w = actual_a * NOMINAL_VOLTAGE_V
            self.meter_wh += power_w * METER_INTERVAL_S / 3600.0

            if limit_a is not None and actual_a < EV_REQUESTED_CURRENT_A:
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
