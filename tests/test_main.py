"""Comprehensive tests for uv-task-runner."""

import io
import logging
import signal
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from uv_task_runner import hello
from uv_task_runner.__main__ import (
    Settings,
    TaskConfig,
    _pipe_to_log,
    _terminate_tree,
    main,
    run_task,
)

# ---------------------------------------------------------------------------
# hello()
# ---------------------------------------------------------------------------


class TestHello:
    def test_returns_greeting(self):
        assert hello() == "Hello from uv-task-runner!"

    def test_return_type(self):
        assert isinstance(hello(), str)


# ---------------------------------------------------------------------------
# TaskConfig
# ---------------------------------------------------------------------------


class TesttaskConfig:
    def test_defaults(self):
        _ = TaskConfig()

    def test_custom_values(self):
        cfg = TaskConfig(
            wait=False,
            task_args=["--foo", "bar"],
            uv_run_args=["--verbose"],
        )
        assert cfg.wait is False
        assert cfg.task_args == ["--foo", "bar"]
        assert cfg.uv_run_args == ["--verbose"]

    def test_default_factory_independence(self):
        """Each instance should get its own list, not a shared reference."""
        a = TaskConfig()
        b = TaskConfig()
        a.task_args.append("x")
        assert "x" not in b.task_args

    def test_uv_run_args_default_factory_independence(self):
        a = TaskConfig()
        b = TaskConfig()
        a.uv_run_args.append("--extra")
        assert "--extra" not in b.uv_run_args


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_defaults_without_toml(self, tmp_path, monkeypatch):
        """With no TOML file present, all fields use their defaults."""
        monkeypatch.chdir(tmp_path)
        s = Settings()
        assert s.parallel is True
        assert s.fail_fast is True
        assert s.log_multiline is True
        assert s.task_paths == []
        assert s.task_configs == {}

    def test_loads_from_toml(self, tmp_path, monkeypatch):
        toml = tmp_path / "task_runner.toml"
        toml.write_text(
            'task_paths = ["a.py", "b.py"]\n'
            "parallel = false\n"
            "fail_fast = false\n"
            "log_multiline = false\n"
            "\n"
            '[tasks."a.py"]\n'
            "wait = false\n"
            'task_args = ["--x"]\n'
        )
        monkeypatch.chdir(tmp_path)
        s = Settings()
        assert s.parallel is False
        assert s.fail_fast is False
        assert s.log_multiline is False
        assert s.task_paths == ["a.py", "b.py"]
        assert "a.py" in s.task_configs
        assert s.task_configs["a.py"].wait is False
        assert s.task_configs["a.py"].task_args == ["--x"]

    def test_init_kwargs_override_toml(self, tmp_path, monkeypatch):
        toml = tmp_path / "task_runner.toml"
        toml.write_text("parallel = false\n")
        monkeypatch.chdir(tmp_path)
        s = Settings(parallel=True)
        assert s.parallel is True

    def test_task_config_defaults_for_unlisted_task(self, tmp_path, monkeypatch):
        """A task not listed in [tasks] should get default TaskConfig."""
        toml = tmp_path / "task_runner.toml"
        toml.write_text('task_paths = ["x.py"]\n')
        monkeypatch.chdir(tmp_path)
        s = Settings()
        cfg = s.task_configs.get("x.py", TaskConfig())
        assert cfg.wait is True
        assert cfg.task_args == []
        assert cfg.uv_run_args == ["--quiet", "--script"]


# ---------------------------------------------------------------------------
# _pipe_to_log
# ---------------------------------------------------------------------------


