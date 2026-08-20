"""Unit tests for the connector state machine.  接口状态机的单元测试。"""

import unittest

from ocpp.v16.enums import ChargePointStatus as S

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


if __name__ == "__main__":
    unittest.main()
