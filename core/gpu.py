"""显卡检测：读取 WMI ``Win32_VideoController`` 信息。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.common import CommandRunner

_GPU_QUERY = (
    "$ErrorActionPreference = 'Stop'; "
    "Get-CimInstance Win32_VideoController | "
    "Select-Object Name, DriverVersion, Status, AdapterRAM | "
    "ConvertTo-Json -Compress"
)


@dataclass(frozen=True, slots=True)
class GpuInfo:
    """一块主显卡的检测摘要。"""

    name: str
    driver_version: str
    status: str
    is_basic_adapter: bool
    detail: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.name) and not self.is_basic_adapter


def _tail(text: str, limit: int = 160) -> str:
    compact = " ".join((text or "").split())
    return compact[-limit:] if len(compact) > limit else compact


def _is_basic_adapter(name: str) -> bool:
    lower = name.lower()
    basic = "basic" in lower and ("display" in lower or "render" in lower)
    remote = "remote" in lower and "display" in lower
    return basic or remote


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def detect_gpu(runner: CommandRunner) -> GpuInfo:
    """选择主显卡；基础显示适配器视为需要安装驱动。"""
    result = runner.query_powershell(_GPU_QUERY, timeout_s=20)
    if not result.ok:
        reason = _tail(result.stderr or result.stdout or "未知错误")
        return GpuInfo("", "", "", False, "显卡检测失败", reason or "未知错误")

    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        return GpuInfo("", "", "", False, "显卡检测失败", f"WMI 返回无法解析：{exc}")

    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for record in _records(payload):
        name = str(record.get("Name") or "").strip()
        if not name:
            continue
        basic = _is_basic_adapter(name)
        try:
            memory = int(record.get("AdapterRAM") or 0)
        except (TypeError, ValueError):
            memory = 0
        candidates.append((0 if basic else 1, memory, record))

    if not candidates:
        return GpuInfo("", "", "", False, "显卡检测失败", "未检测到任何显卡适配器")

    _score, _memory, best = max(candidates, key=lambda item: (item[0], item[1]))
    name = str(best.get("Name") or "").strip()
    driver = str(best.get("DriverVersion") or "").strip()
    status = str(best.get("Status") or "Unknown").strip()
    basic = _is_basic_adapter(name)
    if basic:
        detail = "仅检测到基础显示适配器，请安装显卡驱动后再串流"
    else:
        detail = f"{name} · 驱动 {driver or '未知'}"
    return GpuInfo(
        name=name,
        driver_version=driver,
        status=status,
        is_basic_adapter=basic,
        detail=detail,
        error=None,
    )
