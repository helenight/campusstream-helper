"""CampusStream Helper 程序入口。"""

from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from core import __version__
from core.privileges import is_admin, relaunch_as_admin
from ui.styles import apply_stylesheet
from ui.wizard import WizardWindow


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
    app = QApplication([sys.argv[0]])
    app.setApplicationName("CampusStream Helper")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("CampusStream Helper Contributors")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    apply_stylesheet(app)

    force_dry_run = "--dry-run" in args
    if not is_admin() and not force_dry_run:
        proceed, dry_run = _prompt_elevation(args)
        if not proceed:
            return 0
        force_dry_run = dry_run

    window = WizardWindow(force_dry_run=force_dry_run)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
