"""CommandRunner 的 Dry-Run、备份与原子写入测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from core.common import CommandRunner


class CommandRunnerTest(unittest.TestCase):
    def test_dry_run_mutate_is_simulated(self) -> None:
        runner = CommandRunner(dry_run=True)
        result = runner.mutate(["cmd", "/c", "exit", "1"])
        self.assertTrue(result.ok)
        self.assertTrue(result.simulated)

    def test_dry_run_write_does_not_touch_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.txt"
            runner = CommandRunner(dry_run=True)
            backup = runner.write_text_atomic(path, "new content")
            self.assertIsNone(backup)
            self.assertFalse(path.exists())

    def test_write_backup_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sunshine.conf"
            path.write_text("old = 1\n", encoding="utf-8")
            runner = CommandRunner()

            backup = runner.write_text_atomic(path, "new = 2\n")
            self.assertIsNotNone(backup)
            self.assertTrue(backup.exists())
            self.assertEqual(path.read_text(encoding="utf-8"), "new = 2\n")

            latest = runner.latest_backup(path)
            self.assertEqual(latest, backup)

            restored = runner.restore_latest_backup(path)
            self.assertTrue(restored.ok)
            self.assertEqual(path.read_text(encoding="utf-8"), "old = 1\n")

    def test_write_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / "state.json"
            runner = CommandRunner()
            runner.write_text_atomic(path, "{}\n")
            self.assertTrue(path.is_file())

    def test_query_still_executes_in_dry_run(self) -> None:
        runner = CommandRunner(dry_run=True)
        result = runner.query([sys.executable, "-c", "print('ok')"])
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_log_sink_receives_messages(self) -> None:
        messages: list[str] = []
        runner = CommandRunner(log_sink=messages.append)
        runner.log("hello")
        self.assertIn("hello", messages)


if __name__ == "__main__":
    unittest.main()
