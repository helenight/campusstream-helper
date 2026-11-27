"""诊断报告核心：脱敏、端口探测、六板块采集与失败隔离。"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from core.common import CommandResult
from core.diagnostics import (
    APP_LOG_FILENAME,
    CRASH_LOG_FILENAME,
    DiagnosticSection,
    DiagnosticsReport,
    collect_diagnostics,
    configure_file_logging,
    probe_ports,
    redact_text,
)
from core.network import AdapterInfo, NetworkReport
from core.sunshine import SunshineStatus
from core.vdd import VddStatus
from tests.fakes import FakeRunner


class RedactionTest(unittest.TestCase):
    def test_masks_user_directory_and_secret_values(self) -> None:
        text = (
            r"安装目录 C:\Users\周成儒\AppData\Local\Sunshine" + "\n"
            "password = hunter2-secret\n"
            "2026-01-01 12:00:00 [Sunshine] token = log-token-value\n"
            '"token": "abc-token-value"\n'
            "<secret>xml-secret</secret>\n"
            "https://example.test/pin=9876&x=1\n"
            "普通日志：服务运行正常"
        )
        result = redact_text(text)

        self.assertIn(r"C:\Users\<USER>\AppData", result)
        self.assertNotIn("周成儒", result)
        self.assertNotIn("hunter2-secret", result)
        self.assertNotIn("log-token-value", result)
        self.assertNotIn("abc-token-value", result)
        self.assertNotIn("xml-secret", result)
        self.assertIn("2026-01-01 12:00:00 [Sunshine] token = <已脱敏>", result)
        self.assertNotIn("9876", result)
        self.assertIn("<已脱敏>", result)
        self.assertIn("普通日志：服务运行正常", result)

    def test_report_can_disable_redaction(self) -> None:
        report = _report(
            sections=(
                DiagnosticSection(
                    "system",
                    "系统与代理",
                    r"路径：C:\Users\周成儒\secret" + "\npassword = abc123",
                ),
            )
        )
        redacted = report.to_markdown(redact=True)
        raw = report.to_markdown(redact=False)

        self.assertIn(r"C:\Users\<USER>\secret", redacted)
        self.assertNotIn("周成儒", redacted)
        self.assertIn("自动脱敏：已开启", redacted)
        self.assertIn("周成儒", raw)
        self.assertIn("自动脱敏：未开启", raw)
        self.assertEqual(report.failed_sections, ())


class ProbePortsTest(unittest.TestCase):
    def test_uses_injected_connector(self) -> None:
        result = probe_ports(
            (47984, 47989, 47990, 48010),
            connector=lambda port: port in {47990, 48010},
        )
        self.assertEqual(
            result,
            {47984: False, 47989: False, 47990: True, 48010: True},
        )

    def test_connector_exception_is_false(self) -> None:
        def broken(_port: int) -> bool:
            raise OSError("boom")

        self.assertEqual(probe_ports((47990,), connector=broken), {47990: False})


class DiagnosticsRunner(FakeRunner):
    """按脚本内容返回系统 / 显示器查询结果。"""

    def __init__(self) -> None:
        super().__init__()
        self.system_payload = {
            "ProductName": "Windows 11 专业版",
            "DisplayVersion": "24H2",
            "CurrentBuildNumber": "26100",
            "UBR": 1234,
            "ProxyEnable": 1,
            "ProxyServer": "127.0.0.1:7890",
            "ProxyOverride": "localhost;127.*",
            "AutoConfigURL": "",
        }

    def query_powershell(
        self,
        script: str,
        *,
        timeout_s: float | None = None,
    ) -> CommandResult:
        self.powershell_scripts.append(script)
        if "Internet Settings" in script:
            return CommandResult(
                ("pwsh",),
                0,
                json.dumps(self.system_payload, ensure_ascii=False),
            )
        if "Get-PnpDevice -Class Display" in script:
            return CommandResult(
                ("pwsh",),
                0,
                "Status FriendlyName\nOK     Virtual Display Driver\n",
            )
        return CommandResult(("pwsh",), 0, "{}")


def _report(
    *,
    sections: tuple[DiagnosticSection, ...],
    dry_run: bool = True,
    admin: bool = True,
    state_dir: Path | None = None,
) -> DiagnosticsReport:
    return DiagnosticsReport(
        sections=sections,
        generated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        dry_run=dry_run,
        admin=admin,
        state_dir=state_dir,
    )


class CollectDiagnosticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.state_dir = self.root / "CampusStreamHelper"
        self.state_dir.mkdir()

        sunshine_dir = self.root / "Sunshine"
        config_dir = sunshine_dir / "config"
        config_dir.mkdir(parents=True)
        tools_dir = sunshine_dir / "tools"
        tools_dir.mkdir()
        self.executable = sunshine_dir / "sunshine.exe"
        self.executable.write_text("exe", encoding="utf-8")
        self.sunshinesvc = tools_dir / "sunshinesvc.exe"
        self.sunshinesvc.write_text("svc", encoding="utf-8")
        self.config_path = config_dir / "sunshine.conf"
        self.config_path.write_text(
            "port = 47989\nusername = alice\npassword = top-secret\n",
            encoding="utf-8",
        )
        self.log_path = config_dir / "sunshine.log"
        self.log_path.write_text(
            "\n".join(f"log line {index}" for index in range(80)),
            encoding="utf-8",
        )
        self.sunshine = SunshineStatus(
            installed=True,
            install_dir=sunshine_dir,
            executable=self.executable,
            config_path=self.config_path,
            service_name="SunshineService",
            service_state="running",
            version="2026.1",
            detail="test sunshine",
            sunshinesvc_path=self.sunshinesvc,
            sunshinesvc_exists=True,
            service_image_path=self.executable,
            service_image_exists=True,
        )

        vdd_dir = self.root / "VirtualDisplayDriver"
        vdd_dir.mkdir()
        self.vdd_settings = vdd_dir / "vdd_settings.xml"
        self.vdd_settings.write_text(
            "<vdd_settings><monitors><count>1</count></monitors></vdd_settings>",
            encoding="utf-8",
        )
        self.vdd = VddStatus(
            installed=True,
            install_dir=vdd_dir,
            settings_path=self.vdd_settings,
            driver_version="1.2.3",
            schema="resolutions",
            configured_monitor_count=1,
            configured_resolution_count=2,
            detail="test vdd",
            device_ready=True,
        )

        self.network = NetworkReport(
            adapters=(
                AdapterInfo(
                    name="以太网",
                    description="Realtek",
                    kind="ethernet",
                    is_up=True,
                    ipv4="192.168.1.10",
                    global_ipv6=("2408:8207::1",),
                    ipv6_binding_enabled=True,
                ),
            ),
            scenario="ethernet_ready",
            preferred_ipv4="192.168.1.10",
            preferred_ipv6="2408:8207::1",
            firewall_rule_present=True,
            warnings=(),
            generated_at="2026-01-02T03:04:05+08:00",
        )

        (self.state_dir / APP_LOG_FILENAME).write_text(
            "line1\nline2\n",
            encoding="utf-8",
        )
        (self.state_dir / CRASH_LOG_FILENAME).write_text(
            "header\nTraceback (most recent call last):\n"
            '  File "app.py", line 1\nValueError: boom\n',
            encoding="utf-8",
        )

    def _collect(
        self,
        *,
        sunshine_detector=None,
        vdd_detector=None,
        network_collector=None,
    ):
        progress: list[tuple[int, str]] = []
        report = collect_diagnostics(
            DiagnosticsRunner(),
            dry_run=True,
            state_dir=self.state_dir,
            progress=lambda percent, message: progress.append((percent, message)),
            admin_check=lambda: True,
            port_connector=lambda port: port in {47990, 48010},
            sunshine_detector=sunshine_detector or (lambda _runner: self.sunshine),
            vdd_detector=vdd_detector or (lambda _runner: self.vdd),
            network_collector=network_collector
            or (lambda _runner, sunshine_exe=None: self.network),
            generated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )
        return report, progress

    def test_collects_all_six_sections(self) -> None:
        report, progress = self._collect()
        self.assertEqual(len(report.sections), 6)
        self.assertEqual(report.failed_sections, ())
        self.assertTrue(report.dry_run)
        self.assertTrue(report.admin)
        self.assertEqual(progress[-1], (100, "诊断报告生成完成"))

        by_key = {section.key: section.content for section in report.sections}
        self.assertIn("Windows 11 专业版 24H2", by_key["system"])
        self.assertIn("26100.1234", by_key["system"])
        self.assertIn("ProxyServer", by_key["system"])
        self.assertIn("Virtual Display Driver", by_key["display"])
        self.assertIn("tools\\sunshinesvc.exe", by_key["sunshine"])
        self.assertIn("127.0.0.1:47990", by_key["sunshine"])
        self.assertIn("✅ 正在监听", by_key["sunshine"])
        self.assertIn("port = 47989", by_key["sunshine"])
        self.assertIn("log line 79", by_key["sunshine"])
        self.assertNotIn("log line 29", by_key["sunshine"])
        self.assertIn("<vdd_settings>", by_key["vdd"])
        self.assertIn("192.168.1.10", by_key["network"])
        self.assertIn("2408:8207::1", by_key["network"])
        self.assertIn("已存在", by_key["network"])
        self.assertIn("Traceback", by_key["app_logs"])
        self.assertIn("ValueError: boom", by_key["app_logs"])

        markdown = report.to_markdown(redact=True)
        self.assertNotIn("top-secret", markdown)
        self.assertIn("<已脱敏>", markdown)
        self.assertIn("自动脱敏：已开启", markdown)

    def test_section_failure_does_not_abort_other_sections(self) -> None:
        def broken_vdd(_runner):
            raise RuntimeError("VDD 查询失败")

        def broken_network(_runner, sunshine_exe=None):
            raise OSError("网络查询失败")

        report, _progress = self._collect(
            vdd_detector=broken_vdd,
            network_collector=broken_network,
        )
        failed = {section.key for section in report.failed_sections}
        self.assertEqual(failed, {"vdd", "network"})
        self.assertEqual(len(report.sections), 6)
        self.assertIn("VDD 查询失败", report.to_markdown())
        markdown = report.to_markdown()
        self.assertIn("采集失败", markdown)
        self.assertIn("## 1. 系统与代理", markdown)

    def test_sunshine_detector_exception_is_reported(self) -> None:
        def broken_sunshine(_runner):
            raise RuntimeError("Sunshine 查询失败")

        report, _progress = self._collect(sunshine_detector=broken_sunshine)
        by_key = {section.key: section.content for section in report.sections}
        self.assertIn("Sunshine 查询失败", by_key["display"])
        self.assertIn("Sunshine 查询失败", by_key["sunshine"])
        self.assertIn("192.168.1.10", by_key["network"])


class FileLoggingTest(unittest.TestCase):
    def test_writes_app_log_without_duplicate_handlers(self) -> None:
        logger = logging.getLogger("campusstream")
        before_handlers = list(logger.handlers)
        previous_propagate = logger.propagate

        # Windows 上必须先关闭文件句柄，TemporaryDirectory 才能删除 app.log。
        with tempfile.TemporaryDirectory() as tmp:
            try:
                state_dir = Path(tmp) / "CampusStreamHelper"
                path = configure_file_logging(state_dir)
                self.assertIsNotNone(path)
                assert path is not None
                logger.info("diagnostic test line")
                self.assertIn(
                    "diagnostic test line",
                    path.read_text(encoding="utf-8"),
                )
                self.assertEqual(configure_file_logging(state_dir), path)

                file_handlers = [
                    handler
                    for handler in logger.handlers
                    if isinstance(handler, logging.FileHandler)
                    and Path(handler.baseFilename).resolve() == path.resolve()
                ]
                self.assertEqual(len(file_handlers), 1)
            finally:
                self._restore_logger(
                    logger,
                    before_handlers,
                    previous_propagate,
                )

    @staticmethod
    def _restore_logger(logger, handlers, propagate) -> None:
        for handler in list(logger.handlers):
            if handler not in handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.propagate = propagate


if __name__ == "__main__":
    unittest.main()
