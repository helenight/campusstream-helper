"""下载安装对话框测试：离屏运行，不访问网络、不真实安装。"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from core.downloader import DownloadError
    from tests.fakes import FakeRunner
    from tests.test_downloader import FakeTransport
    from tests.test_package_worker import small_payload
    from ui.dialogs import PackageInstallDialog
    from ui.dialogs.package_dialog import PROXY_SETTINGS_KEY
except ImportError:  # PySide6 未安装时跳过
    QApplication = None  # type: ignore[assignment]
    PackageInstallDialog = None  # type: ignore[assignment]


class BlockingTransport(FakeTransport):
    def __init__(self, payload: dict) -> None:
        super().__init__(api_results=[payload])
        self.entered = threading.Event()
        self.release = threading.Event()

    def get_json(self, url: str, *, timeout: float):
        self.entered.set()
        self.release.wait(5)
        return super().get_json(url, timeout=timeout)


@unittest.skipIf(QApplication is None, "PySide6 未安装")
class PackageDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.downloads = self.root / "downloads"
        self.settings = QSettings(
            str(self.root / "settings.ini"),
            QSettings.Format.IniFormat,
        )
        self.runner = FakeRunner()
        self.dialogs: list[PackageInstallDialog] = []
        self.blocking: BlockingTransport | None = None

    def tearDown(self) -> None:
        if self.blocking is not None:
            self.blocking.release.set()
        for dialog in self.dialogs:
            if dialog._task_active and dialog._worker is not None:
                dialog._worker.request_cancel()
                self._wait(dialog, timeout=5)
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()
        if self.blocking is not None:
            self.blocking.release.set()

    def _dialog(self, transport, *, dry_run: bool = False) -> PackageInstallDialog:
        dialog = PackageInstallDialog(
            None,
            "sunshine",
            dry_run=dry_run,
            download_dir=self.downloads,
            vdd_install_dir=self.root / "vdd",
            transport=transport,
            settings=self.settings,
            require_confirmation=False,
            auto_start=False,
            runner_factory=lambda _dry: self.runner,
        )
        self.dialogs.append(dialog)
        return dialog

    def _wait(self, dialog: PackageInstallDialog, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not dialog.is_running and dialog._thread is None:
                return
            time.sleep(0.01)
        self.fail("下载安装任务未在超时时间内完成")

    def test_dry_run_shows_progress_and_records_command(self) -> None:
        transport = FakeTransport(api_results=[small_payload()])
        dialog = self._dialog(transport, dry_run=True)
        installed: list[str] = []
        dialog.installed.connect(installed.append)

        dialog.start()
        self._wait(dialog)

        self.assertEqual(installed, [])
        self.assertFalse(transport.download_urls)
        self.assertEqual(dialog.progress_bar.value(), 100)
        self.assertEqual(dialog.percent_label.text(), "100%")
        self.assertEqual(dialog.status_pill.text(), "Dry-Run 完成")
        self.assertIn("msiexec", dialog.log_view.toPlainText())
        self.assertEqual(dialog.start_button.text(), "再次 Dry-Run")
        self.assertTrue(dialog.start_button.isEnabled())

    def test_real_install_emits_installed(self) -> None:
        transport = FakeTransport(
            api_results=[small_payload()],
            download_results=["ok"],
        )
        dialog = self._dialog(transport)
        installed: list[str] = []
        dialog.installed.connect(installed.append)

        dialog.start()
        self._wait(dialog)

        self.assertEqual(installed, ["sunshine"])
        self.assertEqual(dialog.status_pill.text(), "安装完成")
        self.assertEqual(dialog.progress_bar.value(), 100)
        self.assertFalse(dialog.notice.isHidden())
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertEqual(dialog.start_button.text(), "重新下载并安装")
        self.assertEqual(self.runner.commands[0][0], "msiexec")
        self.assertIn("安装完成", dialog.log_view.toPlainText())

    def test_manual_fallback_after_api_failures(self) -> None:
        transport = FakeTransport(
            api_results=[DownloadError("direct blocked"), DownloadError("proxy blocked")]
        )
        dialog = self._dialog(transport)
        dialog.proxy_edit.setText("https://gh-proxy.test/")
        installed: list[str] = []
        dialog.installed.connect(installed.append)

        dialog.start()
        self._wait(dialog)

        self.assertEqual(installed, [])
        self.assertTrue(dialog._manual_error)
        self.assertIn("github.com", dialog._release_page)
        self.assertFalse(dialog.notice.isHidden())
        self.assertTrue(dialog.open_button.isEnabled())
        self.assertTrue(dialog.local_button.isEnabled())
        self.assertIn("选择本地安装包", dialog.log_view.toPlainText())

    def test_local_package_skips_network(self) -> None:
        msi = self.root / "local.msi"
        msi.write_bytes(b"x")
        transport = FakeTransport(
            api_results=[DownloadError("must not be called")],
        )
        dialog = self._dialog(transport)
        installed: list[str] = []
        dialog.installed.connect(installed.append)

        dialog.start(msi)
        self._wait(dialog)

        self.assertEqual(installed, ["sunshine"])
        self.assertEqual(transport.json_urls, [])
        self.assertEqual(transport.download_urls, [])
        self.assertEqual(self.runner.commands[0][0], "msiexec")
        self.assertIn("使用本地安装包", dialog.log_view.toPlainText())

    def test_proxy_is_persisted(self) -> None:
        transport = BlockingTransport(small_payload())
        self.blocking = transport
        dialog = self._dialog(transport, dry_run=True)
        dialog.proxy_edit.setText("https://gh-proxy.test/")
        dialog.start()
        self.assertTrue(transport.entered.wait(2))

        self.assertEqual(dialog.cancel_button.text(), "取消")
        self.assertFalse(dialog.start_button.isEnabled())
        self.assertFalse(dialog.proxy_edit.isEnabled())
        self.assertEqual(
            self.settings.value(PROXY_SETTINGS_KEY),
            "https://gh-proxy.test/",
        )

        transport.release.set()
        self._wait(dialog)

    def test_controls_lock_while_running(self) -> None:
        transport = BlockingTransport(small_payload())
        self.blocking = transport
        dialog = self._dialog(transport, dry_run=True)
        dialog.start()
        self.assertTrue(transport.entered.wait(2))

        self.assertFalse(dialog.start_button.isEnabled())
        self.assertFalse(dialog.proxy_edit.isEnabled())
        self.assertFalse(dialog.open_button.isEnabled())
        self.assertFalse(dialog.local_button.isEnabled())
        self.assertTrue(dialog.cancel_button.isEnabled())

        transport.release.set()
        self._wait(dialog)
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertTrue(dialog.open_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
