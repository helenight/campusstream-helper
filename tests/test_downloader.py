"""下载与安装核心测试：不访问真实网络，不触碰系统安装目录。"""

from __future__ import annotations

import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from core.downloader import (
    DownloadCancelled,
    DownloadError,
    ManualDownloadRequired,
    PackagePlan,
    ReleaseAsset,
    ReleaseInfo,
    apply_proxy,
    detect_arch,
    download_asset,
    extract_zip_safe,
    install_command,
    install_local_package,
    install_sunshine,
    install_vdd,
    resolve_package,
    select_sunshine_asset,
    select_vdd_asset,
)
from tests.fakes import FakeRunner


SUNSHINE_PAYLOAD = {
    "tag_name": "v2026.914.233613",
    "html_url": "https://github.com/SunshineStream/Sunshine/releases/tag/v2026.914.233613",
    "assets": [
        {
            "name": "Sunshine-Windows-AMD64-installer.msi",
            "browser_download_url": "https://github.com/SunshineStream/Sunshine/releases/download/v2026.914.233613/Sunshine-Windows-AMD64-installer.msi",
            "size": 33_710_080,
        },
        {
            "name": "Sunshine-Windows-ARM64-installer.msi",
            "browser_download_url": "https://github.com/SunshineStream/Sunshine/releases/download/v2026.914.233613/Sunshine-Windows-ARM64-installer.msi",
            "size": 24_541_615,
        },
        {
            "name": "Sunshine-Windows-AMD64-lite.zip",
            "browser_download_url": "https://example.test/lite.zip",
            "size": 38_060_196,
        },
        {
            "name": "Sunshine-Windows-AMD64-debuginfo.7z",
            "browser_download_url": "https://example.test/debuginfo.7z",
            "size": 164_295_875,
        },
    ],
}

VDD_PAYLOAD = {
    "tag_name": "25.7.23",
    "html_url": "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/tag/25.7.23",
    "assets": [
        {
            "name": "VDD.Control.25.7.23.zip",
            "browser_download_url": "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/25.7.23/VDD.Control.25.7.23.zip",
            "size": 71_225_903,
        },
        {
            "name": "VirtualDisplayDriver-x86.Driver.Only.zip",
            "browser_download_url": "https://example.test/driver.zip",
            "size": 132_118,
        },
    ],
}


class FakeTransport:
    def __init__(self, api_results=None, download_results=None) -> None:
        self.api_results = list(api_results or [])
        self.download_results = list(download_results or [])
        self.json_urls: list[str] = []
        self.download_urls: list[str] = []
        self.timeouts: list[float] = []
        self.chunks: list[tuple[int, int]] = []

    def get_json(self, url: str, *, timeout: float):
        self.json_urls.append(url)
        self.timeouts.append(timeout)
        result = self.api_results.pop(0) if self.api_results else {}
        if isinstance(result, Exception):
            raise result
        return result

    def download(
        self,
        url: str,
        dest: Path,
        *,
        expected_size: int,
        timeout: float,
        on_chunk=None,
        cancel=None,
    ) -> int:
        self.download_urls.append(url)
        self.timeouts.append(timeout)
        result = self.download_results.pop(0) if self.download_results else "ok"
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("下载已取消")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(result, Exception):
            dest.write_bytes(b"partial")
            raise result
        size = expected_size if result == "ok" else int(result)
        dest.write_bytes(b"x" * size)
        self.chunks.append((size, expected_size or size))
        if on_chunk is not None:
            on_chunk(size, expected_size or size)
        return size


def make_plan(size: int = 10, name: str = "Sunshine-Windows-AMD64-installer.msi") -> PackagePlan:
    asset = ReleaseAsset(name=name, download_url="https://example.test/asset", size=size)
    release = ReleaseInfo(
        repo="LizardByte/Sunshine",
        tag="v1",
        page_url="https://example.test/release",
        assets=(asset,),
    )
    return PackagePlan("sunshine", release, asset, asset.download_url)


def write_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


