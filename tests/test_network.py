"""网络报告解析、IPv6 过滤与防火墙命令测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.network import (
    CAMPUS_WIFI_WARNING,
    _classify_adapter,
    _is_global_ipv6,
    collect_network_report,
    enable_ipv6_binding,
    ensure_firewall_rule,
)
from tests.fakes import FakeRunner


def _payload(*, wifi_only: bool = False) -> dict:
    adapters = [
        {
            "Name": "以太网",
            "InterfaceDescription": "Realtek PCIe GbE Family Controller",
            "Status": "Up",
            "MediaType": "802.3",
            "NdisPhysicalMedium": 14,
        },
        {
            "Name": "WLAN",
            "InterfaceDescription": "Intel Wi-Fi 6 AX201",
            "Status": "Up",
            "MediaType": "Native 802.11",
            "NdisPhysicalMedium": 9,
        },
        {
            "Name": "vEthernet (WSL)",
            "InterfaceDescription": "Hyper-V Virtual Ethernet Adapter",
            "Status": "Up",
            "MediaType": "802.3",
            "NdisPhysicalMedium": 14,
        },
    ]
    ipv4 = [
        {
            "InterfaceAlias": "WLAN",
            "IPAddress": "10.0.0.5",
            "AddressState": "Preferred",
        }
    ]
    if not wifi_only:
        ipv4.insert(
            0,
            {
                "InterfaceAlias": "以太网",
                "IPAddress": "192.168.1.10",
                "AddressState": "Preferred",
            },
        )
    ipv6 = [
        {
            "InterfaceAlias": "以太网",
            "IPAddress": "fe80::1",
            "AddressState": "Preferred",
            "SuffixOrigin": "Link",
        },
        {
            "InterfaceAlias": "以太网",
            "IPAddress": "2001:da8::10",
            "AddressState": "Preferred",
            "SuffixOrigin": "RouterAdvertisement",
        },
        {
            "InterfaceAlias": "WLAN",
            "IPAddress": "2001:da8::20",
            "AddressState": "Preferred",
            "SuffixOrigin": "Random",
        },
    ]
    if wifi_only:
        ipv6 = [item for item in ipv6 if item["InterfaceAlias"] != "以太网"]

    return {
        "adapters": adapters,
        "ipv4": ipv4,
        "ipv6": ipv6,
        "bindings": [
            {"Name": "以太网", "Enabled": True},
            {"Name": "WLAN", "Enabled": False},
        ],
        "firewall": [
            {"DisplayName": "CampusStream Helper - Sunshine", "Enabled": True}
        ],
    }


class NetworkReportTest(unittest.TestCase):
    def test_ethernet_preferred_and_ipv6_filtering(self) -> None:
        runner = FakeRunner(powershell_response=json.dumps(_payload()))
        report = collect_network_report(runner)
        self.assertEqual(report.scenario, "ethernet_ready")
        self.assertEqual(report.preferred_ipv4, "192.168.1.10")
        self.assertEqual(report.preferred_ipv6, "2001:da8::10")
        self.assertEqual(report.ethernet_ipv4, "192.168.1.10")
        self.assertEqual(report.wlan_ipv4, "10.0.0.5")
        self.assertTrue(report.firewall_rule_present)
        self.assertTrue(any("同时在线" in warning for warning in report.warnings))
        self.assertTrue(any("ms_tcpip6" in warning for warning in report.warnings))

    def test_wifi_only_warning(self) -> None:
        runner = FakeRunner(powershell_response=json.dumps(_payload(wifi_only=True)))
        report = collect_network_report(runner)
        self.assertEqual(report.scenario, "wifi_only")
        self.assertIn(CAMPUS_WIFI_WARNING, report.warnings)

    def test_classify_adapter(self) -> None:
        self.assertEqual(_classify_adapter("以太网", "Realtek", "802.3"), "ethernet")
        self.assertEqual(_classify_adapter("WLAN", "Intel", "Native 802.11"), "wifi")
        self.assertEqual(
            _classify_adapter("vEthernet (WSL)", "Hyper-V Virtual Ethernet Adapter", "802.3"),
            "virtual",
        )

    def test_global_ipv6_filter(self) -> None:
        self.assertTrue(_is_global_ipv6("2001:da8::10", "RouterAdvertisement"))
        self.assertFalse(_is_global_ipv6("fe80::1", "Link"))
        self.assertFalse(_is_global_ipv6("2001:da8::20", "Random"))
        self.assertFalse(_is_global_ipv6("fd00::1", "Manual"))

    def test_enable_ipv6_binding(self) -> None:
        with tempfile.TemporaryDirectory():
            runner = FakeRunner()
            result = enable_ipv6_binding(runner, "WLAN")
            self.assertTrue(result.ok)
            self.assertIn("WLAN", runner.powershell_mutations[0])
            self.assertIn("Enable-NetAdapterBinding", runner.powershell_mutations[0])

    def test_firewall_rule_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "sunshine.exe"
            exe.write_text("stub", encoding="utf-8")
            runner = FakeRunner(dry_run=True)
            result = ensure_firewall_rule(runner, exe)
            self.assertTrue(result.ok)
            self.assertTrue(result.simulated)
            self.assertTrue(
                any("netsh" in command[0] for command in runner.commands),
                "Dry-Run 仍应记录将要执行的 netsh 命令",
            )


if __name__ == "__main__":
    unittest.main()
