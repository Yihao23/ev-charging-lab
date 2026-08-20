"""
A minimal CSMS (Central System) — enough to drive the charge point.
一个最小可用的 CSMS(后台) —— 足以驱动充电桩。

Run / 运行:
    python -m csms.central_system --port 9000 --push-profile-after 15

Use this while you are iterating. Once the flow works, point the same charge
point at a real open-source CSMS instead (SteVe / CitrineOS) — see README.
迭代阶段用这个。流程跑通后，把同一个充电桩接到真正的开源 CSMS
(SteVe / CitrineOS) 上 —— 见 README。
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
    AuthorizationStatus,
    ChargingProfileKindType,
    ChargingProfilePurposeType,
    ChargingRateUnitType,
    RegistrationStatus,
)

LOG = logging.getLogger("csms")

HEARTBEAT_INTERVAL = 30
# id tags this toy CSMS accepts.  这个玩具后台接受的 idTag 白名单。
WHITELIST = {"DEADBEEF", "CAFEBABE"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class CentralSystem(OcppChargePoint):
    """Server-side view of ONE connected charge point.
    服务端视角下的单个已连接充电桩。"""

    def __init__(self, cp_id: str, connection):
        super().__init__(cp_id, connection)
        self.next_tx_id = 1
        self.transactions: dict[int, str] = {}

    # ---------------------------------------------------------------- #
    # Handlers  (charge point -> CSMS)                                  #
    # ---------------------------------------------------------------- #

    @on(Action.boot_notification)
    def on_boot(self, charge_point_vendor, charge_point_model, **kwargs):
        LOG.info("BootNotification from %s / %s", charge_point_vendor, charge_point_model)
        return call_result.BootNotification(
            current_time=utcnow(),
            interval=HEARTBEAT_INTERVAL,
            status=RegistrationStatus.accepted,
        )

    @on(Action.heartbeat)
    def on_heartbeat(self, **kwargs):
        return call_result.Heartbeat(current_time=utcnow())

    @on(Action.status_notification)
    def on_status(self, connector_id, error_code, status, **kwargs):
        LOG.info("StatusNotification c%s: %s (err=%s)", connector_id, status, error_code)
        return call_result.StatusNotification()

    @on(Action.authorize)
    def on_authorize(self, id_tag, **kwargs):
        ok = id_tag in WHITELIST
        LOG.info("Authorize(%s) -> %s", id_tag, "Accepted" if ok else "Invalid")
        return call_result.Authorize(
            id_tag_info={
                "status": AuthorizationStatus.accepted if ok else AuthorizationStatus.invalid
            }
        )

    @on(Action.start_transaction)
    def on_start_tx(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        tx_id = self.next_tx_id
        self.next_tx_id += 1
        self.transactions[tx_id] = id_tag
        LOG.info("StartTransaction c%s idTag=%s -> tx=%s", connector_id, id_tag, tx_id)
        return call_result.StartTransaction(
            transaction_id=tx_id,
            id_tag_info={"status": AuthorizationStatus.accepted},
        )

    @on(Action.stop_transaction)
    def on_stop_tx(self, meter_stop, timestamp, transaction_id, **kwargs):
        LOG.info("StopTransaction tx=%s meterStop=%sWh", transaction_id, meter_stop)
        self.transactions.pop(transaction_id, None)
        return call_result.StopTransaction()

    @on(Action.meter_values)
    def on_meter_values(self, connector_id, meter_value, **kwargs):
        for mv in meter_value:
            readings = ", ".join(
                f"{s.get('measurand', 'Energy.Active.Import.Register')}="
                f"{s['value']}{s.get('unit', '')}"
                for s in mv.get("sampled_value", mv.get("sampledValue", []))
            )
            LOG.info("MeterValues c%s: %s", connector_id, readings)
        return call_result.MeterValues()

    # ---------------------------------------------------------------- #
    # Operator actions  (CSMS -> charge point)                          #
    # 运营侧动作 (后台 -> 桩)                                            #
    # ---------------------------------------------------------------- #

    async def push_grid_limit_profile(self) -> None:
        """Simulate a grid constraint: step the station down over time.
        模拟电网约束: 让充电桩的功率随时间阶梯下降。

        0-30s   : 32 A  (no constraint)      | 无约束
        30-60s  : 16 A  (grid gets tight)    | 电网趋紧
        60s+    :  8 A  (hard curtailment)   | 硬性削减
        """
        payload = {
            "chargingProfileId": 100,
            "stackLevel": 1,
            "chargingProfilePurpose": ChargingProfilePurposeType.tx_profile,
            "chargingProfileKind": ChargingProfileKindType.relative,
            "chargingSchedule": {
                "chargingRateUnit": ChargingRateUnitType.amps,
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": 32.0},
                    {"startPeriod": 30, "limit": 16.0},
                    {"startPeriod": 60, "limit": 8.0},
                ],
            },
        }
        resp = await self.call(
            call.SetChargingProfile(connector_id=1, cs_charging_profiles=payload)
        )
        LOG.info("SetChargingProfile -> %s", resp.status)

    async def remote_start(self, id_tag: str = "DEADBEEF") -> None:
        resp = await self.call(call.RemoteStartTransaction(id_tag=id_tag))
        LOG.info("RemoteStartTransaction -> %s", resp.status)

    # ------------------------------------------------------------------
    # TODO(you) — Stage 1, day 4
    # ------------------------------------------------------------------
    # 1. Persist transactions to SQLite so restarts do not lose them.
    #    把事务持久化到 SQLite，重启不丢数据。
    # 2. Reject a StartTransaction whose idTag has a concurrent transaction
    #    (AuthorizationStatus.concurrent_tx) — a real spec corner.
    #    对已有并发事务的 idTag 返回 concurrent_tx —— 规范里的真实边角。
    # 3. Expose an HTTP endpoint so Stage 2's Node-RED dashboard can drive
    #    RemoteStart / SetChargingProfile from a button.
    #    暴露 HTTP 接口，让阶段 2 的 Node-RED 仪表盘用按钮触发这些动作。


async def main() -> None:
    ap = argparse.ArgumentParser(description="Toy OCPP 1.6 CSMS")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--push-profile-after", type=float, default=0.0,
                    help="seconds after connect before pushing a SetChargingProfile (0 = never)")
    ap.add_argument("--remote-start-after", type=float, default=0.0,
                    help="seconds after connect before sending RemoteStartTransaction (0 = never)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(name)-6s %(message)s")

    async def on_connect(ws):
        # websockets >= 14 exposes the request path here.
        # websockets >= 14 在这里暴露请求路径。
        path = getattr(getattr(ws, "request", None), "path", "") or "/UNKNOWN"
        cp_id = path.strip("/").split("/")[-1]

        if ws.subprotocol != "ocpp1.6":
            LOG.error("client %s did not negotiate ocpp1.6, closing", cp_id)
            await ws.close()
            return

        LOG.info("charge point %s connected", cp_id)
        cs = CentralSystem(cp_id, ws)

        async def operator():
            if args.remote_start_after > 0:
                await asyncio.sleep(args.remote_start_after)
                await cs.remote_start()
            if args.push_profile_after > 0:
                await asyncio.sleep(args.push_profile_after)
                await cs.push_grid_limit_profile()

        try:
            await asyncio.gather(cs.start(), operator())
        except websockets.exceptions.ConnectionClosed:
            LOG.info("charge point %s disconnected", cp_id)

    async with websockets.serve(on_connect, args.host, args.port, subprotocols=["ocpp1.6"]):
        LOG.info("CSMS listening on ws://%s:%s/<chargePointId>", args.host, args.port)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
