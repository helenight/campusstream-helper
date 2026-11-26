"""模块 D：电源保活管理（插电永不休眠、定时关屏、一键还原）。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.common import CommandRunner, OperationResult, default_state_dir

REMOTE_DISPLAY_OFF_MINUTES = 10
_STATE_FILENAME = "power_state.json"

_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_INDEX_RE = re.compile(r"0x([0-9a-fA-F]{8})")


@dataclass(frozen=True, slots=True)
class PowerState:
    """当前电源方案的 AC / DC 超时（秒）；0 表示永不。"""

    scheme_guid: str
    scheme_name: str
    ac_sleep_seconds: int
    ac_display_off_seconds: int
    dc_sleep_seconds: int
    dc_display_off_seconds: int

    @property
    def remote_ready(self) -> bool:
        """插电永不休眠即视为满足远控保活。"""
        return self.ac_sleep_seconds == 0


def read_power_state(runner: CommandRunner) -> PowerState:
    """读取当前电源方案的睡眠与关屏超时。

    优先读取 ``PowerSchemes`` 注册表（不受系统语言影响），
    失败时回退到 ``powercfg /query`` 解析。
    """
    state = _read_state_via_registry(runner)
    if state is not None:
        runner.log(
            f"[电源] 当前方案「{state.scheme_name}」："
            f"睡眠 {_format_seconds(state.ac_sleep_seconds)}，"
            f"关屏 {_format_seconds(state.ac_display_off_seconds)}"
        )
        return state

    fallback = _read_state_via_powercfg(runner)
    if fallback is not None:
        runner.log(
            f"[电源] 当前方案「{fallback.scheme_name}」："
            f"睡眠 {_format_seconds(fallback.ac_sleep_seconds)}，"
            f"关屏 {_format_seconds(fallback.ac_display_off_seconds)}"
        )
        return fallback
    raise RuntimeError("无法读取当前电源方案，请确认 powercfg 可用")


def apply_remote_profile(
    runner: CommandRunner,
    *,
    state_path: Path | None = None,
) -> OperationResult:
    """应用「插电永不休眠，10 分钟后关闭显示器」。

    修改前把原 AC 超时写入状态文件；DC（电池）设置保持不变。
    """
    path = Path(state_path) if state_path is not None else default_state_dir() / _STATE_FILENAME
    current = read_power_state(runner)

    existing = _load_state_file(path)
    if existing is None:
        payload = _state_payload(current)
        if runner.dry_run:
            runner.log(f"[DRY-RUN] 将保存原始电源配置: {path}")
        else:
            runner.write_text_atomic(
                path,
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            runner.log(f"[电源] 已保存原始配置: {path}")
    else:
        runner.log(f"[电源] 已存在原始配置记录，保留: {path}")

    commands = [
        ["powercfg", "/change", "standby-timeout-ac", "0"],
        ["powercfg", "/change", "monitor-timeout-ac", str(REMOTE_DISPLAY_OFF_MINUTES)],
    ]
    results = []
    for argv in commands:
        result = runner.mutate(argv, timeout_s=20)
        results.append(result)
        if not result.ok and not result.simulated:
            details = [f"命令失败: {' '.join(argv)}", _tail(result.stderr or result.stdout)]
            restore_target = existing or _state_payload(current)
            restored = _apply_original_values(runner, restore_target, log=False)
            if restored:
                details.append("已尝试还原原电源配置")
            return OperationResult.failure("应用电源保活配置失败", details=tuple(details))

    simulated = runner.dry_run or any(result.simulated for result in results)
    prefix = "将设置" if simulated else "已设置"
    return OperationResult.success(
        f"{prefix}：插电永不休眠，10 分钟后关闭显示器",
        details=(
            f"原睡眠: {_format_seconds(current.ac_sleep_seconds)}",
            f"原关屏: {_format_seconds(current.ac_display_off_seconds)}",
            f"状态文件: {path}",
        ),
        simulated=simulated,
    )


def restore_previous_profile(
    runner: CommandRunner,
    *,
    state_path: Path | None = None,
) -> OperationResult:
    """读取状态文件并还原应用前的 AC 睡眠与关屏超时。"""
    path = Path(state_path) if state_path is not None else default_state_dir() / _STATE_FILENAME
    payload = _load_state_file(path)
    if payload is None:
        return OperationResult.failure(
            "没有找到电源配置备份，无法还原",
            details=(f"预期位置: {path}",),
        )

    results = _apply_original_values(runner, payload, log=True)
    if not results:
        return OperationResult.failure(
            "电源配置备份内容不完整，无法还原",
            details=(f"状态文件: {path}",),
        )
    if not all(result.ok or result.simulated for result in results):
        return OperationResult.failure(
            "还原电源配置失败",
            details=tuple(
                f"{' '.join(result.argv)}: {_tail(result.stderr or result.stdout)}"
                for result in results
                if not result.ok and not result.simulated
            ),
        )

    if not runner.dry_run:
        path.unlink(missing_ok=True)
        runner.log(f"[电源] 已删除状态文件: {path}")

    prefix = "将还原" if runner.dry_run else "已还原"
    return OperationResult.success(
        f"{prefix}原始电源配置",
        details=(
            f"睡眠: {_format_seconds(int(payload['ac_sleep_seconds']))}",
            f"关屏: {_format_seconds(int(payload['ac_display_off_seconds']))}",
        ),
        simulated=runner.dry_run,
    )


# ----------------------------------------------------------------------
# 内部实现
# ----------------------------------------------------------------------


def _read_state_via_registry(runner: CommandRunner) -> PowerState | None:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$out = powercfg /getactivescheme
$guid = [regex]::Match($out, '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}').Value
$name = $out.Trim()
if ($out -match '\(([^)]+)\)') { $name = $matches[1] }

$sleep = $null
$video = $null
if ($guid) {
    $base = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\$guid"
    $sleep = Get-ItemProperty "$base\238C9FA8-0AAD-41ED-83F4-97BE242C8F20\29F6C1DB-86DA-48C5-9FDB-F2B67B1F44DA"
    $video = Get-ItemProperty "$base\7516B95F-F776-4464-8C53-06167F40CC99\3C0BC021-C8A3-4E07-A973-6B14CBCB2B7E"
}

$acSleep = -1
$dcSleep = -1
$acVideo = -1
$dcVideo = -1
if ($sleep) {
    $acSleep = [int]$sleep.ACSettingIndex
    $dcSleep = [int]$sleep.DCSettingIndex
}
if ($video) {
    $acVideo = [int]$video.ACSettingIndex
    $dcVideo = [int]$video.DCSettingIndex
}

[pscustomobject]@{
    scheme_guid = $guid
    scheme_name = $name
    ac_sleep_seconds = $acSleep
    dc_sleep_seconds = $dcSleep
    ac_display_off_seconds = $acVideo
    dc_display_off_seconds = $dcVideo
} | ConvertTo-Json -Compress
""".strip()
    result = runner.query_powershell(script, timeout_s=20)
    if not result.ok:
        runner.log(f"[电源] 注册表查询失败: {_tail(result.stderr or result.stdout)}")
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    try:
        state = PowerState(
            scheme_guid=str(payload.get("scheme_guid") or ""),
            scheme_name=str(payload.get("scheme_name") or "未知方案"),
            ac_sleep_seconds=int(payload["ac_sleep_seconds"]),
            ac_display_off_seconds=int(payload["ac_display_off_seconds"]),
            dc_sleep_seconds=int(payload["dc_sleep_seconds"]),
            dc_display_off_seconds=int(payload["dc_display_off_seconds"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not state.scheme_guid or state.ac_sleep_seconds < 0 or state.ac_display_off_seconds < 0:
        return None
    return state


def _read_state_via_powercfg(runner: CommandRunner) -> PowerState | None:
    scheme = runner.query(["powercfg", "/getactivescheme"], timeout_s=15)
    if not scheme.ok:
        return None
    guid_match = _GUID_RE.search(scheme.stdout)
    if guid_match is None:
        return None
    guid = guid_match.group(0)
    name_match = re.search(r"\(([^)]+)\)", scheme.stdout)
    name = name_match.group(1).strip() if name_match else scheme.stdout.strip()

    sleep = runner.query(
        ["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"],
        timeout_s=15,
    )
    video = runner.query(
        ["powercfg", "/query", "SCHEME_CURRENT", "SUB_VIDEO", "VIDEOIDLE"],
        timeout_s=15,
    )
    sleep_indexes = _extract_current_indexes(sleep.stdout if sleep.ok else "")
    video_indexes = _extract_current_indexes(video.stdout if video.ok else "")
    if sleep_indexes is None or video_indexes is None:
        return None
    ac_sleep, dc_sleep = sleep_indexes
    ac_video, dc_video = video_indexes
    return PowerState(
        scheme_guid=guid,
        scheme_name=name or "未知方案",
        ac_sleep_seconds=ac_sleep,
        ac_display_off_seconds=ac_video,
        dc_sleep_seconds=dc_sleep,
        dc_display_off_seconds=dc_video,
    )


def _extract_current_indexes(text: str) -> tuple[int, int] | None:
    values = [int(value, 16) for value in _INDEX_RE.findall(text)]
    if len(values) >= 2:
        return values[-2], values[-1]
    if len(values) == 1:
        return values[0], values[0]
    return None


def _apply_original_values(
    runner: CommandRunner,
    payload: dict[str, Any],
    *,
    log: bool,
) -> list[Any]:
    try:
        sleep_minutes = _seconds_to_minutes(int(payload["ac_sleep_seconds"]))
        display_minutes = _seconds_to_minutes(int(payload["ac_display_off_seconds"]))
    except (KeyError, TypeError, ValueError):
        return []
    commands = [
        ["powercfg", "/change", "standby-timeout-ac", str(sleep_minutes)],
        ["powercfg", "/change", "monitor-timeout-ac", str(display_minutes)],
    ]
    results = []
    for argv in commands:
        result = runner.mutate(argv, timeout_s=20)
        results.append(result)
        if log:
            state = "成功" if result.ok or result.simulated else "失败"
            runner.log(f"[电源] 还原 {argv[-1]} 分钟: {state}")
    return results


def _state_payload(state: PowerState) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scheme_guid": state.scheme_guid,
        "scheme_name": state.scheme_name,
        "ac_sleep_seconds": state.ac_sleep_seconds,
        "ac_display_off_seconds": state.ac_display_off_seconds,
        "dc_sleep_seconds": state.dc_sleep_seconds,
        "dc_display_off_seconds": state.dc_display_off_seconds,
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _load_state_file(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        int(payload["ac_sleep_seconds"])
        int(payload["ac_display_off_seconds"])
    except (KeyError, TypeError, ValueError):
        return None
    return payload


def _seconds_to_minutes(seconds: int) -> int:
    if seconds <= 0:
        return 0
    return max(1, (seconds + 30) // 60)


def _format_seconds(seconds: int) -> str:
    if seconds == 0:
        return "永不"
    if seconds < 0:
        return "未知"
    return f"{_seconds_to_minutes(seconds)} 分钟"


def _tail(text: str, limit: int = 300) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "无输出"
    return lines[-1][:limit]
