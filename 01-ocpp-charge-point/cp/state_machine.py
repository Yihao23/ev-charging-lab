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


@dataclass
class ConnectorStateMachine:
    """Tracks one connector and records its transition history.
    跟踪单个接口并记录迁移历史。"""

    connector_id: int
    state: S = S.available
    history: list[tuple[S, S]] = field(default_factory=list)

    def can(self, target: S) -> bool:
        """True if `target` is reachable from the current state.
        若 `target` 可从当前状态到达则返回 True。"""
        return target in LEGAL.get(self.state, set())

    def to(self, target: S) -> S:
        """Perform the transition, or raise IllegalTransition.
        执行状态迁移，非法则抛出 IllegalTransition。"""
        if target == self.state:
            return self.state
        if not self.can(target):
            raise IllegalTransition(
                f"connector {self.connector_id}: {self.state.value} -> {target.value}"
            )
        self.history.append((self.state, target))
        self.state = target
        return self.state

    # ------------------------------------------------------------------
    # TODO(you) — extend this when you get to Stage 1 day 3
    # TODO(你) — 阶段 1 第 3 天再来扩展这里
    # ------------------------------------------------------------------
    # 1. OCPP 1.6 also carries a ChargePointErrorCode alongside every status.
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
