"""Sunshine / VDD 的 GitHub Release 解析、下载与安装。

设计约束：
- 网络与文件操作都可注入，测试不访问真实网络；
- Dry-Run 只解析链接并记录将执行的命令，不下载、不解压、不安装；
- 下载完成后按 API 提供的文件大小校验；
- ZIP 解压拒绝绝对路径、``..`` 与符号链接，且不覆盖已有 ``vdd_settings.xml``。
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.common import CommandRunner, OperationResult, default_state_dir

Package = Literal["sunshine", "vdd"]

SUNSHINE_REPO = "LizardByte/Sunshine"
VDD_REPO = "VirtualDrivers/Virtual-Display-Driver"
SUNSHINE_PAGE = "https://github.com/LizardByte/Sunshine/releases/latest"
VDD_PAGE = "https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/latest"
DEFAULT_VDD_DIR = Path(r"C:\VirtualDisplayDriver")

API_TIMEOUT = 10.0
DOWNLOAD_READ_TIMEOUT = 30.0
MAX_ATTEMPTS = 2
CHUNK_SIZE = 256 * 1024

ProgressCallback = Callable[[int, int], None]
LogCallback = Callable[[str], None]

_HTTP_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "CampusStreamHelper/0.2.0",
}


class DownloadError(RuntimeError):
    """下载或安装流程中的可展示错误。"""


class DownloadCancelled(DownloadError):
    """用户主动取消。"""


class ManualDownloadRequired(DownloadError):
    """需要界面提供「打开浏览器」或「选择本地安装包」兜底。"""

    def __init__(
        self,
        message: str,
        *,
        package: Package,
        release_page: str,
        details: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.package = package
        self.release_page = release_page
        self.details = details


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    repo: str
    tag: str
    page_url: str
    assets: tuple[ReleaseAsset, ...]


@dataclass(frozen=True, slots=True)
class PackagePlan:
    package: Package
    release: ReleaseInfo
    asset: ReleaseAsset
    download_url: str

    @property
    def display_name(self) -> str:
        return "Sunshine" if self.package == "sunshine" else "Virtual Display Driver"


class HttpTransport(Protocol):
    def get_json(self, url: str, *, timeout: float) -> dict[str, Any]: ...

    def download(
        self,
        url: str,
        dest: Path,
        *,
        expected_size: int,
        timeout: float,
        on_chunk: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> int: ...


class UrllibTransport:
    """标准库实现；自动跟随 GitHub 的 301 重定向。"""

    def get_json(self, url: str, *, timeout: float) -> dict[str, Any]:
        request = Request(url, headers=_HTTP_HEADERS)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = _read_error_body(exc)
            raise DownloadError(f"GitHub API 返回 HTTP {exc.code}：{detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DownloadError(f"GitHub API 请求失败：{exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DownloadError(f"GitHub API 返回无法解析：{exc}") from exc
        if not isinstance(payload, dict):
            raise DownloadError("GitHub API 返回了非对象 JSON")
        return payload

    def download(
        self,
        url: str,
        dest: Path,
        *,
        expected_size: int,
        timeout: float,
        on_chunk: ProgressCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = _part_path(dest)
        request = Request(url, headers={"User-Agent": _HTTP_HEADERS["User-Agent"]})
        received = 0
        try:
            with urlopen(request, timeout=timeout) as response, part.open("wb") as handle:
                header = response.headers.get("Content-Length")
                total = expected_size or (int(header) if header and header.isdigit() else 0)
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled("下载已取消")
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    if on_chunk is not None:
                        on_chunk(received, total or received)
            if expected_size > 0 and received != expected_size:
                raise DownloadError(
                    f"文件大小不一致：期望 {expected_size} 字节，实际 {received} 字节"
                )
            os.replace(part, dest)
            return received
        except HTTPError as exc:
            raise DownloadError(
                f"下载返回 HTTP {exc.code}：{_read_error_body(exc)}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DownloadError(f"下载失败：{exc}") from exc
        finally:
            if part.exists():
                try:
                    part.unlink()
                except OSError:
                    pass


def _read_error_body(exc: HTTPError) -> str:
    try:
        text = exc.read().decode("utf-8", "replace")
        payload = json.loads(text)
        if isinstance(payload, dict) and payload.get("message"):
            return str(payload["message"])
        return " ".join(text.split())[:200]
    except Exception:
        return "未知错误"


def _part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def apply_proxy(url: str, prefix: str) -> str:
    """把 GitHub 链接拼接到用户提供的加速前缀后。"""
    value = (prefix or "").strip()
    if not value:
        return url
    if not value.lower().startswith(("http://", "https://")):
        raise ValueError("代理前缀必须以 http:// 或 https:// 开头")
    return value.rstrip("/") + "/" + url


def detect_arch(machine: str | None = None) -> Literal["amd64", "arm64"]:
    value = (machine or platform.machine()).lower()
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"amd64", "x86_64", "x64"}:
        return "amd64"
    raise DownloadError(f"无法识别的 CPU 架构：{machine or platform.machine()}")


def _fallback_page(repo: str) -> str:
    return f"https://github.com/{repo}/releases/latest"


def _parse_release(repo: str, payload: dict[str, Any]) -> ReleaseInfo:
    tag = str(payload.get("tag_name") or payload.get("name") or "latest")
    page_url = str(payload.get("html_url") or _fallback_page(repo))
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        message = str(payload.get("message") or "Release 中没有可下载资产")
        raise DownloadError(message)
    assets: list[ReleaseAsset] = []
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("browser_download_url") or "").strip()
        if not name or not url:
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        assets.append(ReleaseAsset(name=name, download_url=url, size=size))
    if not assets:
        raise DownloadError("Release 资产列表为空")
    return ReleaseInfo(repo=repo, tag=tag, page_url=page_url, assets=tuple(assets))


def fetch_release(
    repo: str,
    *,
    transport: HttpTransport | None = None,
    proxy_prefix: str = "",
    timeout: float = API_TIMEOUT,
) -> ReleaseInfo:
    """请求 latest release；直连失败时按需重试代理。"""
    transport = transport or UrllibTransport()
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    candidates = [api_url]
    if proxy_prefix.strip():
        proxies = apply_proxy(api_url, proxy_prefix)
        if proxies != api_url:
            candidates.append(proxies)

    errors: list[str] = []
    for url in candidates:
        try:
            payload = transport.get_json(url, timeout=timeout)
            return _parse_release(repo, payload)
        except DownloadError as exc:
            errors.append(str(exc))
        except Exception as exc:  # 传输层实现可能抛出任意网络异常
            errors.append(f"{type(exc).__name__}: {exc}")
    raise ManualDownloadRequired(
        "无法获取 GitHub 最新版本信息",
        package="sunshine" if repo == SUNSHINE_REPO else "vdd",
        release_page=_fallback_page(repo),
        details=tuple(errors),
    )


def select_sunshine_asset(
    release: ReleaseInfo,
    arch: Literal["amd64", "arm64"],
) -> ReleaseAsset:
    pattern = re.compile(
        rf"^sunshine-windows-{arch}-installer\.msi$", re.IGNORECASE
    )
    for asset in release.assets:
        if pattern.match(asset.name):
            return asset
    for asset in release.assets:
        lower = asset.name.lower()
        if (
            lower.endswith(".msi")
            and arch in lower
            and "windows" in lower
            and "debug" not in lower
        ):
            return asset
    raise ManualDownloadRequired(
        f"未在 Release 中找到 Windows {arch.upper()} 安装包",
        package="sunshine",
        release_page=release.page_url,
        details=tuple(asset.name for asset in release.assets),
    )


def select_vdd_asset(release: ReleaseInfo) -> ReleaseAsset:
    pattern = re.compile(r"^vdd\.control\..*\.zip$", re.IGNORECASE)
    matches = [asset for asset in release.assets if pattern.match(asset.name)]
    if matches:
        return max(matches, key=lambda asset: asset.name.lower())
    raise ManualDownloadRequired(
        "未在 Release 中找到 VDD.Control 压缩包",
        package="vdd",
        release_page=release.page_url,
        details=tuple(asset.name for asset in release.assets),
    )


def resolve_package(
    package: Package,
    *,
    transport: HttpTransport | None = None,
    proxy_prefix: str = "",
    arch: str | None = None,
) -> PackagePlan:
    repo = SUNSHINE_REPO if package == "sunshine" else VDD_REPO
    release = fetch_release(repo, transport=transport, proxy_prefix=proxy_prefix)
    if package == "sunshine":
        asset = select_sunshine_asset(release, detect_arch(arch))
    else:
        asset = select_vdd_asset(release)
    if not asset.download_url.lower().startswith("https://"):
        raise ManualDownloadRequired(
            "Release 资产不是 HTTPS 链接，已拒绝下载",
            package=package,
            release_page=release.page_url,
            details=(asset.download_url,),
        )
    return PackagePlan(
        package=package,
        release=release,
        asset=asset,
        download_url=apply_proxy(asset.download_url, proxy_prefix),
    )


def default_download_dir() -> Path:
    return default_state_dir() / "downloads"


def download_asset(
    plan: PackagePlan,
    dest_dir: Path | None = None,
    *,
    transport: HttpTransport | None = None,
    on_chunk: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    log: LogCallback | None = None,
    retry_delay: float = 0.8,
) -> Path:
    """下载并校验资产；失败会清理半成品并重试一次。"""
    transport = transport or UrllibTransport()
    target_dir = Path(dest_dir) if dest_dir is not None else default_download_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(plan.asset.name).name
    if not safe_name or safe_name != plan.asset.name:
        raise DownloadError(f"资产文件名不合法：{plan.asset.name}")
    dest = target_dir / safe_name

    if dest.is_file() and plan.asset.size > 0 and dest.stat().st_size == plan.asset.size:
        if log:
            log(f"[下载] 复用已下载文件：{dest}")
        return dest

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("下载已取消")
        try:
            if log:
                log(f"[下载] 第 {attempt} 次尝试：{plan.download_url}")
            transport.download(
                plan.download_url,
                dest,
                expected_size=plan.asset.size,
                timeout=DOWNLOAD_READ_TIMEOUT,
                on_chunk=on_chunk,
                cancel=cancel,
            )
            if plan.asset.size > 0:
                actual = dest.stat().st_size
                if actual != plan.asset.size:
                    raise DownloadError(
                        f"文件大小不一致：期望 {plan.asset.size} 字节，实际 {actual} 字节"
                    )
            if log:
                log(f"[下载] 完成：{dest}（{dest.stat().st_size} 字节）")
            return dest
        except DownloadCancelled:
            _remove_quietly(dest)
            raise
        except Exception as exc:
            last_error = exc
            _remove_quietly(dest)
            _remove_quietly(_part_path(dest))
            if log:
                log(f"[下载] 失败：{exc}")
            if attempt < MAX_ATTEMPTS and retry_delay > 0:
                time.sleep(retry_delay)
    raise DownloadError(f"下载失败，已重试 {MAX_ATTEMPTS} 次：{last_error}")


def _remove_quietly(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def install_command(package: Package, path: Path) -> tuple[str, ...] | None:
    if package == "sunshine":
        return ("msiexec", "/i", str(path), "/passive", "/norestart")
    return None


def _command_text(argv: tuple[str, ...]) -> str:
    return subprocess.list2cmdline(list(argv))


def install_sunshine(
    runner: CommandRunner,
    msi_path: Path,
    *,
    dry_run: bool = False,
) -> OperationResult:
    path = Path(msi_path)
    if not path.is_file():
        return OperationResult.failure(f"安装包不存在：{path}")
    if path.suffix.lower() != ".msi":
        return OperationResult.failure("Sunshine 安装包必须是 .msi")
    argv = install_command("sunshine", path)
    assert argv is not None
    if dry_run:
        text = _command_text(argv)
        runner.log(f"[安装] 模拟执行：{text}")
        return OperationResult.success(
            "Dry-Run：将执行 Sunshine 安装命令",
            details=(text,),
            simulated=True,
        )
    result = runner.mutate(argv, timeout_s=600)
    if result.returncode == 0:
        return OperationResult.success("Sunshine 安装完成")
    if result.returncode == 3010:
        return OperationResult.success(
            "Sunshine 安装完成，需要重启系统后生效",
            details=(result.stderr or result.stdout,),
        )
    detail = (result.stderr or result.stdout or "").strip()
    return OperationResult.failure(
        f"Sunshine 安装失败（msiexec 退出码 {result.returncode}）",
        details=(detail,) if detail else (),
    )


def _normalized_zip_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    parts = pure.parts
    if not parts:
        raise DownloadError("ZIP 中包含空路径")
    if pure.is_absolute() or any(part == ".." for part in parts):
        raise DownloadError(f"ZIP 条目包含非法路径：{name}")
    if any(":" in part for part in parts):
        raise DownloadError(f"ZIP 条目包含盘符：{name}")
    return parts


def extract_zip_safe(zip_path: Path, target_dir: Path) -> list[Path]:
    """安全解压，返回写出的文件路径。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = _normalized_zip_parts(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise DownloadError(f"ZIP 中包含符号链接：{info.filename}")
            dest = target_dir.joinpath(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, dest.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            written.append(dest)
    return written


def _zip_names(zip_path: Path) -> tuple[str, ...]:
    with zipfile.ZipFile(zip_path) as archive:
        return tuple(
            info.filename for info in archive.infolist() if not info.is_dir()
        )


def _find_entry(root: Path) -> tuple[str, Path] | None:
    files = [path for path in root.rglob("*") if path.is_file()]
    files.sort(key=lambda path: len(path.parts))
    for path in files:
        if path.name.lower() == "vdd.control.exe":
            return "control", path
    for path in files:
        if path.name.lower() == "install.bat":
            return "bat", path
    for path in files:
        if path.name.lower() == "install.ps1":
            return "ps1", path
    for path in files:
        if path.suffix.lower() == ".inf":
            return "inf", path
    return None


def _planned_entry(zip_path: Path) -> str:
    names = [name.replace("\\", "/").rsplit("/", 1)[-1].lower() for name in _zip_names(zip_path)]
    if "vdd.control.exe" in names:
        return "VDD.Control.exe"
    if "install.bat" in names:
        return "install.bat"
    if "install.ps1" in names:
        return "install.ps1"
    for name in names:
        if name.endswith(".inf"):
            return "pnputil /add-driver <inf> /install"
    return "未找到自动安装入口，将仅解压文件"


def launch_elevated(path: Path) -> bool:
    """通过 UAC 启动安装向导；测试可注入替代实现。"""
    if sys.platform != "win32":
        return False
    try:
        result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
            None,
            "runas",
            str(path),
            None,
            str(path.parent),
            1,
        )
        return int(result or 0) > 32
    except OSError:
        return False


def _effective_source_root(root: Path) -> Path:
    children = [child for child in root.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return root


def _copy_tree_preserving_settings(source_root: Path, install_dir: Path) -> list[Path]:
    copied: list[Path] = []
    for source in source_root.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(source_root)
        destination = install_dir / relative
        if (
            relative.name.lower() == "vdd_settings.xml"
            and destination.exists()
        ):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def install_vdd(
    runner: CommandRunner,
    zip_path: Path,
    *,
    install_dir: Path = DEFAULT_VDD_DIR,
    dry_run: bool = False,
    launcher: Callable[[Path], bool] = launch_elevated,
    staging_root: Path | None = None,
) -> OperationResult:
    path = Path(zip_path)
    if not path.is_file():
        return OperationResult.failure(f"压缩包不存在：{path}")
    if path.suffix.lower() != ".zip":
        return OperationResult.failure("VDD 安装包必须是 .zip")
    try:
        entry_hint = _planned_entry(path)
        names = _zip_names(path)
        for name in names:
            _normalized_zip_parts(name)
    except (zipfile.BadZipFile, DownloadError) as exc:
        return OperationResult.failure(f"压缩包无效：{exc}")

    if dry_run:
        details = (
            f"将解压到：{install_dir}",
            f"安装入口：{entry_hint}",
        )
        runner.log("[安装] Dry-Run：将解压 VDD 压缩包并执行安装入口")
        for line in details:
            runner.log(f"[安装] {line}")
        return OperationResult.success(
            "Dry-Run：将部署 Virtual Display Driver",
            details=details,
            simulated=True,
        )

    root = Path(staging_root) if staging_root is not None else default_download_dir()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="vdd-staging-", dir=root))
    try:
        extract_zip_safe(path, staging)
        source_root = _effective_source_root(staging)
        copied = _copy_tree_preserving_settings(source_root, install_dir)
        entry = _find_entry(source_root)
        details = (
            f"已复制 {len(copied)} 个文件到 {install_dir}",
            f"安装入口：{entry[1].name if entry else '未找到'}",
        )
        if entry is None:
            return OperationResult.success(
                "VDD 文件已部署，但未找到自动安装入口，请手动运行 VDD.Control.exe",
                details=details,
            )
        kind, entry_path = entry
        if kind == "control":
            if launcher(entry_path):
                return OperationResult.success(
                    "已启动 VDD 安装向导，请按提示完成驱动安装",
                    details=details,
                )
            return OperationResult.failure(
                "无法启动 VDD.Control.exe（可能取消了 UAC）",
                details=details,
            )
        if kind == "bat":
            argv = ("cmd.exe", "/c", str(entry_path))
        elif kind == "ps1":
            argv = (
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "RemoteSigned",
                "-File",
                str(entry_path),
            )
        else:
            argv = ("pnputil.exe", "/add-driver", str(entry_path), "/install")
        result = runner.mutate(argv, timeout_s=600)
        if result.returncode == 0:
            return OperationResult.success(
                "VDD 安装命令已执行完成", details=details
            )
        detail = (result.stderr or result.stdout or "").strip()
        return OperationResult.failure(
            f"VDD 安装失败（退出码 {result.returncode}）",
            details=details + ((detail,) if detail else ()),
        )
    except PermissionError as exc:
        return OperationResult.failure(
            f"写入 {install_dir} 需要管理员权限：{exc}"
        )
    except (OSError, zipfile.BadZipFile, DownloadError) as exc:
        return OperationResult.failure(f"VDD 部署失败：{exc}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install_local_package(
    runner: CommandRunner,
    package: Package,
    path: Path,
    *,
    install_dir: Path = DEFAULT_VDD_DIR,
    dry_run: bool = False,
    launcher: Callable[[Path], bool] = launch_elevated,
    staging_root: Path | None = None,
) -> OperationResult:
    if package == "sunshine":
        return install_sunshine(runner, path, dry_run=dry_run)
    return install_vdd(
        runner,
        path,
        install_dir=install_dir,
        dry_run=dry_run,
        launcher=launcher,
        staging_root=staging_root,
    )
