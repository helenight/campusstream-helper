"""PackageWorker 测试：解析、下载、安装、取消与 Dry-Run。"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.common import OperationResult
from core.downloader import DownloadError
from tests.fakes import FakeRunner
from tests.test_downloader import FakeTransport
from ui.workers import PackageWorker


def small_payload() -> dict:
    return {
        "tag_name": "v2026.914.233613",
        "html_url": "https://example.test/release",
        "assets": [
            {
                "name": "Sunshine-Windows-AMD64-installer.msi",
                "browser_download_url": "https://example.test/amd64.msi",
                "size": 10,
            },
            {
                "name": "Sunshine-Windows-ARM64-installer.msi",
                "browser_download_url": "https://example.test/arm64.msi",
                "size": 10,
            },
        ],
    }


class PackageWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.downloads = self.root / "downloads"
        self.vdd_dir = self.root / "vdd"

    def _worker(
        self,
        transport,
        *,
        dry_run=False,
        local_path=None,
        require_confirmation=False,
        package="sunshine",
        vdd_wait_timeout_s=300.0,
    ):
        self.runner = FakeRunner(dry_run=dry_run)
        worker = PackageWorker(
            package,
            dry_run=dry_run,
            local_path=local_path,
            vdd_install_dir=self.vdd_dir,
            download_dir=self.downloads,
            transport=transport,
            require_confirmation=require_confirmation,
            runner_factory=lambda _dry: self.runner,
            sleep=lambda _seconds: None,
            vdd_wait_timeout_s=vdd_wait_timeout_s,
            vdd_poll_interval_s=0.1,
        )
        self.updates = []
        self.logs = []
        self.finished = []
        self.failed = []
        self.cancelled = []
        self.manual = []
        worker.update.connect(self.updates.append)
        worker.log.connect(self.logs.append)
        worker.finished.connect(self.finished.append)
        worker.failed.connect(self.failed.append)
        worker.cancelled.connect(lambda: self.cancelled.append(True))
        worker.manual_required.connect(self.manual.append)
        return worker

    def test_dry_run_resolves_without_download(self) -> None:
        transport = FakeTransport(api_results=[small_payload()])
        worker = self._worker(transport, dry_run=True)
        worker.run()

        self.assertFalse(transport.download_urls)
        self.assertEqual(self.cancelled, [])
        self.assertEqual(self.failed, [])
        self.assertEqual(len(self.finished), 1)
        result = self.finished[0]
        self.assertTrue(result.ok)
        self.assertTrue(result.simulated)
        self.assertIn("msiexec", result.details[0])
        self.assertTrue(any("https://example.test/" in line for line in self.logs))
        self.assertEqual(self.updates[-1].stage, "done")

    def test_download_then_install(self) -> None:
        transport = FakeTransport(
            api_results=[small_payload()],
            download_results=["ok"],
        )
        worker = self._worker(transport)
        worker.run()

        self.assertEqual(len(transport.download_urls), 1)
        self.assertEqual(self.failed, [])
        self.assertEqual(len(self.finished), 1)
        self.assertTrue(self.finished[0].ok)
        self.assertFalse(self.finished[0].simulated)
        self.assertTrue(any(update.stage == "download" for update in self.updates))
        self.assertEqual(self.runner.commands[0][0], "msiexec")
        self.assertTrue(
            (self.downloads / "Sunshine-Windows-AMD64-installer.msi").is_file()
            or (self.downloads / "Sunshine-Windows-ARM64-installer.msi").is_file()
        )

    def test_manual_required_after_api_failures(self) -> None:
        transport = FakeTransport(
            api_results=[DownloadError("direct blocked"), DownloadError("proxy blocked")]
        )
        worker = self._worker(transport)
        worker._proxy = "https://gh-proxy.test/"
        worker.run()

        self.assertEqual(self.finished, [])
        self.assertEqual(len(self.failed), 1)
        self.assertEqual(len(self.manual), 1)
        self.assertEqual(self.manual[0].package, "sunshine")
        self.assertIn("Sunshine", self.manual[0].release_page)

    def test_download_error_offers_manual_fallback(self) -> None:
        transport = FakeTransport(
            api_results=[small_payload()],
            download_results=[DownloadError("network reset"), DownloadError("timeout")],
        )
        worker = self._worker(transport)
        worker.run()

        self.assertEqual(self.finished, [])
        self.assertEqual(len(self.manual), 1)
        self.assertEqual(self.manual[0].package, "sunshine")
        self.assertIn("Sunshine", self.manual[0].release_page)
        self.assertIn("timeout", self.failed[0])

    def test_local_package_skips_network(self) -> None:
        msi = self.root / "local.msi"
        msi.write_bytes(b"x")
        transport = FakeTransport(
            api_results=[DownloadError("must not be called")],
        )
        worker = self._worker(transport, local_path=msi)
        worker.run()

        self.assertEqual(transport.json_urls, [])
        self.assertEqual(transport.download_urls, [])
        self.assertEqual(self.failed, [])
        self.assertTrue(self.finished[0].ok)
        self.assertEqual(self.runner.commands[0][0], "msiexec")

    def test_cancel_before_start(self) -> None:
        transport = FakeTransport(api_results=[small_payload()])
        worker = self._worker(transport)
        worker.request_cancel()
        worker.run()

        self.assertEqual(len(self.cancelled), 1)
        self.assertEqual(transport.json_urls, [])
        self.assertEqual(self.finished, [])

    def _vdd_local_worker(self, *, timeout: float = 300.0) -> PackageWorker:
        self.vdd_dir.mkdir(parents=True)
        (self.vdd_dir / "VDD.Control.exe").write_bytes(b"exe")
        archive = self.root / "VDD.Control.25.7.23.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("VDD.Control.exe", b"exe")
        return self._worker(
            FakeTransport(),
            package="vdd",
            local_path=archive,
            vdd_wait_timeout_s=timeout,
        )

    def test_vdd_ready_after_install_skips_prompt(self) -> None:
        worker = self._vdd_local_worker()
        deployed = OperationResult.success("已部署 VDD 文件")
        with (
            patch("ui.workers.install_local_package", return_value=deployed),
            patch(
                "ui.workers.detect_vdd",
                return_value=SimpleNamespace(device_ready=True),
            ),
        ):
            worker.run()

        self.assertEqual(self.failed, [])
        self.assertTrue(self.finished[0].ok)
        self.assertIn("已就绪", self.finished[0].message)
        self.assertNotIn("vdd_prompt", [update.stage for update in self.updates])

    def test_vdd_waits_until_driver_ready(self) -> None:
        worker = self._vdd_local_worker()
        deployed = OperationResult.success("已部署 VDD 文件")
        with (
            patch("ui.workers.install_local_package", return_value=deployed),
            patch(
                "ui.workers.detect_vdd",
                side_effect=[
                    SimpleNamespace(device_ready=False),
                    SimpleNamespace(device_ready=True),
                ],
            ),
        ):
            worker.run()

        self.assertEqual(self.failed, [])
        self.assertTrue(self.finished[0].ok)
        self.assertIn("Step 1 将自动刷新", self.finished[0].message)
        stages = [update.stage for update in self.updates]
        self.assertIn("vdd_prompt", stages)
        self.assertIn("vdd_wait", stages)
        self.assertTrue(
            any(
                "Virtual Display Driver → System → Install Driver" in line
                for line in self.logs
            )
        )

    def test_vdd_wait_timeout_is_non_fatal(self) -> None:
        worker = self._vdd_local_worker(timeout=0)
        deployed = OperationResult.success("已部署 VDD 文件")
        with (
            patch("ui.workers.install_local_package", return_value=deployed),
            patch(
                "ui.workers.detect_vdd",
                return_value=SimpleNamespace(device_ready=False),
            ),
        ):
            worker.run()

        self.assertEqual(self.failed, [])
        self.assertTrue(self.finished[0].ok)
        self.assertIn("暂未检测到驱动就绪", self.finished[0].message)


if __name__ == "__main__":
    unittest.main()