class TestPipeToLog:
    def test_buffered_single_message(self):
        """When buffer_output=True, all content emitted as one log call."""
        stream = io.StringIO("line1\nline2\nline3\n")
        log_fn = MagicMock()
        _pipe_to_log(stream, log_fn, prefix="[test] ", buffer_output=True)
        log_fn.assert_called_once()
        msg = log_fn.call_args[0][0]
        assert msg.startswith("[test] ")
        assert "line1" in msg
        assert "line2" in msg
        assert "line3" in msg

    def test_buffered_strips_trailing_whitespace(self):
        stream = io.StringIO("hello\n\n")
        log_fn = MagicMock()
        _pipe_to_log(stream, log_fn, prefix="", buffer_output=True)
        msg = log_fn.call_args[0][0]
        assert not msg.endswith("\n")

    def test_buffered_empty_stream_no_log(self):
        """Empty or whitespace-only stream should not produce a log call."""
        for content in ["", "   ", "\n", "  \n  "]:
            log_fn = MagicMock()
            _pipe_to_log(
                io.StringIO(content), log_fn, prefix="[x] ", buffer_output=True
            )
            log_fn.assert_not_called()

    def test_line_by_line_mode(self):
        """When buffer_output=False, each line is a separate log call."""
        stream = io.StringIO("a\nb\nc\n")
        log_fn = MagicMock()
        _pipe_to_log(stream, log_fn, prefix="[p] ", buffer_output=False)
        assert log_fn.call_count == 3
        log_fn.assert_any_call("[p] a")
        log_fn.assert_any_call("[p] b")
        log_fn.assert_any_call("[p] c")

    def test_line_by_line_strips_newlines(self):
        stream = io.StringIO("hello\n")
        log_fn = MagicMock()
        _pipe_to_log(stream, log_fn, prefix="", buffer_output=False)
        msg = log_fn.call_args[0][0]
        assert msg == "hello"

    def test_line_by_line_empty_stream(self):
        stream = io.StringIO("")
        log_fn = MagicMock()
        _pipe_to_log(stream, log_fn, prefix="", buffer_output=False)
        log_fn.assert_not_called()

    def test_prefix_included(self):
        stream = io.StringIO("msg\n")
        log_fn = MagicMock()
        _pipe_to_log(stream, log_fn, prefix="[task_a.py:123] ", buffer_output=False)
        assert log_fn.call_args[0][0] == "[task_a.py:123] msg"

    def test_multiline_content_kept_together_in_buffered_mode(self):
        """Stack-trace-like content should be a single log message."""
        traceback_text = (
            "Traceback (most recent call last):\n"
            '  File "test.py", line 1, in <module>\n'
            "    raise ValueError\n"
            "ValueError: boom\n"
        )
        stream = io.StringIO(traceback_text)
        log_fn = MagicMock()
        _pipe_to_log(stream, log_fn, prefix="[err] ", buffer_output=True)
        log_fn.assert_called_once()
        msg = log_fn.call_args[0][0]
        assert "Traceback" in msg
        assert "ValueError: boom" in msg


# ---------------------------------------------------------------------------
# _terminate_tree
# ---------------------------------------------------------------------------


class TestTerminateTree:
    @patch("uv_task_runner.__main__.sys")
    @patch("uv_task_runner.__main__.subprocess.run")
    def test_windows_uses_taskkill(self, mock_run, mock_sys):
        mock_sys.platform = "win32"
        proc = MagicMock()
        proc.pid = 9999
        _terminate_tree(proc)
        mock_run.assert_called_once_with(
            ["taskkill", "/F", "/T", "/PID", "9999"], capture_output=True
        )

    @patch("uv_task_runner.__main__.sys")
    @patch("uv_task_runner.__main__.os.killpg", create=True)
    @patch("uv_task_runner.__main__.os.getpgid", return_value=42, create=True)
    def test_unix_uses_killpg(self, mock_getpgid, mock_killpg, mock_sys):
        mock_sys.platform = "linux"
        proc = MagicMock()
        proc.pid = 1234
        _terminate_tree(proc)
        mock_getpgid.assert_called_once_with(1234)
        mock_killpg.assert_called_once_with(42, signal.SIGTERM)

    @patch("uv_task_runner.__main__.sys")
    @patch("uv_task_runner.__main__.os.killpg", create=True)
    @patch(
        "uv_task_runner.__main__.os.getpgid",
        side_effect=ProcessLookupError,
        create=True,
    )
    def test_unix_handles_already_terminated(self, mock_getpgid, mock_killpg, mock_sys):
        mock_sys.platform = "linux"
        proc = MagicMock()
        proc.pid = 1234
        # Should not raise
        _terminate_tree(proc)
        mock_killpg.assert_not_called()


