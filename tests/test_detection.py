"""五项检测的状态推导与汇总文案测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from core.detection import (
    DetectionState,
    admin_item,
    failed_item,
    gpu_item,
    network_item,
    pending_items,
    summarize,
    sunshine_item,
    vdd_item,
)
from core.gpu import GpuInfo
from core.network import AdapterInfo, NetworkReport
from core.power import PowerState
from core.sunshine import SunshineStatus
from core.vdd import VddStatus


def make_sunshine(
    *,
    installed: bool = True,
    service_state: str = "running",
    with_exe: bool = True,
    sunshinesvc_exists: bool = True,
    service_image_path: Path | None = None,
    service_image_exists: bool = True,
) -> SunshineStatus:
    install_dir = Path(r"C:\Program Files\Sunshine") if installed else None
    sunshinesvc_path = install_dir / "tools" / "sunshinesvc.exe" if install_dir else None
    return SunshineStatus(
        installed=installed,
        install_dir=install_dir,
        executable=(
            Path(r"C:\Program Files\Sunshine\sunshine.exe")
            if installed and with_exe
            else None
        ),
        config_path=(
            Path(r"C:\Program Files\Sunshine\config\sunshine.conf")
            if installed
            else None
        ),
        service_name="SunshineService",
        service_state=service_state,  # type: ignore[arg-type]
        version="2026.914.233613" if installed else None,
        detail="test",
        sunshinesvc_path=sunshinesvc_path,
        sunshinesvc_exists=sunshinesvc_exists,
        service_image_path=service_image_path,
        service_image_exists=service_image_exists,
    )


def make_vdd(
    *,
    installed: bool = True,
    with_settings: bool = True,
    device_ready: bool = True,
) -> VddStatus:
    return VddStatus(
        installed=installed,
        install_dir=Path(r"C:\VirtualDisplayDriver") if installed else None,
        settings_path=(
            Path(r"C:\VirtualDisplayDriver\vdd_settings.xml")
            if installed and with_settings
            else None
        ),
        driver_version="15.6.5.199" if installed else None,
        schema="resolutions" if installed else "unknown",
        configured_monitor_count=1 if installed else 0,
        configured_resolution_count=6 if installed else 0,
        detail="test",
        device_ready=device_ready,
    )


def make_network(
    *,
    firewall: bool = True,
    scenario: str = "ethernet_ready",
    ipv6_enabled: bool = True,
) -> NetworkReport:
    adapter = AdapterInfo(
        name="以太网",
        description="Intel Ethernet",
        kind="ethernet",
        is_up=True,
        ipv4="10.3.127.190",
        global_ipv6=("2408:8207::1",),
        ipv6_binding_enabled=ipv6_enabled,
    )
    return NetworkReport(
        adapters=(adapter,),
        scenario=scenario,  # type: ignore[arg-type]
        preferred_ipv4="10.3.127.190" if scenario != "offline" else None,
        preferred_ipv6=None,
        firewall_rule_present=firewall,
        warnings=(),
        generated_at="2026-11-26T21:00:00+08:00",
    )


def make_gpu(*, basic: bool = False) -> GpuInfo:
    return GpuInfo(
        name="Microsoft Basic Display Adapter" if basic else "NVIDIA RTX 4060",
        driver_version="" if basic else "32.0.15",
        status="OK",
        is_basic_adapter=basic,
        detail="test",
    )


def make_power() -> PowerState:
    return PowerState(
        scheme_guid="{test}",
        scheme_name="平衡",
        ac_sleep_seconds=0,
        ac_display_off_seconds=600,
        dc_sleep_seconds=900,
        dc_display_off_seconds=300,
    )


def ready_items(*, sunshine: bool = True, vdd: bool = True) -> tuple:
    return (
        admin_item(True, make_power()),
        gpu_item(make_gpu()),
        sunshine_item(make_sunshine(installed=sunshine)),
        vdd_item(make_vdd(installed=vdd)),
        network_item(make_network()),
    )


class DetectionModelTest(unittest.TestCase):
    def test_pending_items_cover_five_keys(self) -> None:
        items = pending_items()
        self.assertEqual(len(items), 5)
        self.assertEqual(
            [item.key for item in items],
            ["admin", "gpu", "sunshine", "vdd", "network"],
        )
        self.assertTrue(all(item.state == DetectionState.PENDING for item in items))

    def test_admin_warning_has_relaunch_action(self) -> None:
        item = admin_item(False, make_power())
        self.assertEqual(item.state, DetectionState.WARNING)
        self.assertEqual(item.actions[0].kind, "relaunch_admin")
        self.assertIn("普通用户", item.summary)

    def test_gpu_basic_adapter_is_warning(self) -> None:
        item = gpu_item(make_gpu(basic=True))
        self.assertEqual(item.state, DetectionState.WARNING)
        self.assertEqual(item.actions[0].kind, "open_device_manager")

    def test_sunshine_states(self) -> None:
        missing = sunshine_item(make_sunshine(installed=False))
        self.assertEqual(missing.state, DetectionState.WARNING)
        self.assertEqual(missing.actions[0].kind, "download_sunshine")

        stopped = sunshine_item(make_sunshine(service_state="stopped"))
        self.assertEqual(stopped.state, DetectionState.WARNING)
        self.assertEqual(stopped.actions[0].kind, "restart_sunshine")

        running = sunshine_item(make_sunshine())
        self.assertEqual(running.state, DetectionState.READY)
        self.assertFalse(running.actions)

    def test_sunshine_missing_service_binary_is_failed(self) -> None:
        item = sunshine_item(make_sunshine(sunshinesvc_exists=False))
        self.assertEqual(item.state, DetectionState.FAILED)
        self.assertIn("sunshinesvc.exe 缺失", item.summary)
        self.assertIn("360", item.summary)
        self.assertIn(
            r"请先将 C:\Program Files\Sunshine 加入杀软信任区",
            item.summary,
        )
        self.assertEqual(item.actions[0].kind, "download_sunshine")
        self.assertEqual(item.actions[0].label, "重新修复安装")
        self.assertIn("服务程序", [row[0] for row in item.detail])

    def test_sunshine_missing_service_image_is_failed(self) -> None:
        missing_image = Path(r"C:\Program Files\Sunshine\tools\sunshinesvc.exe")
        item = sunshine_item(
            make_sunshine(
                service_image_path=missing_image,
                service_image_exists=False,
            )
        )
        self.assertEqual(item.state, DetectionState.FAILED)
        self.assertIn("sunshinesvc.exe 缺失", item.summary)
        self.assertEqual(item.actions[0].label, "重新修复安装")

    def test_vdd_missing_settings(self) -> None:
        item = vdd_item(make_vdd(with_settings=False))
        self.assertEqual(item.state, DetectionState.WARNING)
        self.assertEqual(item.actions[0].kind, "download_vdd")

    def test_vdd_deployed_but_driver_not_ready(self) -> None:
        item = vdd_item(make_vdd(device_ready=False))
        self.assertEqual(item.state, DetectionState.WARNING)
        self.assertIn("尚未就绪", item.summary)
        self.assertEqual(item.actions[0].kind, "download_vdd")
        self.assertEqual(item.actions[0].label, "重新安装 VDD 驱动")
        self.assertIn("PnP 状态", [row[0] for row in item.detail])

    def test_network_firewall_and_ipv6_actions(self) -> None:
        healthy = network_item(make_network(), can_add_firewall=True)
        self.assertEqual(healthy.state, DetectionState.READY)
        self.assertFalse(healthy.actions)

        no_firewall = network_item(make_network(firewall=False), can_add_firewall=True)
        self.assertEqual(no_firewall.state, DetectionState.WARNING)
        self.assertIn("add_firewall", [action.kind for action in no_firewall.actions])

        no_ipv6 = network_item(make_network(ipv6_enabled=False))
        self.assertEqual(no_ipv6.state, DetectionState.WARNING)
        fix = next(action for action in no_ipv6.actions if action.kind == "fix_ipv6")
        self.assertEqual(fix.payload, ("以太网",))

    def test_network_firewall_action_requires_sunshine(self) -> None:
        item = network_item(make_network(firewall=False), can_add_firewall=False)
        self.assertNotIn("add_firewall", [action.kind for action in item.actions])

    def test_summarize_all_ready(self) -> None:
        text, result = summarize(ready_items())
        self.assertEqual(result, "success")
        self.assertIn("环境全部就绪", text)

    def test_summarize_warning_and_failed(self) -> None:
        warning_items = list(ready_items())
        warning_items[2] = sunshine_item(make_sunshine(installed=False))
        text, result = summarize(tuple(warning_items))
        self.assertEqual(result, "warning")
        self.assertIn("1 项待处理", text)

        failed_items = list(ready_items())
        failed_items[1] = failed_item("gpu", "WMI 查询失败")
        text, result = summarize(tuple(failed_items))
        self.assertEqual(result, "failed")
        self.assertIn("1 项失败", text)


if __name__ == "__main__":
    unittest.main()
