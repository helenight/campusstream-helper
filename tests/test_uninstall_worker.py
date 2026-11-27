"""UninstallWorker 测试：进度、日志、完成报告与异常。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.uninstaller import UninstallOptions, UninstallReport, UninstallState
from tests.fakes import FakeRunner
from ui.workers import UninstallWorker


class BrokenRunner(FakeRunner):
    def query_powershell(self, script: str, *, timeout_s: float | None = None):
        raise RuntimeError("query boom")


class UninstallWorkerTest(unittest.TestCase):
    def _worker(self, options: UninstallOptions, runner: FakeRunner):
        worker = UninstallWorker(
            options,
            dry_run=runner.dry_run,
            runner_factory=lambda _dry: runner,
        )
        progress: list[tuple[int, str]] = []
        steps: list[object] = []
        logs: list[str] = []
        reports: list[UninstallReport] = []
        failures: list[str] = []
        worker.progress.connect(lambda value, text: progress.append((value, text)))
        worker.step.connect(steps.append)
        worker.log.connect(logs.append)
        worker.finished.connect(reports.append)
        worker.failed.connect(failures.append)
        return worker, progress, steps, logs, reports, failures

    def test_dry_run_emits_progress_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp) / "CampusStreamHelper"
            state_dir.mkdir()
            (state_dir / "crash.log").write_text("log", encoding="utf-8")
            options = UninstallOptions(
                restore_display=False,
                restore_power=False,
                remove_firewall=False,
                uninstall_sunshine=False,
                uninstall_vdd=False,
                delete_vdd_directory=False,
                clean_cache=True,
                state_dir=state_dir,
            )
            runner = FakeRunner(dry_run=True)
            worker, progress, steps, logs, reports, failures = self._worker(
                options, runner
            )
            worker.run()
            self.assertTrue(state_dir.exists())

        self.assertEqual(failures, [])
        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertTrue(report.dry_run)
        self.assertEqual(report.steps[0].state, UninstallState.SIMULATED)
        self.assertEqual(len(steps), 1)
        self.assertEqual(progress[-1], (100, "清理流程结束"))
        self.assertTrue(any("[DRY-RUN]" in line for line in runner.logs))

    def test_exception_emits_failed(self) -> None:
        options = UninstallOptions(
            restore_display=False,
            restore_power=False,
            remove_firewall=False,
            uninstall_sunshine=True,
            uninstall_vdd=False,
            delete_vdd_directory=False,
            clean_cache=False,
        )
        runner = BrokenRunner()
        worker, progress, steps, logs, reports, failures = self._worker(
            options, runner
        )
        worker.run()

        self.assertEqual(reports, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("query boom", failures[0])
        self.assertIn("sunshine_stop", [step.key for step in steps])
        self.assertEqual(progress[0][0], 0)


if __name__ == "__main__":
    unittest.main()
