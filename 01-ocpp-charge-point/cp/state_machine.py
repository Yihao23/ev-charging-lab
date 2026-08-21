"""
Connector state machine for an OCPP 1.6 charge point.
充电桩接口(connector)的 OCPP 1.6 状态机。

Why this file exists / 为什么单独一个文件:
    The state machine is the part a reviewer will ask you about. Keeping it
    free of I/O means you can unit-test every transition without a network.
    状态机是面试官最可能追问的部分。把它与 I/O 解耦，就能脱离网络做单元测试。

Reference / 参考: OCPP 1.6 Specification, section 3.4 "Status Notification".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ocpp.v16.enums import ChargePointErrorCode as E
from ocpp.v16.enums import ChargePointStatus as S

# Legal transitions of the OCPP 1.6 connector status model.
# OCPP 1.6 接口状态模型的合法迁移。
#
# The happy path is:  Available -> Preparing -> Charging -> Finishing -> Available
# 正常路径:            可用     ->  准备中   ->  充电中  ->  结束中   ->  可用
LEGAL: dict[S, set[S]] = {
    S.available: {S.preparing, S.reserved, S.unavailable, S.faulted},
    S.preparing: {S.charging, S.available, S.finishing, S.faulted},
    S.charging: {S.suspended_ev, S.suspended_evse, S.finishing, S.faulted},
    S.suspended_ev: {S.charging, S.suspended_evse, S.finishing, S.faulted},
    S.suspended_evse: {S.charging, S.suspended_ev, S.finishing, S.faulted},
    S.finishing: {S.available, S.faulted},
    S.reserved: {S.available, S.preparing, S.faulted},
    S.unavailable: {S.available, S.faulted},
    # A Faulted connector may only recover to Available.
    # 故障接口只能恢复到 Available。
    S.faulted: {S.available},
}


class IllegalTransition(RuntimeError):
    """Raised when the caller requests a transition the spec forbids.
    当调用方请求规范禁止的状态迁移时抛出。"""


class FaultLatched(IllegalTransition):
    """Raised when leaving `faulted` while an error is still latched.
    错误码仍处于闩锁状态时试图离开 faulted，抛出此异常。

    A subclass of IllegalTransition so existing `except IllegalTransition`
    still catches it, while callers that care can tell the two apart.
    继承自 IllegalTransition，已有的 `except IllegalTransition` 仍能接住，
    需要区分的调用方也能单独捕获。"""


@dataclass
class ConnectorStateMachine:
    """Tracks one connector and records its transition history.
    跟踪单个接口并记录迁移历史。"""

    connector_id: int
    state: S = S.available
    error_code: E = E.no_error
    history: list[tuple[S, S]] = field(default_factory=list)

    def can(self, target: S) -> bool:
        """True if `target` is reachable from the current state.
        若 `target` 可从当前状态到达则返回 True。"""
        return target in LEGAL.get(self.state, set())

    def to(self, target: S, error_code: E | None = None) -> S:
        """Perform the transition, or raise.
        执行状态迁移，非法则抛出异常。

        `error_code` is three-way, mirroring what a StatusNotification can
        say / `error_code` 有三种含义，对应 StatusNotification 能表达的三件事:
            omitted   — not reporting a code; the latched one stays as it is
                        不报告错误码，已记录的保持不变
            no_error  — explicitly reporting "no fault"; this is the reset
                        显式报告"无故障"，这就是复位动作
            anything  — record this code
            else        记录这个错误码

        Two independent checks run in order, and both still apply:
        两层独立检查按顺序执行，缺一不可:
            1. LEGAL — is this transition in the state model at all?
                       这个迁移在状态模型里存在吗?
            2. latch — is a fault still holding us in `faulted`?
                       是否还有故障把我们锁在 faulted?

        Raises IllegalTransition for 1 and FaultLatched for 2, so a caller
        can tell "you asked for something impossible" apart from "the
        hardware still needs attention".
        1 抛 IllegalTransition，2 抛 FaultLatched，调用方能区分
        "你请求了不可能的事" 和 "硬件还需要人处理"。
        """
        if target == self.state:
            # Same status, possibly a new error code. Still a status report,
            # but not a transition — nothing goes into history.
            # 状态没变，但可能带了新的错误码。这仍是一次状态上报，
            # 只是不算迁移，所以不记入 history。
            if error_code is not None:
                self.error_code = error_code
            return self.state

        if not self.can(target):
            raise IllegalTransition(
                f"connector {self.connector_id}: {self.state.value} -> {target.value}"
            )

        # The latch only guards the way OUT of `faulted`. Checking it on
        # every transition would trap a connector that is merely Available
        # with a HighTemperature warning — a real and chargeable state.
        # 闩锁只守住"离开 faulted"这一条路。每次迁移都检查的话，一个仅仅是
        # Available + HighTemperature 的接口会被永久困住 —— 而那是真实且
        # 可以充电的状态。
        #
        # An earth fault does not clear itself: a station that reports
        # Faulted/GroundFailure and then Available/GroundFailure a second
        # later keeps attracting drivers who cannot charge, while the
        # backend believes it is healthy and sends nobody to repair it.
        # 接地故障不会自己好: 一台先报 Faulted/GroundFailure、一秒后又报
        # Available/GroundFailure 的桩，会不断吸引车主来插枪却充不上，
        # 而后台以为它健康、不派人维修。
        if (self.state is S.faulted
                and self.error_code is not E.no_error
                and error_code is not E.no_error):
            raise FaultLatched(
                f"connector {self.connector_id}: cannot leave "
                f"{self.state.value} while {self.error_code.value} is latched; "
                f"report {E.no_error.value} to reset"
            )

        self.history.append((self.state, target))
        self.state = target
        if error_code is not None:
            self.error_code = error_code
        return self.state

    # ------------------------------------------------------------------
    # TODO(you) — extend this when you get to Stage 1 day 3
    # TODO(你) — 阶段 1 第 3 天再来扩展这里
    # ------------------------------------------------------------------
    # 1. OCPP 1.6 also carries a ChargePointErrorCode alongside every status.
    #    --- DONE / 已完成 --- see `error_code`, `to(..., error_code=)` and
    #    `FaultLatched`. Still to wire into charge_point.notify_status().
    #    Track the last error code and refuse to leave `faulted` while an
    #    error other than `NoError` is latched.
    #    OCPP 1.6 每条状态都携带 ChargePointErrorCode。请记录最后的错误码，
    #    并在错误码不是 NoError 时拒绝离开 faulted 状态。
    #
    # 2. Real stations debounce: a connector that flaps
    #    Charging <-> SuspendedEV faster than N seconds should not emit a
    #    StatusNotification for every flap. Add a min-dwell filter and prove
    #    it with a test that fakes the clock.
    #    真实充电桩会做防抖: 在 N 秒内反复 Charging <-> SuspendedEV 抖动时
    #    不应每次都上报 StatusNotification。加一个最小驻留时间过滤器，
    #    并用一个伪造时钟的测试证明它。
