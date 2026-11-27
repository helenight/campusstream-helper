"""一键排障：只读收集本机环境，生成可自动脱敏的 Markdown 诊断报告。

设计原则：
1. 采集过程只调用 ``CommandRunner.query*`` 与普通文件读取，不修改系统；
2. 任意一个板块失败都不影响其他板块，失败原因会写进报告；
3. 报告默认脱敏：把 ``C:\\Users\\<用户名>`` 替换为 ``C:\\Users\\<USER>``，
   并隐藏 password / token / PIN 等敏感值；
4. 报告只在本地生成，复制与导出均由用户主动触发，不会自动上传。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Sequence

from core import __version__
from core.common import CommandRunner, default_state_dir
from core.network import FIREWALL_RULE_NAME, NetworkReport, collect_network_report
from core.privileges import is_admin
from core.sunshine import (
    CONFIG_FILENAME,
    SunshineStatus,
    detect_sunshine,
    is_local_port_open,
    pairing_port,
    pairing_url,
    sunshine_log_candidates,
)
from core.vdd import VddStatus, detect_vdd

APP_LOG_FILENAME = "app.log"
CRASH_LOG_FILENAME = "crash.log"
DIAGNOSTIC_FILENAME = "diagnostic_report.md"
DEFAULT_PROBE_TIMEOUT_S = 1.0

# Sunshine 的核心 TCP 端口：47984/47989/47990 为 Web/HTTPS/配对页，
# 48010 为串流控制端口。诊断时统一按回环地址探测。
SUNSHINE_PORTS: tuple[int, ...] = (47984, 47989, 47990, 48010)

_LOGGER_NAME = "campusstream"
_REDACTED = "<已脱敏>"
_USER_DIR_RE = re.compile(r"(?i)([a-z]:[\\/]+users[\\/]+)[^\\/\s\"'<>|]+")
_SECRET_KEY_RE = re.compile(
    r"""(?ix)
    (?:^|(?<=[\s\[,;]))
    ["']?
    (?:password|passwd|pwd|token|secret|api[_-]?key|authorization|cookie|private[_-]?key|pin)
    ["']?\s*[:=]\s*
    """
)
_SECRET_XML_RE = re.compile(
    r"(?is)(<(?P<tag>password|passwd|pwd|token|secret|api[_-]?key|"
    r"authorization|cookie|private[_-]?key|pin)>)(.*?)(</(?P=tag)>)"
)
_SECRET_PARAM_RE = re.compile(
    r"(?i)([?&/](?:token|key|secret|password|passwd|pwd|pin)=)[^&\s]+"
)

ProgressCallback = Callable[[int, str], None]

_SYSTEM_QUERY = r"""
$ErrorActionPreference = 'SilentlyContinue'
$os = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$proxy = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
[pscustomobject]@{
  ProductName = $os.ProductName
  DisplayVersion = $os.DisplayVersion
  CurrentBuildNumber = $os.CurrentBuildNumber
  UBR = $os.UBR
  EditionID = $os.EditionID
  ProxyEnable = $proxy.ProxyEnable
  ProxyServer = $proxy.ProxyServer
  ProxyOverride = $proxy.ProxyOverride
  AutoConfigURL = $proxy.AutoConfigURL
} | ConvertTo-Json -Compress
""".strip()

_DISPLAY_QUERY = r"""
$ErrorActionPreference = 'SilentlyContinue'
'=== Get-PnpDevice -Class Display ==='
Get-PnpDevice -Class Display |
    Select-Object Status, Class, FriendlyName, InstanceId, Problem |
    Format-Table -AutoSize | Out-String -Width 300
'=== Win32_VideoController ==='
Get-CimInstance Win32_VideoController |
    Select-Object Name, DriverVersion, Status, AdapterRAM |
    Format-List | Out-String
""".strip()


@dataclass(frozen=True, slots=True)
class DiagnosticSection:
    """诊断报告中的一个板块。"""

    key: str
    title: str
    content: str
    ok: bool = True


@dataclass(frozen=True, slots=True)
class DiagnosticsReport:
    """一次完整诊断的不可变快照。"""

    sections: tuple[DiagnosticSection, ...]
    generated_at: datetime
    dry_run: bool
    admin: bool
    app_version: str = __version__
    state_dir: Path | None = None

    @property
    def failed_sections(self) -> tuple[DiagnosticSection, ...]:
        return tuple(section for section in self.sections if not section.ok)

    def to_markdown(self, *, redact: bool = True) -> str:
        """把报告渲染为 Markdown；默认自动脱敏。"""
        stamp = self.generated_at.strftime("%Y-%m-%d %H:%M:%S %z").strip()
        lines: list[str] = [
            "# CampusStream Helper 排障诊断报告",
            "",
            f"- 生成时间：{stamp}",
            f"- 程序版本：v{self.app_version}",
            f"- 运行模式：{'Dry-Run 模拟' if self.dry_run else '真实模式'}",
            f"- 管理员权限：{'是' if self.admin else '否'}",
            f"- 自动脱敏：{'已开启' if redact else '未开启'}",
        ]
        if self.state_dir is not None:
            lines.append(f"- 状态目录：{self.state_dir}")
        lines.extend(
            [
                "",
                "> 本报告由本机只读检测生成，不会自动上传。"
                "分享给他人前请确认是否仍包含个人路径、局域网 IP 等信息。",
                "",
            ]
        )
        for index, section in enumerate(self.sections, start=1):
            suffix = "" if section.ok else "（采集失败）"
            lines.append(f"## {index}. {section.title}{suffix}")
            lines.append("")
            lines.append(section.content.strip() or "（无内容）")
            lines.append("")
        text = "\n".join(lines).rstrip() + "\n"
        return redact_text(text) if redact else text


# ----------------------------------------------------------------------
# 脱敏
# ----------------------------------------------------------------------


def redact_text(text: str) -> str:
    """隐藏用户名目录与 password / token / PIN 等敏感值。"""
    redacted = _USER_DIR_RE.sub(r"\1<USER>", text)
    redacted = _SECRET_XML_RE.sub(
        lambda match: f"{match.group(1)}{_REDACTED}{match.group(4)}",
        redacted,
    )
    redacted = _SECRET_PARAM_RE.sub(
        lambda match: f"{match.group(1)}{_REDACTED}",
        redacted,
    )
    lines: list[str] = []
    for line in redacted.splitlines():
        match = _SECRET_KEY_RE.search(line)
        if match is None:
            lines.append(line)
        else:
            # 保留时间戳 / 日志前缀，只丢掉等号或冒号之后的敏感值。
            lines.append(line[: match.end()] + _REDACTED)
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 日志文件
# ----------------------------------------------------------------------


def configure_file_logging(state_dir: Path | None = None) -> Path | None:
    """把 ``campusstream`` 日志同时写入 ``app.log``，供诊断报告读取。

    使用轮转文件，单个文件 1 MB、保留 2 份，避免长期运行无限增长。
    返回日志路径；无法创建目录或文件时返回 ``None``。
    """
    directory = Path(state_dir) if state_dir is not None else default_state_dir()
    log_path = directory / APP_LOG_FILENAME
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    resolved = log_path.resolve()
    for handler in logger.handlers:
        if not isinstance(handler, logging.FileHandler):
            continue
        try:
            if Path(handler.baseFilename).resolve() == resolved:
                return log_path
        except OSError:
            continue
    try:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
    except OSError:
        return None
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    logger.addHandler(handler)
    logger.info("[启动] CampusStream Helper v%s", __version__)
    return log_path


# ----------------------------------------------------------------------
# 端口探测
# ----------------------------------------------------------------------


def probe_ports(
    ports: Sequence[int] = SUNSHINE_PORTS,
    *,
    connector: Callable[[int], bool] | None = None,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
) -> dict[int, bool]:
    """探测一组本机端口；默认每个端口最多等待 1 秒。"""
    probe = connector
    if probe is None:
        probe = lambda port: is_local_port_open(port, timeout_s=timeout_s)
    result: dict[int, bool] = {}
    for port in ports:
        try:
            result[int(port)] = bool(probe(int(port)))
        except Exception:
            result[int(port)] = False
    return result


# ----------------------------------------------------------------------
# 采集入口
# ----------------------------------------------------------------------


def collect_diagnostics(
    runner: CommandRunner,
    *,
    dry_run: bool = False,
    state_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    admin_check: Callable[[], bool] | None = None,
    port_connector: Callable[[int], bool] | None = None,
    probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    sunshine_detector: Callable[[CommandRunner], SunshineStatus] = detect_sunshine,
    vdd_detector: Callable[[CommandRunner], VddStatus] = detect_vdd,
    network_collector: Callable[..., NetworkReport] = collect_network_report,
    generated_at: datetime | None = None,
) -> DiagnosticsReport:
    """按六个板块收集诊断信息并返回报告。

    Args:
        runner: 只读命令执行器；``dry_run`` 不影响只读查询。
        dry_run: 主界面是否处于 Dry-Run，仅用于报告头部说明。
        state_dir: 程序状态目录，默认 ``%LOCALAPPDATA%\\CampusStreamHelper``。
        progress: 可选进度回调 ``(百分比, 当前动作)``。
        admin_check: 可注入的管理员检测函数，便于测试。
        port_connector: 可注入的端口探测函数，返回 True 表示正在监听。
        probe_timeout_s: 单端口探测超时，默认 1 秒。
        sunshine_detector / vdd_detector / network_collector: 便于测试替换。
        generated_at: 可注入的生成时间，便于生成稳定快照。
    """
    emit = progress or (lambda _percent, _message: None)
    resolved_state_dir = (
        Path(state_dir) if state_dir is not None else default_state_dir()
    )
    emit(1, "准备收集排障信息…")

    check_admin = admin_check or is_admin
    try:
        admin = bool(check_admin())
    except Exception:
        admin = False

    emit(6, "检测 Sunshine 安装与服务")
    sunshine: SunshineStatus | None = None
    sunshine_error: str | None = None
    try:
        sunshine = sunshine_detector(runner)
    except Exception as exc:  # detector 自身的异常不应中断报告
        sunshine_error = f"{type(exc).__name__}: {exc}"

    sections: list[DiagnosticSection] = []

    def add_section(
        index: int,
        key: str,
        title: str,
        builder: Callable[[], str],
    ) -> None:
        emit(min(99, 10 + index * 15), f"采集：{title}")
        try:
            content = builder()
            ok = True
        except Exception as exc:
            content = f"采集失败：{type(exc).__name__}: {exc}"
            ok = False
        sections.append(
            DiagnosticSection(key=key, title=title, content=content, ok=ok)
        )

    add_section(
        0,
        "system",
        "系统与代理",
        lambda: _build_system_section(runner, dry_run=dry_run, admin=admin),
    )
    add_section(
        1,
        "display",
        "显示与显卡",
        lambda: _build_display_section(runner, sunshine, sunshine_error),
    )
    add_section(
        2,
        "sunshine",
        "Sunshine 深度检查",
        lambda: _build_sunshine_section(
            runner,
            sunshine,
            sunshine_error,
            port_connector=port_connector,
            probe_timeout_s=probe_timeout_s,
        ),
    )
    add_section(
        3,
        "vdd",
        "Virtual Display Driver",
        lambda: _build_vdd_section(runner, vdd_detector),
    )
    add_section(
        4,
        "network",
        "网络与防火墙",
        lambda: _build_network_section(
            runner,
            sunshine,
            network_collector,
        ),
    )
    add_section(
        5,
        "app_logs",
        "程序运行日志",
        lambda: _build_app_log_section(resolved_state_dir),
    )

    report = DiagnosticsReport(
        sections=tuple(sections),
        generated_at=generated_at or datetime.now().astimezone(),
        dry_run=dry_run,
        admin=admin,
        app_version=__version__,
        state_dir=resolved_state_dir,
    )
    emit(100, "诊断报告生成完成")
    return report


# ----------------------------------------------------------------------
# 各板块构建
# ----------------------------------------------------------------------


def _build_system_section(
    runner: CommandRunner,
    *,
    dry_run: bool,
    admin: bool,
) -> str:
    payload: dict[str, Any] = {}
    query_error: str | None = None
    result = runner.query_powershell(_SYSTEM_QUERY, timeout_s=15)
    if result.ok:
        try:
            value = json.loads(result.stdout or "{}")
            if isinstance(value, dict):
                payload = value
        except json.JSONDecodeError as exc:
            query_error = f"注册表查询返回无法解析的 JSON：{exc}"
    else:
        query_error = _tail(result.stderr or result.stdout or "未知错误")

    product = str(payload.get("ProductName") or "").strip()
    display_version = str(payload.get("DisplayVersion") or "").strip()
    build = str(payload.get("CurrentBuildNumber") or "").strip()
    ubr = str(payload.get("UBR") or "").strip()
    if build and ubr:
        build = f"{build}.{ubr}"

    windows_text = " ".join(
        part for part in (product, display_version) if part
    ) or platform.platform()
    proxy_enabled = _as_switch(payload.get("ProxyEnable"))
    proxy_server = str(payload.get("ProxyServer") or "").strip()
    proxy_override = str(payload.get("ProxyOverride") or "").strip()
    pac_url = str(payload.get("AutoConfigURL") or "").strip()

    lines = [
        f"- 程序版本：v{__version__}",
        f"- 运行模式：{'Dry-Run 模拟' if dry_run else '真实模式'}",
        f"- 管理员权限：{'是' if admin else '否'}",
        f"- 系统：{windows_text}",
        f"- Windows Build：{build or '未读取到'}",
        f"- Python：{platform.python_version()}（{platform.architecture()[0]}）",
        f"- 处理器架构：{os.environ.get('PROCESSOR_ARCHITECTURE') or '未知'}",
        "",
        f"- 系统代理开关（ProxyEnable）：{proxy_enabled}",
        f"- 代理服务器（ProxyServer）：{proxy_server or '未设置'}",
        f"- 代理例外（ProxyOverride）：{proxy_override or '未设置'}",
        f"- 自动配置脚本（AutoConfigURL）：{pac_url or '未设置'}",
    ]
    if query_error:
        lines.extend(["", f"> 注册表读取失败：{query_error}"])
    if payload.get("ProxyEnable") not in (None, 0, "0", False):
        lines.extend(
            [
                "",
                "> 检测到系统代理已开启。若浏览器打不开 127.0.0.1 配对页，"
                "请在代理例外中加入 `127.0.0.1;localhost`。",
            ]
        )
    return "\n".join(lines)


def _build_display_section(
    runner: CommandRunner,
    sunshine: SunshineStatus | None,
    sunshine_error: str | None,
) -> str:
    lines = ["### PnP 显示器与显卡", ""]
    result = runner.query_powershell(_DISPLAY_QUERY, timeout_s=20)
    if result.ok and result.stdout.strip():
        lines.append(_fence(result.stdout.strip(), "text"))
    else:
        lines.append(
            "查询失败："
            + _tail(result.stderr or result.stdout or "无输出")
        )

    lines.extend(["", "### dxgi-info.exe 原始输出", ""])
    dxgi = _find_dxgi_info(sunshine)
    if dxgi is None:
        reason = "未安装 Sunshine，或安装目录中没有 dxgi-info.exe"
        if sunshine_error:
            reason += f"（Sunshine 检测异常：{sunshine_error}）"
        lines.append(reason)
        return "\n".join(lines)

    lines.append(f"路径：`{dxgi}`")
    dxgi_result = runner.query([str(dxgi)], timeout_s=20)
    if dxgi_result.ok and dxgi_result.stdout.strip():
        lines.extend(
            [
                "",
                _fence(dxgi_result.stdout.strip(), "text"),
            ]
        )
    else:
        lines.extend(
            [
                "",
                "执行失败："
                + _tail(dxgi_result.stderr or dxgi_result.stdout or "无输出"),
            ]
        )
    return "\n".join(lines)


def _build_sunshine_section(
    runner: CommandRunner,
    status: SunshineStatus | None,
    error: str | None,
    *,
    port_connector: Callable[[int], bool] | None,
    probe_timeout_s: float,
) -> str:
    lines: list[str] = []
    if status is None:
        lines.append(f"Sunshine 检测失败：{error or '未知错误'}")
        lines.append("")
        lines.append("可尝试：重新安装 Sunshine，并确认服务 SunshineService 存在。")
        return "\n".join(lines)

    install_dir = status.install_dir
    executable = status.executable
    service_exists = bool(install_dir and executable and executable.is_file())
    lines.extend(
        [
            f"- 是否安装：{'是' if status.installed else '否'}",
            f"- 安装目录：{install_dir or '未找到'}",
            f"- sunshine.exe：{_path_with_state(executable)}",
            f"- tools\\sunshinesvc.exe：{_path_with_state(status.sunshinesvc_path)}",
            f"- 服务名称：{status.service_name}",
            f"- 服务状态：{_service_state_text(status.service_state)}",
            f"- 服务 ImagePath：{_path_with_state(status.service_image_path)}",
            f"- 版本：{status.version or '未知'}",
            f"- 检测详情：{status.detail}",
        ]
    )
    if status.service_binary_missing:
        lines.extend(
            [
                "",
                "> ⚠ 检测到服务程序文件缺失（sunshine.exe / tools\\sunshinesvc.exe / "
                "ImagePath 指向的文件不存在）。国内杀软可能误删，"
                "请先把安装目录加入 360 / 火绒等信任区，再使用「重新修复安装」。",
            ]
        )

    web_port = pairing_port(status)
    port_status = probe_ports(
        SUNSHINE_PORTS,
        connector=port_connector,
        timeout_s=probe_timeout_s,
    )
    lines.extend(["", "### 本机端口监听情况（127.0.0.1）", ""])
    for port in SUNSHINE_PORTS:
        state = "✅ 正在监听" if port_status.get(port) else "❌ 未监听"
        suffix = "（配对页 / Web UI）" if port == web_port else ""
        lines.append(f"- `127.0.0.1:{port}`：{state}{suffix}")
    if web_port not in SUNSHINE_PORTS:
        web_state = (
            "✅ 正在监听"
            if _probe_one(web_port, port_connector, probe_timeout_s)
            else "❌ 未监听"
        )
        lines.append(f"- `127.0.0.1:{web_port}`：{web_state}（配对页 / Web UI）")

    any_port_open = any(port_status.values())
    lines.extend(["", f"- 配对页地址：{pairing_url(status)}"])
    if status.service_state != "running" and not any_port_open:
        lines.append(
            "- 处理建议：SunshineService 未运行，请在 Step 4 点击「一键启动服务」。"
        )
    elif status.service_state == "running" and not any_port_open:
        lines.append(
            "- 处理建议：服务在运行但所有端口都未监听，可能被防火墙 / 杀软拦截，"
            "或 sunshine.conf 中的端口被修改。"
        )
    elif not _probe_one(web_port, port_connector, probe_timeout_s):
        lines.append(
            "- 处理建议：核心串流端口已监听，但配对页端口未就绪，"
            "请稍后重新探测或重启 SunshineService。"
        )

    lines.extend(["", "### sunshine.conf", ""])
    config_path = _resolve_config_path(status)
    if config_path is None:
        lines.append("未找到 sunshine.conf。")
    else:
        content, read_error = _read_text_file(config_path)
        lines.append(f"路径：`{config_path}`")
        lines.append("")
        if read_error is not None:
            lines.append(f"读取失败：{read_error}")
        else:
            lines.append(_fence(content, "ini"))

    lines.extend(["", "### sunshine.log（最后 50 行）", ""])
    candidates = sunshine_log_candidates(status)
    existing = next((path for path in candidates if path.is_file()), None)
    if existing is None:
        lines.append("未找到 sunshine.log。已检查以下路径：")
        lines.extend(f"- `{path}`" for path in candidates)
    else:
        content, read_error = _read_text_file(existing)
        lines.append(f"路径：`{existing}`")
        lines.append("")
        if read_error is not None:
            lines.append(f"读取失败：{read_error}")
        else:
            tail = "\n".join(content.splitlines()[-50:])
            lines.append(_fence(tail or "（日志为空）", "text"))

    if service_exists and status.service_state == "running" and not any_port_open:
        lines.append("")
        lines.append(
            "> 提示：若日志中出现 bind / port 相关错误，"
            "请检查 47984-48010 是否被其他程序占用。"
        )
    return "\n".join(lines)


def _build_vdd_section(
    runner: CommandRunner,
    vdd_detector: Callable[[CommandRunner], VddStatus],
) -> str:
    status = vdd_detector(runner)
    lines = [
        f"- 是否安装：{'是' if status.installed else '否'}",
        f"- 驱动目录：{status.install_dir or '未找到'}",
        f"- 配置文件：{status.settings_path or '未找到'}",
        f"- 驱动版本：{status.driver_version or '未知'}",
        f"- XML 结构：{status.schema}",
        f"- 已配置显示器数量：{status.configured_monitor_count}",
        f"- 已配置分辨率数量：{status.configured_resolution_count}",
        f"- PnP 设备是否就绪：{'是' if status.device_ready else '否'}",
        f"- 检测详情：{status.detail}",
    ]
    if not status.device_ready:
        lines.extend(
            [
                "",
                "> ⚠ VDD 驱动尚未就绪：请打开 VDD.Control.exe → System → "
                "Install Driver，完成后重新检测。",
            ]
        )

    lines.extend(["", "### 驱动目录文件", ""])
    if status.install_dir is None or not Path(status.install_dir).is_dir():
        lines.append("驱动目录不存在或不可读。")
    else:
        lines.append(_format_directory(Path(status.install_dir)))

    lines.extend(["", "### vdd_settings.xml", ""])
    settings_path = _resolve_vdd_settings_path(status)
    if settings_path is None:
        lines.append("未找到 vdd_settings.xml。")
    else:
        content, read_error = _read_text_file(settings_path)
        lines.append(f"路径：`{settings_path}`")
        lines.append("")
        if read_error is not None:
            lines.append(f"读取失败：{read_error}")
        else:
            lines.append(_fence(content, "xml"))
    return "\n".join(lines)


def _build_network_section(
    runner: CommandRunner,
    sunshine: SunshineStatus | None,
    network_collector: Callable[..., NetworkReport],
) -> str:
    sunshine_exe = sunshine.executable if sunshine is not None else None
    report = network_collector(runner, sunshine_exe=sunshine_exe)
    scenario_text = {
        "ethernet_ready": "有线网络已就绪",
        "wifi_only": "仅无线网络在线",
        "offline": "未检测到可用网络",
    }.get(report.scenario, report.scenario)
    lines = [
        f"- 网络场景：{scenario_text}",
        f"- Moonlight 建议填写：{report.recommended_client_ip or '无可用地址'}",
        f"- 防火墙规则「{FIREWALL_RULE_NAME}」："
        f"{'已存在' if report.firewall_rule_present else '不存在'}",
        f"- 检测时间：{report.generated_at}",
        "",
        "### 网卡与地址",
        "",
        "| 网卡 | 类型 | 状态 | IPv4 | 全局 IPv6 | ms_tcpip6 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if not report.adapters:
        lines.append("| （未检测到网卡） | - | - | - | - | - |")
    for adapter in report.adapters:
        ipv6 = "<br>".join(adapter.global_ipv6) or "-"
        lines.append(
            "| {name} | {kind} | {state} | {ipv4} | {ipv6} | {binding} |".format(
                name=_md_cell(adapter.name),
                kind=_md_cell(adapter.kind),
                state="在线" if adapter.is_up else "离线",
                ipv4=_md_cell(adapter.ipv4 or "-"),
                ipv6=_md_cell(ipv6),
                binding="已启用" if adapter.ipv6_binding_enabled else "**未启用**",
            )
        )
    if report.warnings:
        lines.extend(["", "### 网络提醒", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
    return "\n".join(lines)


def _build_app_log_section(state_dir: Path) -> str:
    lines = [f"状态目录：`{state_dir}`", ""]
    app_log = state_dir / APP_LOG_FILENAME
    crash_log = state_dir / CRASH_LOG_FILENAME

    lines.extend([f"### {APP_LOG_FILENAME}（最后 200 行）", ""])
    app_content, app_error = _read_text_file(app_log)
    if app_error is not None:
        lines.append(
            f"未读取到运行日志（{app_error}）。"
            "该文件从 v0.3.0 起自动写入，旧版本可能不存在。"
        )
    else:
        tail = "\n".join(app_content.splitlines()[-200:])
        lines.extend([f"路径：`{app_log}`", "", _fence(tail or "（日志为空）", "text")])

    lines.extend(["", f"### {CRASH_LOG_FILENAME}（最后 80 行）", ""])
    crash_content, crash_error = _read_text_file(crash_log)
    if crash_error is not None:
        lines.append(f"未读取到崩溃日志：{crash_error}")
        lines.extend(["", "### 最近一次 traceback", "", "没有找到 crash.log。"])
    else:
        tail = "\n".join(crash_content.splitlines()[-80:])
        lines.extend(
            [
                f"路径：`{crash_log}`",
                "",
                _fence(tail or "（日志为空）", "text"),
                "",
                "### 最近一次 traceback",
                "",
            ]
        )
        traceback_text = _last_traceback(crash_content)
        if traceback_text is None:
            lines.append("crash.log 中没有 Traceback。")
        else:
            lines.append(_fence(traceback_text, "text"))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------


def _resolve_config_path(status: SunshineStatus) -> Path | None:
    if status.config_path is not None and Path(status.config_path).is_file():
        return Path(status.config_path)
    if status.install_dir is None:
        return None
    for candidate in (
        Path(status.install_dir) / "config" / CONFIG_FILENAME,
        Path(status.install_dir) / CONFIG_FILENAME,
    ):
        if candidate.is_file():
            return candidate
    return None


def _resolve_vdd_settings_path(status: VddStatus) -> Path | None:
    if status.settings_path is not None and Path(status.settings_path).is_file():
        return Path(status.settings_path)
    if status.install_dir is None:
        return None
    for candidate in (
        Path(status.install_dir) / "vdd_settings.xml",
        Path(status.install_dir) / "config" / "vdd_settings.xml",
    ):
        if candidate.is_file():
            return candidate
    return None


def _find_dxgi_info(status: SunshineStatus | None) -> Path | None:
    if status is None or status.install_dir is None:
        return None
    install_dir = Path(status.install_dir)
    for candidate in (
        install_dir / "dxgi-info.exe",
        install_dir / "tools" / "dxgi-info.exe",
    ):
        if candidate.is_file():
            return candidate
    return None


def _probe_one(
    port: int,
    connector: Callable[[int], bool] | None,
    timeout_s: float,
) -> bool:
    try:
        if connector is not None:
            return bool(connector(port))
        return is_local_port_open(port, timeout_s=timeout_s)
    except Exception:
        return False


def _read_text_file(path: Path, *, max_bytes: int = 2_000_000) -> tuple[str, str | None]:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return "", f"{type(exc).__name__}: {exc}"
    if len(data) > max_bytes:
        data = data[-max_bytes:]
        prefix = "（文件过大，仅显示末尾部分）\n"
    else:
        prefix = ""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return prefix + text, None


def _last_traceback(text: str, *, max_lines: int = 120) -> str | None:
    marker = "Traceback (most recent call last)"
    index = text.rfind(marker)
    if index < 0:
        return None
    lines = text[index:].splitlines()
    return "\n".join(lines[:max_lines])


def _format_directory(path: Path, *, limit: int = 100) -> str:
    try:
        entries = sorted(
            path.iterdir(),
            key=lambda item: (not item.is_dir(), item.name.casefold()),
        )
    except OSError as exc:
        return f"读取目录失败：{type(exc).__name__}: {exc}"
    if not entries:
        return "（目录为空）"
    lines: list[str] = []
    for entry in entries[:limit]:
        try:
            if entry.is_dir():
                lines.append(f"[目录] {entry.name}/")
            else:
                lines.append(f"[文件] {entry.name}  {entry.stat().st_size} 字节")
        except OSError:
            lines.append(f"[未知] {entry.name}")
    if len(entries) > limit:
        lines.append(f"… 其余 {len(entries) - limit} 项已省略")
    return "\n".join(lines)


def _fence(text: str, language: str = "") -> str:
    runs = re.findall(r"`+", text)
    longest = max((len(run) for run in runs), default=0)
    fence = "`" * max(3, longest + 1)
    body = text.rstrip()
    return f"{fence}{language}\n{body}\n{fence}"


def _path_with_state(path: Path | None) -> str:
    if path is None:
        return "未提供"
    try:
        exists = Path(path).is_file()
    except OSError:
        exists = False
    return f"`{path}`（{'存在' if exists else '缺失'}）"


def _service_state_text(state: str) -> str:
    return {
        "running": "运行中",
        "stopped": "已停止",
        "start_pending": "正在启动",
        "stop_pending": "正在停止",
        "not_installed": "未安装",
        "unknown": "未知",
    }.get(state, state)


def _as_switch(value: Any) -> str:
    if value in (1, "1", True, "true", "True"):
        return "已开启"
    if value in (0, "0", False, "false", "False"):
        return "未开启"
    return "未读取到"


def _md_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _tail(text: str, limit: int = 300) -> str:
    lines = [line.strip() for line in str(text).strip().splitlines() if line.strip()]
    if not lines:
        return "无输出"
    return lines[-1][:limit]