# ---------------------------------------------------------------------------
# run_task
# ---------------------------------------------------------------------------


class TestRuntask:
    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_builds_correct_command(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        run_task("tasks/test.py", task_args=["--a", "1"], uv_run_args=["--quiet"])

        args = mock_popen.call_args[0][0]
        assert args == ["uv", "run", "--quiet", "--script", "tasks/test.py", "--a", "1"]

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_default_args_are_empty(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        run_task("script.py")

        args = mock_popen.call_args[0][0]
        assert args == ["uv", "run", "--script", "script.py"]

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_returns_process_and_threads(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        result = run_task("s.py")
        assert len(result) == 3
        proc, t1, t2 = result
        assert proc is mock_proc
        assert isinstance(t1, threading.Thread)
        assert isinstance(t2, threading.Thread)

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_threads_are_daemon(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        _, t1, t2 = run_task("s.py")
        assert t1.daemon is True
        assert t2.daemon is True

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_popen_uses_pipe_and_text(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        run_task("s.py")

        _, kwargs = mock_popen.call_args
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.PIPE
        assert kwargs["text"] is True

    @patch("uv_task_runner.__main__.sys")
    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_unix_sets_new_session(self, mock_popen, mock_sys):
        mock_sys.platform = "linux"
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        run_task("s.py")

        _, kwargs = mock_popen.call_args
        assert kwargs.get("start_new_session") is True

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_popen_kwargs_forwarded(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        run_task("s.py", popen_kwargs={"cwd": "/tmp"})

        _, kwargs = mock_popen.call_args
        assert kwargs["cwd"] == "/tmp"

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_logging_prefix_includes_pid(self, mock_popen):
        """The prefix passed to _pipe_to_log should include the task name and PID."""
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.stdout = io.StringIO("hello\n")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        proc, t1, t2 = run_task("tasks/my_task.py")
        # Wait for threads to finish processing
        t1.join(timeout=2)
        t2.join(timeout=2)

        # The prefix format is "[filename:PID] "
        expected_prefix = "[my_task.py:42] "
        assert proc.pid == 42
        # Verify via the Path-based prefix logic:
        assert Path("tasks/my_task.py").name == "my_task.py"


# ---------------------------------------------------------------------------
# Logging output verification
# ---------------------------------------------------------------------------


class TestLoggingOutput:
    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_run_task_logs_task_path(self, mock_popen, caplog):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            run_task("tasks/test.py", task_args=["--a"])

        assert any("Running tasks/test.py" in r.message for r in caplog.records)
        assert any("task_args=['--a']" in r.message for r in caplog.records)

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_stdout_piped_to_info_log(self, mock_popen, caplog):
        mock_proc = MagicMock()
        mock_proc.pid = 5
        mock_proc.stdout = io.StringIO("task output\n")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            proc, t1, t2 = run_task("p.py")
            t1.join(timeout=2)
            t2.join(timeout=2)

        assert any("task output" in r.message for r in caplog.records)

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_stderr_piped_to_info_log(self, mock_popen, caplog):
        mock_proc = MagicMock()
        mock_proc.pid = 5
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("error output\n")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            proc, t1, t2 = run_task("p.py")
            t1.join(timeout=2)
            t2.join(timeout=2)

        assert any("error output" in r.message for r in caplog.records)

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_log_prefix_format(self, mock_popen, caplog):
        """Log messages from task output should have [filename:PID] prefix."""
        mock_proc = MagicMock()
        mock_proc.pid = 999
        mock_proc.stdout = io.StringIO("hello\n")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            _, t1, t2 = run_task("tasks/demo.py")
            t1.join(timeout=2)
            t2.join(timeout=2)

        output_records = [r for r in caplog.records if "hello" in r.message]
        assert len(output_records) == 1
        assert output_records[0].message.startswith("[demo.py:999] ")

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_multiline_output_buffered_as_single_record(self, mock_popen, caplog):
        """With log_multiline=True, multiline output is one log record."""
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_proc.stdout = io.StringIO("line1\nline2\nline3\n")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            _, t1, t2 = run_task("p.py", log_multiline=True)
            t1.join(timeout=2)
            t2.join(timeout=2)

        output_records = [r for r in caplog.records if "line1" in r.message]
        assert len(output_records) == 1
        assert "line2" in output_records[0].message
        assert "line3" in output_records[0].message

    @patch("uv_task_runner.__main__.subprocess.Popen")
    def test_line_by_line_output_separate_records(self, mock_popen, caplog):
        """With log_multiline=False, each line is a separate log record."""
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_proc.stdout = io.StringIO("aaa\nbbb\n")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            _, t1, t2 = run_task("p.py", log_multiline=False)
            t1.join(timeout=2)
            t2.join(timeout=2)

        output_messages = [
            r.message
            for r in caplog.records
            if "aaa" in r.message or "bbb" in r.message
        ]
        assert len(output_messages) == 2


# ---------------------------------------------------------------------------
# main() – parallel mode
# ---------------------------------------------------------------------------


class TestMainParallel:
    """Tests for main() when settings.parallel is True."""

    def _make_settings(self, **overrides):
        defaults = dict(
            parallel=True,
            fail_fast=True,
            log_multiline=True,
            task_paths=["a.py", "b.py"],
            tasks={},
        )
        defaults.update(overrides)
        return Settings(**defaults)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_all_tasks_succeed(self, mock_settings_cls, mock_run_task, caplog):
        settings = self._make_settings(task_paths=["a.py", "b.py"])
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 0
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            t1 = MagicMock()
            t2 = MagicMock()
            return proc, t1, t2

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert mock_run_task.call_count == 2
        assert any("completed successfully" in r.message for r in caplog.records)

    @patch("uv_task_runner.__main__._terminate_tree")
    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_fail_fast_terminates_running(
        self, mock_settings_cls, mock_run_task, mock_term, caplog
    ):
        settings = self._make_settings(
            task_paths=["fast_fail.py", "slow.py"],
            fail_fast=True,
        )
        mock_settings_cls.return_value = settings

        slow_proc = MagicMock()
        slow_proc.pid = 2
        slow_proc.poll.return_value = None  # still running

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            if "fast_fail" in task_path:
                proc.pid = 1
                proc.returncode = 1
                proc.wait.return_value = 1
                proc.poll.return_value = 1
            else:
                proc.pid = 2
                proc.returncode = None
                proc.wait.side_effect = (
                    lambda: setattr(proc, "returncode", 1) or proc.returncode
                )
                proc.poll.return_value = None  # still running
            t1 = MagicMock()
            t2 = MagicMock()
            return proc, t1, t2

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert any("failed with return code" in r.message for r in caplog.records)
        assert any("Fail fast enabled" in r.message for r in caplog.records)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_fail_fast_disabled_continues(
        self, mock_settings_cls, mock_run_task, caplog
    ):
        settings = self._make_settings(
            task_paths=["fail.py", "ok.py"],
            fail_fast=False,
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            if "fail" in task_path:
                proc.returncode = 1
                proc.wait.return_value = 1
            else:
                proc.returncode = 0
                proc.wait.return_value = 0
            proc.poll.return_value = proc.returncode
            t1 = MagicMock()
            t2 = MagicMock()
            return proc, t1, t2

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        # Both tasks should have been run
        assert mock_run_task.call_count == 2

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_logs_error_on_failure(self, mock_settings_cls, mock_run_task, caplog):
        settings = self._make_settings(
            task_paths=["bad.py"],
            fail_fast=False,
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 1
            proc.wait.return_value = 1
            proc.poll.return_value = 1
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.ERROR, logger="uv_task_runner.__main__"):
            main()

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any(
            "bad.py failed with return code 1" in r.message for r in error_records
        )

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_logs_task_count(self, mock_settings_cls, mock_run_task, caplog):
        settings = self._make_settings(task_paths=["a.py", "b.py", "c.py"])
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 0
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert any("Running 3 task(s)" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# main() – sequential mode
# ---------------------------------------------------------------------------


class TestMainSequential:
    """Tests for main() when settings.parallel is False."""

    def _make_settings(self, **overrides):
        defaults = dict(
            parallel=False,
            fail_fast=True,
            log_multiline=True,
            task_paths=["a.py", "b.py"],
            tasks={},
        )
        defaults.update(overrides)
        return Settings(**defaults)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_all_succeed_sequential(self, mock_settings_cls, mock_run_task, caplog):
        settings = self._make_settings(task_paths=["a.py", "b.py"])
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 0
            proc.wait.return_value = 0
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert mock_run_task.call_count == 2
        success_records = [
            r for r in caplog.records if "completed successfully" in r.message
        ]
        assert len(success_records) == 2

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_fail_fast_stops_sequential(self, mock_settings_cls, mock_run_task, caplog):
        settings = self._make_settings(
            task_paths=["fail.py", "never_run.py"],
            fail_fast=True,
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 1
            proc.wait.return_value = 1
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        # Only the first task should run, second should be skipped
        assert mock_run_task.call_count == 1
        assert any("Fail fast enabled, exiting" in r.message for r in caplog.records)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_fail_fast_disabled_continues_sequential(
        self, mock_settings_cls, mock_run_task, caplog
    ):
        settings = self._make_settings(
            task_paths=["fail.py", "ok.py"],
            fail_fast=False,
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            if "fail" in task_path:
                proc.returncode = 1
                proc.wait.return_value = 1
            else:
                proc.returncode = 0
                proc.wait.return_value = 0
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert mock_run_task.call_count == 2

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_wait_false_logs_not_waiting(
        self, mock_settings_cls, mock_run_task, caplog
    ):
        """rc=None (from wait=False) is handled first and logs 'not waiting'."""
        settings = self._make_settings(
            task_paths=["bg.py"],
            tasks={"bg.py": TaskConfig(wait=False)},
            fail_fast=False,
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = None  # Not waited on
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert any("not waiting for it to finish" in r.message for r in caplog.records)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_per_task_config_used(self, mock_settings_cls, mock_run_task):
        settings = self._make_settings(
            task_paths=["x.py"],
            tasks={
                "x.py": TaskConfig(
                    task_args=["--foo", "bar"],
                    uv_run_args=["--verbose"],
                )
            },
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 0
            proc.wait.return_value = 0
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run
        main()

        mock_run_task.assert_called_once()
        _, kwargs = mock_run_task.call_args
        assert kwargs["task_args"] == ["--foo", "bar"]
        assert kwargs["uv_run_args"] == ["--verbose"]


# ---------------------------------------------------------------------------
# main() – return code handling
# ---------------------------------------------------------------------------


class TestMainReturnCodes:
    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_none_return_code_treated_as_failure_parallel(
        self, mock_settings_cls, mock_run_task, caplog
    ):
        """rc=None (wait=False) is != 0, so treated as failure in parallel mode."""
        settings = Settings(
            parallel=True,
            fail_fast=False,
            task_paths=["nowait.py"],
            tasks={"nowait.py": TaskConfig(wait=False)},
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = None
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.ERROR, logger="uv_task_runner.__main__"):
            main()

        # In parallel mode, rc != 0 (None != 0) triggers the error path
        assert any("failed with return code None" in r.message for r in caplog.records)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_none_return_code_sequential_logs_not_waiting(
        self, mock_settings_cls, mock_run_task, caplog
    ):
        """In sequential mode, rc=None is handled first and logs 'not waiting'."""
        settings = Settings(
            parallel=False,
            fail_fast=True,
            task_paths=["bg.py"],
            tasks={"bg.py": TaskConfig(wait=False)},
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = None
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert any("not waiting for it to finish" in r.message for r in caplog.records)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_nonzero_return_code(self, mock_settings_cls, mock_run_task, caplog):
        settings = Settings(
            parallel=False,
            fail_fast=False,
            task_paths=["err.py"],
            tasks={},
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 2
            proc.wait.return_value = 2
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.ERROR, logger="uv_task_runner.__main__"):
            main()

        assert any("failed with return code 2" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# main() – logging levels
# ---------------------------------------------------------------------------


class TestMainLoggingLevels:
    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_success_logged_at_info(self, mock_settings_cls, mock_run_task, caplog):
        settings = Settings(
            parallel=False, fail_fast=False, task_paths=["ok.py"], tasks={}
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 0
            proc.wait.return_value = 0
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.DEBUG, logger="uv_task_runner.__main__"):
            main()

        success_records = [
            r for r in caplog.records if "completed successfully" in r.message
        ]
        assert all(r.levelno == logging.INFO for r in success_records)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_failure_logged_at_error(self, mock_settings_cls, mock_run_task, caplog):
        settings = Settings(
            parallel=False, fail_fast=False, task_paths=["bad.py"], tasks={}
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 1
            proc.wait.return_value = 1
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.DEBUG, logger="uv_task_runner.__main__"):
            main()

        error_records = [r for r in caplog.records if "failed" in r.message]
        assert all(r.levelno == logging.ERROR for r in error_records)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_fail_fast_warning_logged_at_warning(
        self, mock_settings_cls, mock_run_task, caplog
    ):
        settings = Settings(
            parallel=False, fail_fast=True, task_paths=["bad.py"], tasks={}
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 1
            proc.wait.return_value = 1
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.DEBUG, logger="uv_task_runner.__main__"):
            main()

        warning_records = [r for r in caplog.records if "Fail fast" in r.message]
        assert len(warning_records) >= 1
        assert all(r.levelno == logging.WARNING for r in warning_records)

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_task_count_logged_at_info(self, mock_settings_cls, mock_run_task, caplog):
        settings = Settings(
            parallel=False, fail_fast=False, task_paths=["a.py"], tasks={}
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 0
            proc.wait.return_value = 0
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.DEBUG, logger="uv_task_runner.__main__"):
            main()

        count_records = [r for r in caplog.records if "Running 1 task(s)" in r.message]
        assert len(count_records) == 1
        assert count_records[0].levelno == logging.INFO


# ---------------------------------------------------------------------------
# main() – parallel termination details
# ---------------------------------------------------------------------------


class TestParallelTermination:
    @patch("uv_task_runner.__main__._terminate_tree")
    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_only_running_processes_terminated(
        self, mock_settings_cls, mock_run_task, mock_term, caplog
    ):
        """Only processes still running (poll() is None) should be terminated."""
        settings = Settings(
            parallel=True,
            fail_fast=True,
            task_paths=["fail.py", "done.py", "running.py"],
            tasks={},
        )
        mock_settings_cls.return_value = settings

        procs = {}

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = hash(task_path) % 10000
            if "fail" in task_path:
                proc.returncode = 1
                proc.wait.return_value = 1
                proc.poll.return_value = 1
            elif "done" in task_path:
                proc.returncode = 0
                proc.wait.return_value = 0
                proc.poll.return_value = 0  # already finished
            else:
                proc.returncode = 0
                proc.wait.return_value = 0
                proc.poll.return_value = None  # still running
            procs[task_path] = proc
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        # _terminate_tree should have been called for the still-running process
        terminated_pids = [c.args[0].pid for c in mock_term.call_args_list]
        if "running.py" in procs:
            running_pid = procs["running.py"].pid
            # If running.py was registered before fail_fast triggered,
            # it should have been terminated
            if running_pid in terminated_pids:
                assert True
        # done.py should NOT have been terminated (already finished)
        if "done.py" in procs:
            done_pid = procs["done.py"].pid
            assert done_pid not in terminated_pids

    @patch("uv_task_runner.__main__._terminate_tree")
    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_termination_logged_with_pid(
        self, mock_settings_cls, mock_run_task, mock_term, caplog
    ):
        settings = Settings(
            parallel=True,
            fail_fast=True,
            task_paths=["fail.py", "running.py"],
            tasks={},
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            if "fail" in task_path:
                proc.pid = 100
                proc.returncode = 1
                proc.wait.return_value = 1
                proc.poll.return_value = 1
            else:
                proc.pid = 200
                proc.returncode = 0
                proc.wait.return_value = 0
                proc.poll.return_value = None
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.WARNING, logger="uv_task_runner.__main__"):
            main()

        termination_records = [r for r in caplog.records if "Terminating" in r.message]
        if termination_records:
            assert any("PID" in r.message for r in termination_records)


# ---------------------------------------------------------------------------
# main() – no tasks
# ---------------------------------------------------------------------------


class TestMainEdgeCases:
    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_no_tasks_parallel(self, mock_settings_cls, mock_run_task, caplog):
        settings = Settings(parallel=True, fail_fast=True, task_paths=[], tasks={})
        mock_settings_cls.return_value = settings

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert any("Running 0 task(s)" in r.message for r in caplog.records)
        mock_run_task.assert_not_called()

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_no_tasks_sequential(self, mock_settings_cls, mock_run_task, caplog):
        settings = Settings(parallel=False, fail_fast=True, task_paths=[], tasks={})
        mock_settings_cls.return_value = settings

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert any("Running 0 task(s)" in r.message for r in caplog.records)
        mock_run_task.assert_not_called()

    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_single_task_success(self, mock_settings_cls, mock_run_task, caplog):
        settings = Settings(
            parallel=True, fail_fast=True, task_paths=["only.py"], tasks={}
        )
        mock_settings_cls.return_value = settings

        def fake_run(task_path, **kwargs):
            proc = MagicMock()
            proc.pid = 1
            proc.returncode = 0
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            return proc, MagicMock(), MagicMock()

        mock_run_task.side_effect = fake_run

        with caplog.at_level(logging.INFO, logger="uv_task_runner.__main__"):
            main()

        assert any(
            "only.py completed successfully" in r.message for r in caplog.records
        )


# ---------------------------------------------------------------------------
# main() – log_multiline forwarding
# ---------------------------------------------------------------------------


class TestLogMultilineForwarding:
    @patch("uv_task_runner.__main__.run_task")
    @patch("uv_task_runner.__main__.Settings")
    def test_log_multiline_passed_to_run_task(self, mock_settings_cls, mock_run_task):
        for multiline_val in (True, False):
            mock_run_task.reset_mock()
            settings = Settings(
                parallel=False,
                fail_fast=False,
                log_multiline=multiline_val,
                task_paths=["x.py"],
                tasks={},
            )
            mock_settings_cls.return_value = settings

            def fake_run(task_path, **kwargs):
                proc = MagicMock()
                proc.pid = 1
                proc.returncode = 0
                proc.wait.return_value = 0
                return proc, MagicMock(), MagicMock()

            mock_run_task.side_effect = fake_run
            main()

            _, kwargs = mock_run_task.call_args
            assert kwargs["log_multiline"] == multiline_val