class ResolveTest(unittest.TestCase):
    def test_amd64_and_arm64_selection(self) -> None:
        amd64 = resolve_package(
            "sunshine",
            transport=FakeTransport([SUNSHINE_PAYLOAD]),
            arch="amd64",
        )
        self.assertEqual(amd64.asset.name, "Sunshine-Windows-AMD64-installer.msi")
        self.assertEqual(amd64.release.tag, "v2026.914.233613")
        self.assertIn("SunshineStream", amd64.release.page_url)

        arm64 = resolve_package(
            "sunshine",
            transport=FakeTransport([SUNSHINE_PAYLOAD]),
            arch="arm64",
        )
        self.assertEqual(arm64.asset.name, "Sunshine-Windows-ARM64-installer.msi")

    def test_vdd_asset_selection(self) -> None:
        plan = resolve_package("vdd", transport=FakeTransport([VDD_PAYLOAD]))
        self.assertEqual(plan.asset.name, "VDD.Control.25.7.23.zip")
        self.assertEqual(plan.release.tag, "25.7.23")
        self.assertEqual(plan.display_name, "Virtual Display Driver")

    def test_missing_asset_requires_manual_download(self) -> None:
        payload = {
            "tag_name": "v1",
            "html_url": "https://example.test/release",
            "assets": [{"name": "other.zip", "browser_download_url": "https://x", "size": 1}],
        }
        with self.assertRaises(ManualDownloadRequired) as ctx:
            resolve_package("sunshine", transport=FakeTransport([payload]), arch="amd64")
        self.assertEqual(ctx.exception.package, "sunshine")
        self.assertIn("other.zip", ctx.exception.details)

    def test_select_helpers(self) -> None:
        release = ReleaseInfo(
            "repo",
            "v1",
            "https://example.test",
            tuple(
                ReleaseAsset(
                    item["name"],
                    item["browser_download_url"],
                    item["size"],
                )
                for item in SUNSHINE_PAYLOAD["assets"]
            ),
        )
        self.assertEqual(
            select_sunshine_asset(release, "amd64").name,
            "Sunshine-Windows-AMD64-installer.msi",
        )
        vdd_release = ReleaseInfo(
            "repo",
            "v1",
            "https://example.test",
            tuple(
                ReleaseAsset(
                    item["name"],
                    item["browser_download_url"],
                    item["size"],
                )
                for item in VDD_PAYLOAD["assets"]
            ),
        )
        self.assertEqual(select_vdd_asset(vdd_release).name, "VDD.Control.25.7.23.zip")

    def test_unknown_arch(self) -> None:
        with self.assertRaises(DownloadError):
            detect_arch("mips")
        self.assertEqual(detect_arch("AMD64"), "amd64")
        self.assertEqual(detect_arch("aarch64"), "arm64")


class ProxyTest(unittest.TestCase):
    def test_apply_proxy(self) -> None:
        self.assertEqual(
            apply_proxy("https://github.com/a", "https://gh-proxy.test/"),
            "https://gh-proxy.test/https://github.com/a",
        )
        self.assertEqual(apply_proxy("https://github.com/a", ""), "https://github.com/a")
        with self.assertRaises(ValueError):
            apply_proxy("https://github.com/a", "gh-proxy.test")

    def test_api_falls_back_to_proxy_and_asset_is_proxied(self) -> None:
        transport = FakeTransport(
            api_results=[DownloadError("direct blocked"), SUNSHINE_PAYLOAD]
        )
        plan = resolve_package(
            "sunshine",
            transport=transport,
            proxy_prefix="https://gh-proxy.test/",
            arch="amd64",
        )
        self.assertEqual(len(transport.json_urls), 2)
        self.assertTrue(transport.json_urls[0].startswith("https://api.github.com/"))
        self.assertEqual(
            transport.json_urls[1],
            "https://gh-proxy.test/https://api.github.com/repos/LizardByte/Sunshine/releases/latest",
        )
        self.assertTrue(
            plan.download_url.startswith(
                "https://gh-proxy.test/https://github.com/"
            )
        )

    def test_api_failure_raises_manual_fallback(self) -> None:
        transport = FakeTransport(
            api_results=[DownloadError("timeout"), DownloadError("blocked")]
        )
        with self.assertRaises(ManualDownloadRequired) as ctx:
            resolve_package(
                "vdd",
                transport=transport,
                proxy_prefix="https://gh-proxy.test/",
            )
        error = ctx.exception
        self.assertEqual(error.package, "vdd")
        self.assertEqual(
            error.release_page,
            "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/latest",
        )
        self.assertEqual(len(error.details), 2)


class DownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_download_success_and_progress(self) -> None:
        transport = FakeTransport()
        path = download_asset(
            make_plan(10),
            self.dir,
            transport=transport,
            retry_delay=0,
        )
        self.assertEqual(path.read_bytes(), b"x" * 10)
        self.assertEqual(transport.chunks, [(10, 10)])

    def test_download_retries_once(self) -> None:
        transport = FakeTransport(
            download_results=[DownloadError("network reset"), "ok"]
        )
        path = download_asset(
            make_plan(10),
            self.dir,
            transport=transport,
            retry_delay=0,
        )
        self.assertEqual(path.stat().st_size, 10)
        self.assertEqual(len(transport.download_urls), 2)

    def test_size_mismatch_cleans_up(self) -> None:
        transport = FakeTransport(download_results=[5, 5])
        with self.assertRaises(DownloadError):
            download_asset(
                make_plan(10),
                self.dir,
                transport=transport,
                retry_delay=0,
            )
        self.assertFalse((self.dir / "Sunshine-Windows-AMD64-installer.msi").exists())
        self.assertEqual(len(transport.download_urls), 2)

    def test_existing_file_is_reused(self) -> None:
        target = self.dir / "Sunshine-Windows-AMD64-installer.msi"
        target.write_bytes(b"x" * 10)
        transport = FakeTransport()
        path = download_asset(
            make_plan(10),
            self.dir,
            transport=transport,
            retry_delay=0,
        )
        self.assertEqual(path, target)
        self.assertFalse(transport.download_urls)

    def test_cancel_aborts(self) -> None:
        cancel = threading.Event()
        cancel.set()
        transport = FakeTransport()
        with self.assertRaises(DownloadCancelled):
            download_asset(
                make_plan(10),
                self.dir,
                transport=transport,
                cancel=cancel,
                retry_delay=0,
            )
        self.assertFalse((self.dir / "Sunshine-Windows-AMD64-installer.msi").exists())

    def test_unsafe_asset_name_rejected(self) -> None:
        with self.assertRaises(DownloadError):
            download_asset(
                make_plan(10, name="../evil.msi"),
                self.dir,
                transport=FakeTransport(),
                retry_delay=0,
            )


class InstallSunshineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_command_shape(self) -> None:
        argv = install_command("sunshine", Path(r"C:\Temp\sunshine.msi"))
        self.assertEqual(argv[0], "msiexec")
        self.assertIn("/passive", argv)
        self.assertIsNone(install_command("vdd", Path("x")))

    def test_dry_run_only_logs(self) -> None:
        msi = self.dir / "Sunshine.msi"
        msi.write_bytes(b"x")
        runner = FakeRunner(dry_run=True)
        result = install_sunshine(runner, msi, dry_run=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.simulated)
        self.assertIn("msiexec", result.details[0])
        self.assertFalse(runner.commands)

    def test_real_install_uses_msiexec(self) -> None:
        msi = self.dir / "Sunshine.msi"
        msi.write_bytes(b"x")
        runner = FakeRunner()
        result = install_sunshine(runner, msi, dry_run=False)
        self.assertTrue(result.ok)
        self.assertEqual(runner.commands[0][0], "msiexec")
        self.assertIn("/passive", runner.commands[0])

    def test_invalid_extension(self) -> None:
        path = self.dir / "Sunshine.zip"
        path.write_bytes(b"x")
        result = install_sunshine(FakeRunner(), path)
        self.assertFalse(result.ok)


class ZipSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_rejects_parent_traversal(self) -> None:
        archive = write_zip(self.dir / "bad.zip", {"../evil.txt": b"x"})
        with self.assertRaises(DownloadError):
            extract_zip_safe(archive, self.dir / "out")
        self.assertFalse((self.dir / "evil.txt").exists())

    def test_rejects_absolute_path(self) -> None:
        archive = write_zip(self.dir / "bad2.zip", {"/abs.txt": b"x"})
        with self.assertRaises(DownloadError):
            extract_zip_safe(archive, self.dir / "out2")

    def test_rejects_symlink_entry(self) -> None:
        path = self.dir / "link.zip"
        with zipfile.ZipFile(path, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.external_attr = 0o120777 << 16
            archive.writestr(info, b"target")
        with self.assertRaises(DownloadError):
            extract_zip_safe(path, self.dir / "out3")


class InstallVddTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _zip(self) -> Path:
        return write_zip(
            self.dir / "VDD.Control.25.7.23.zip",
            {
                "VDD.Control.exe": b"exe",
                "driver.sys": b"driver",
                "vdd_settings.xml": b"new-settings",
            },
        )

    def test_install_copies_and_preserves_settings(self) -> None:
        install_dir = self.dir / "install"
        install_dir.mkdir()
        (install_dir / "vdd_settings.xml").write_text("keep", encoding="utf-8")
        launched: list[Path] = []
        result = install_vdd(
            FakeRunner(),
            self._zip(),
            install_dir=install_dir,
            launcher=lambda path: launched.append(path) or True,
            staging_root=self.dir / "staging",
        )
        self.assertTrue(result.ok)
        self.assertEqual(
            (install_dir / "vdd_settings.xml").read_text(encoding="utf-8"), "keep"
        )
        self.assertTrue((install_dir / "VDD.Control.exe").is_file())
        self.assertTrue((install_dir / "driver.sys").is_file())
        self.assertEqual(launched[0].name, "VDD.Control.exe")

    def test_dry_run_does_not_extract(self) -> None:
        install_dir = self.dir / "not-created"
        result = install_vdd(
            FakeRunner(dry_run=True),
            self._zip(),
            install_dir=install_dir,
            dry_run=True,
            staging_root=self.dir / "staging",
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.simulated)
        self.assertFalse(install_dir.exists())
        self.assertIn("VDD.Control.exe", " ".join(result.details))

    def test_nested_single_folder_is_flattened(self) -> None:
        archive = write_zip(
            self.dir / "nested.zip",
            {"VDD.Control.25.7.23/VDD.Control.exe": b"exe"},
        )
        install_dir = self.dir / "nested-install"
        result = install_vdd(
            FakeRunner(),
            archive,
            install_dir=install_dir,
            launcher=lambda path: True,
            staging_root=self.dir / "staging",
        )
        self.assertTrue(result.ok)
        self.assertTrue((install_dir / "VDD.Control.exe").is_file())

    def test_zip_slip_returns_failure(self) -> None:
        archive = write_zip(self.dir / "slip.zip", {"../evil.txt": b"x"})
        result = install_vdd(
            FakeRunner(),
            archive,
            install_dir=self.dir / "slip-install",
            staging_root=self.dir / "staging",
        )
        self.assertFalse(result.ok)
        self.assertIn("压缩包无效", result.message)

    def test_bat_entry_runs_through_cmd(self) -> None:
        archive = write_zip(self.dir / "bat.zip", {"install.bat": b"echo hi"})
        runner = FakeRunner()
        result = install_vdd(
            runner,
            archive,
            install_dir=self.dir / "bat-install",
            staging_root=self.dir / "staging",
        )
        self.assertTrue(result.ok)
        self.assertEqual(runner.commands[0][0], "cmd.exe")
        self.assertEqual(runner.commands[0][1], "/c")

    def test_no_entry_still_deploys_files(self) -> None:
        archive = write_zip(self.dir / "files.zip", {"readme.txt": b"hello"})
        install_dir = self.dir / "files-install"
        result = install_vdd(
            FakeRunner(),
            archive,
            install_dir=install_dir,
            staging_root=self.dir / "staging",
        )
        self.assertTrue(result.ok)
        self.assertIn("未找到自动安装入口", result.message)
        self.assertTrue((install_dir / "readme.txt").is_file())

    def test_local_package_dispatch(self) -> None:
        msi = self.dir / "local.msi"
        msi.write_bytes(b"x")
        result = install_local_package(
            FakeRunner(dry_run=True),
            "sunshine",
            msi,
            dry_run=True,
        )
        self.assertTrue(result.simulated)


if __name__ == "__main__":
    unittest.main()
