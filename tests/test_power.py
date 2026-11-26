"""电源配置读取、应用与还原测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.power import (
    _seconds_to_minutes,
    apply_remote_profile,
    read_power_state,
    restore_previous_profile,
)
from tests.fakes import FakeRunner

POWER_PAYLOAD = {
    "scheme_guid": "381b4222-f694-41f0-9685-ff5bb260df2e",
    "scheme_name": "平衡",
    "ac_sleep_seconds": 1200,
    "dc_sleep_seconds": 600,
    "ac_display_off_seconds": 600,
    "dc_display_off_seconds": 300,
}


class PowerTest(unittest.TestCase):
    def test_read_power_state(self) -> None:
        runner = FakeRunner(powershell_response=json.dumps(POWER_PAYLOAD))
        state = read_power_state(runner)
        self.assertEqual(state.scheme_name, "平衡")
        self.assertEqual(state.ac_sleep_seconds, 1200)
        self.assertFalse(state.remote_ready)

    def test_apply_profile_saves_original(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "power_state.json"
            runner = FakeRunner(powershell_response=json.dumps(POWER_PAYLOAD))
            result = apply_remote_profile(runner, state_path=state_path)
            self.assertTrue(result.ok)
            self.assertTrue(state_path.exists())
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["ac_sleep_seconds"], 1200)

            flat = [" ".join(command) for command in runner.commands]
            self.assertTrue(any("standby-timeout-ac 0" in item for item in flat))
            self.assertTrue(any("monitor-timeout-ac 10" in item for item in flat))

    def test_dry_run_does_not_write_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "power_state.json"
            runner = FakeRunner(dry_run=True, powershell_response=json.dumps(POWER_PAYLOAD))
            result = apply_remote_profile(runner, state_path=state_path)
            self.assertTrue(result.ok)
            self.assertTrue(result.simulated)
            self.assertIn("将设置", result.message)
            self.assertFalse(state_path.exists())

    def test_restore_previous_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "power_state.json"
            state_path.write_text(json.dumps(POWER_PAYLOAD), encoding="utf-8")
            runner = FakeRunner()
            result = restore_previous_profile(runner, state_path=state_path)
            self.assertTrue(result.ok)
            self.assertFalse(state_path.exists())

            flat = [" ".join(command) for command in runner.commands]
            self.assertTrue(any("standby-timeout-ac 20" in item for item in flat))
            self.assertTrue(any("monitor-timeout-ac 10" in item for item in flat))

    def test_seconds_to_minutes(self) -> None:
        self.assertEqual(_seconds_to_minutes(0), 0)
        self.assertEqual(_seconds_to_minutes(60), 1)
        self.assertEqual(_seconds_to_minutes(90), 2)
        self.assertEqual(_seconds_to_minutes(600), 10)
        self.assertEqual(_seconds_to_minutes(150), 3)


if __name__ == "__main__":
    unittest.main()
