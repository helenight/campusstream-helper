"""显卡检测测试：WMI JSON 解析与主显卡选择。"""

from __future__ import annotations

import json
import unittest

from core.common import CommandResult
from core.gpu import detect_gpu
from tests.fakes import FakeRunner


class GpuDetectTest(unittest.TestCase):
    def test_prefers_discrete_adapter(self) -> None:
        payload = json.dumps(
            [
                {
                    "Name": "Microsoft Basic Display Adapter",
                    "DriverVersion": "10.0.1",
                    "Status": "OK",
                    "AdapterRAM": 0,
                },
                {
                    "Name": "NVIDIA GeForce RTX 4060",
                    "DriverVersion": "32.0.15",
                    "Status": "OK",
                    "AdapterRAM": 8_589_934_592,
                },
            ]
        )
        info = detect_gpu(FakeRunner(powershell_response=payload))
        self.assertEqual(info.name, "NVIDIA GeForce RTX 4060")
        self.assertEqual(info.driver_version, "32.0.15")
        self.assertFalse(info.is_basic_adapter)
        self.assertTrue(info.ok)
        self.assertIn("RTX 4060", info.detail)

    def test_basic_adapter_needs_driver(self) -> None:
        payload = json.dumps(
            {
                "Name": "Microsoft Basic Display Adapter",
                "DriverVersion": "",
                "Status": "OK",
                "AdapterRAM": 0,
            }
        )
        info = detect_gpu(FakeRunner(powershell_response=payload))
        self.assertTrue(info.is_basic_adapter)
        self.assertFalse(info.ok)
        self.assertIn("基础显示适配器", info.detail)

    def test_empty_adapter_list(self) -> None:
        info = detect_gpu(FakeRunner(powershell_response="[]"))
        self.assertIsNotNone(info.error)
        self.assertEqual(info.name, "")
        self.assertFalse(info.ok)

    def test_invalid_json(self) -> None:
        info = detect_gpu(FakeRunner(powershell_response="{not-json"))
        self.assertIsNotNone(info.error)
        self.assertIn("无法解析", info.error or "")

    def test_query_failure(self) -> None:
        class FailingRunner(FakeRunner):
            def query_powershell(self, script, *, timeout_s=None):  # type: ignore[override]
                return CommandResult(("pwsh",), 1, "", "access denied")

        info = detect_gpu(FailingRunner())
        self.assertIsNotNone(info.error)
        self.assertIn("access denied", info.error or "")


if __name__ == "__main__":
    unittest.main()
