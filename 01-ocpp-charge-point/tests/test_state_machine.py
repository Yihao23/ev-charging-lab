"""Unit tests for the connector state machine.  接口状态机的单元测试。"""

import unittest

from ocpp.v16.enums import ChargePointErrorCode as E
from ocpp.v16.enums import ChargePointStatus as S

from cp import state_machine as SM
from cp.state_machine import ConnectorStateMachine, IllegalTransition


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.sm = ConnectorStateMachine(connector_id=1)

    def test_happy_path(self):
        for target in (S.preparing, S.charging, S.finishing, S.available):
            self.sm.to(target)
        self.assertEqual(self.sm.state, S.available)
        self.assertEqual(len(self.sm.history), 4)

    def test_cannot_jump_from_available_to_charging(self):
        """A connector must pass through Preparing. Skipping it is the single
        most common bug in home-grown OCPP stacks.
        接口必须经过 Preparing。跳过它是自研 OCPP 栈里最常见的 bug。"""
        with self.assertRaises(IllegalTransition):
            self.sm.to(S.charging)

    def test_faulted_only_recovers_to_available(self):
        self.sm.to(S.faulted)
        with self.assertRaises(IllegalTransition):
            self.sm.to(S.charging)
        self.sm.to(S.available)
        self.assertEqual(self.sm.state, S.available)

    def test_self_transition_is_a_noop(self):
        self.sm.to(S.available)
        self.assertEqual(self.sm.history, [])

    def test_suspend_and_resume(self):
        self.sm.to(S.preparing)
        self.sm.to(S.charging)
        self.sm.to(S.suspended_ev)
        self.sm.to(S.charging)
        self.assertEqual(self.sm.state, S.charging)


class TestErrorCodeLatch(unittest.TestCase):
    """TODO 1 — latch the error code while the connector is Faulted.
    TODO 1 —— 接口处于 Faulted 时闩锁错误码。

    RED until `to()` accepts an `error_code` and `FaultLatched` exists.
    在 `to()` 接受 `error_code` 且 `FaultLatched` 存在之前，这一组是红的。

    Why / 为什么:
        OCPP carries `status` and `errorCode` as two independent fields —
        a connector can be Available while reporting a non-NoError code
        (a broken fan does not stop it charging). But a station that
        reports Faulted/GroundFailure and then Available/GroundFailure one
        second later is lying: an earth fault does not fix itself. Such a
        station keeps attracting drivers who cannot charge, while the
        backend believes it is healthy and sends nobody to repair it.
        OCPP 的 status 和 errorCode 是两个正交字段 —— 接口可以是 Available
        同时带着非 NoError 的码(风扇坏了不影响充电)。但一台先报
        Faulted/GroundFailure、一秒后又报 Available/GroundFailure 的桩在撒谎:
        接地故障不会自己好。这种桩会不断吸引车主来插枪却充不上，而后台
        以为它健康、不派人维修。

    Assumed API / 假设的接口:
        ConnectorStateMachine.error_code: ChargePointErrorCode = no_error
        to(target, error_code=E.no_error) -> S
        class FaultLatched(IllegalTransition)

    The latch is checked only when LEAVING `faulted`. Checking it on every
    transition would trap a connector that is merely Available with a
    HighTemperature warning.
    闩锁只在**离开** faulted 时检查。每次迁移都检查的话，一个仅仅是
    Available + HighTemperature 的接口会被困住。
    """

    def setUp(self):
        self.sm = ConnectorStateMachine(connector_id=1)

    def test_defaults_to_no_error(self):
        self.assertEqual(self.sm.error_code, E.no_error)

    def test_entering_faulted_latches_the_code(self):
        self.sm.to(S.faulted, error_code=E.ground_failure)
        self.assertEqual(self.sm.state, S.faulted)
        self.assertEqual(self.sm.error_code, E.ground_failure)

    def test_cannot_leave_faulted_while_latched(self):
        """An earth fault does not clear itself.  接地故障不会自己好。"""
        self.sm.to(S.faulted, error_code=E.ground_failure)
        with self.assertRaises(SM.FaultLatched):
            self.sm.to(S.available)

    def test_explicit_reset_clears_the_latch(self):
        """Passing NoError is the reset.  显式传 NoError 就是复位。"""
        self.sm.to(S.faulted, error_code=E.ground_failure)
        self.sm.to(S.available, error_code=E.no_error)
        self.assertEqual(self.sm.state, S.available)
        self.assertEqual(self.sm.error_code, E.no_error)

    def test_faulted_without_an_error_code_is_not_latched(self):
        """Faulted can be reported with NoError; nothing to reset then.
        Faulted 也可能带 NoError 上报，这时没有东西需要复位。"""
        self.sm.to(S.faulted)
        self.sm.to(S.available)
        self.assertEqual(self.sm.state, S.available)

    def test_error_code_outside_faulted_does_not_latch(self):
        """Available + HighTemperature is legal and must not trap the
        connector. 仅仅是 Available + HighTemperature 不应该困住接口。"""
        self.sm.to(S.preparing, error_code=E.high_temperature)
        self.assertEqual(self.sm.error_code, E.high_temperature)
        self.sm.to(S.charging)
        self.assertEqual(self.sm.state, S.charging)

    def test_latch_does_not_replace_the_legal_table(self):
        """Two independent checks, both still apply.
        两层检查各管各的，都还在。

        Faulted -> Charging is forbidden by LEGAL regardless of the latch,
        and must raise IllegalTransition rather than FaultLatched.
        无论闩锁如何，LEGAL 表都禁止 Faulted -> Charging，
        应该抛 IllegalTransition 而不是 FaultLatched。
        """
        self.sm.to(S.faulted, error_code=E.ground_failure)
        with self.assertRaises(IllegalTransition) as ctx:
            self.sm.to(S.charging)
        self.assertNotIsInstance(ctx.exception, SM.FaultLatched)


if __name__ == "__main__":
    unittest.main()
