"""CampusStream Helper 程序入口。"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from core import __version__
from core.common import default_state_dir, resource_path
from core.diagnostics import configure_file_logging
from core.privileges import is_admin, relaunch_as_admin
from ui.theme import ThemeManager
from ui.wizard import WizardWindow


def _install_crash_handler() -> None:
    """把未捕获异常写入本地日志，避免无控制台的窗口程序静默退出。"""

    def handler(exc_type, exc, tb) -> None:
        log_path: Path | None = None
        try:
            state_dir = default_state_dir()
            state_dir.mkdir(parents=True, exist_ok=True)
            log_path = state_dir / "crash.log"
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} ===\n")
                handle.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except OSError:
            log_path = None
        QMessageBox.critical(
            None,
            "CampusStream Helper 发生错误",
            "程序遇到未处理的错误，已停止当前操作。\n"
            f"日志位置：{log_path or '无法写入日志目录'}\n\n{exc}",
        )
        if sys.stderr is not None:  # 窗口版 exe 没有控制台
            sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = handler


def _prompt_elevation(args: list[str]) -> tuple[bool, bool]:
    """返回 ``(是否继续, 是否强制 Dry-Run)``。"""
    box = QMessageBox()
    box.setWindowTitle("需要管理员权限")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText("CampusStream Helper 需要管理员权限来配置防火墙、服务和虚拟显示器。")
    box.setInformativeText(
        "你可以选择重启提权，或使用 Dry-Run 只检测、不写入系统。"
    )
    restart = box.addButton("以管理员身份重启", QMessageBox.ButtonRole.AcceptRole)
    dry_run = box.addButton("Dry-Run 继续", QMessageBox.ButtonRole.ActionRole)
    quit_button = box.addButton("退出", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(restart)
    box.exec()

    clicked = box.clickedButton()
    if clicked is restart:
        if relaunch_as_admin(args):
            return False, False
        QMessageBox.warning(
            None,
            "提权失败",
            "用户取消了 UAC，或系统拒绝提权。请重新运行并选择 Dry-Run，"
            "或右键程序选择「以管理员身份运行」。",
        )
        return False, False
    if clicked is dry_run:
        return True, True
    _ = quit_button
    return False, False


def main(argv: list[str] | None = None) -> int:
    """启动图形界面，返回进程退出码。"""
    args = list(sys.argv[1:] if argv is None else argv)
    smoke_test = "--smoke-test" in args
    if smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    app = QApplication([sys.argv[0]])
    # 诊断报告需要读取本次运行的 app.log；打包为无控制台 exe 时同样有效。
    configure_file_logging()
    app.setApplicationName("CampusStream Helper")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("CampusStream Helper Contributors")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setWindowIcon(QIcon(str(resource_path("assets/icon.ico"))))
    theme = ThemeManager(app)
    _install_crash_handler()

    force_dry_run = "--dry-run" in args or smoke_test
    if not smoke_test and not is_admin() and not force_dry_run:
        proceed, dry_run = _prompt_elevation(args)
        if not proceed:
            return 0
        force_dry_run = dry_run

    window = WizardWindow(
        force_dry_run=force_dry_run,
        auto_detect=not smoke_test,
        theme=theme,
    )
    window.show()
    if smoke_test:
        app.processEvents()
        preset_count = len(window.devices_step._items)
        if sys.stdout is not None:  # 窗口版 exe 没有控制台，stdout 可能为 None
            print(
                f"smoke ok: version={__version__}, presets={preset_count}, "
                f"steps={window.step_count}"
            )
        window.close()
        return 0 if preset_count else 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
