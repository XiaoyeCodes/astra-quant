"""The OctoBot watchdog restarts a hung OctoBot, never a starting or briefly restarting one."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from octobot_supervisor import Watchdog, exit_status


class WatchdogTests(unittest.TestCase):
    def test_first_start_gets_a_grace_period(self):
        dog = Watchdog(started=0, grace=900, timeout=600)
        # plugin installation: no answer for 20 minutes is still within grace + timeout
        self.assertFalse(any(dog.observe(t, False) for t in range(0, 1500, 30)))
        self.assertTrue(dog.observe(1500, False))

    def test_hang_after_running_is_detected(self):
        dog = Watchdog(started=0, grace=900, timeout=600)
        for t in range(0, 2001, 40):
            dog.observe(t, True)  # last successful ping at t=2000
        self.assertFalse(dog.observe(2000 + 590, False))
        self.assertTrue(dog.observe(2000 + 600, False))

    def test_strategy_switch_restart_is_tolerated(self):
        dog = Watchdog(started=0, grace=900, timeout=600)
        dog.observe(1000, True)
        # OctoBot restarts itself for a strategy switch: about 3 minutes offline
        self.assertFalse(any(dog.observe(1000 + t, False) for t in range(30, 200, 30)))
        self.assertFalse(dog.observe(1210, True))
        self.assertFalse(dog.observe(1800, False))

    def test_recovery_resets_the_clock(self):
        dog = Watchdog(started=0, grace=0, timeout=600)
        dog.observe(0, True)
        dog.observe(500, False)
        dog.observe(550, True)
        self.assertFalse(dog.observe(1100, False))
        self.assertTrue(dog.observe(1150, False))


class ExitStatusTests(unittest.TestCase):
    def test_signal_deaths_use_shell_convention(self):
        self.assertEqual(exit_status(-15), 143)
        self.assertEqual(exit_status(-9), 137)
        self.assertEqual(exit_status(0), 0)
        self.assertEqual(exit_status(1), 1)


if __name__ == "__main__":
    unittest.main()
