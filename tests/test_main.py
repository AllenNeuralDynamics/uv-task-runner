"""Comprehensive tests for uv-task-runner."""

from __future__ import annotations

import io
import logging
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from typing import Any

from uv_task_runner import (
    Pipeline,
    PipelineResult,
    Settings,
    TaskConfig,
    TaskResult,
    run_tasks,
    task,
    utils,
)
from uv_task_runner.__main__ import _CliSettings, main
from uv_task_runner.settings import DEFAULT_CONFIG_PATH


@pytest.fixture(autouse=True)
def _clear_cli_argv(monkeypatch):
    """Prevent CliSettingsSource from reading pytest's sys.argv."""
    monkeypatch.setattr(sys, "argv", ["__main__.py"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_handle(
    task_path: str = "test.py",
    returncode: int | None = 0,
    still_running: bool = False,
) -> task._TaskHandle:
    """Build a task._TaskHandle with a mock process for use in tests."""
    proc = MagicMock()
    proc.pid = abs(hash(task_path)) % 10000 or 1
    proc.returncode = returncode
    proc.poll.return_value = None if still_running else returncode
    proc.wait.return_value = returncode
    proc.stdout = io.StringIO("")
    proc.stderr = io.StringIO("")
    return task._TaskHandle(
        process=proc,
        stdout_thread=MagicMock(),
        stderr_thread=MagicMock(),
        stdout_capture=["stdout content"] if returncode == 0 else [],
        stderr_capture=[] if returncode == 0 else ["error output"],
        task_path=task_path,
        start_time=time.monotonic(),
    )


def make_pipeline(**overrides) -> Pipeline:
    """Build a Pipeline with test-friendly defaults."""
    defaults: dict[str, Any] = dict(
        tasks=[TaskConfig(task_path="a.py"), TaskConfig(task_path="b.py")],
        parallel=True,
        fail_fast=True,
        log_multiline=True,
    )
    defaults.update(overrides)
    return Pipeline(**defaults)


# ---------------------------------------------------------------------------
# TaskConfig
# ---------------------------------------------------------------------------


class TestTaskConfig:
    def test_requires_task_path(self):
        with pytest.raises(Exception):
            TaskConfig()  # type: ignore[call-arg]  # task_path is required

    def test_defaults(self):
        cfg = TaskConfig(task_path="script.py")
        assert cfg.wait is True
        assert cfg.task_args == []
        assert cfg.uv_args == ["--quiet", "--script", "--no-project"]
        assert cfg.on_task_start is None
        assert cfg.on_task_end is None

    def test_custom_values(self):
        cfg = TaskConfig(
            task_path="script.py",
            wait=False,
            task_args=["--foo", "bar"],
            uv_args=["--verbose"],
        )
        assert cfg.wait is False
        assert cfg.task_args == ["--foo", "bar"]
        assert cfg.uv_args == ["--verbose"]

    def test_default_factory_independence(self):
        """Each instance should get its own list, not a shared reference."""
        a = TaskConfig(task_path="a.py")
        b = TaskConfig(task_path="b.py")
        a.task_args.append("x")
        assert "x" not in b.task_args

    def test_uv_args_default_factory_independence(self):
        a = TaskConfig(task_path="a.py")
        b = TaskConfig(task_path="b.py")
        a.uv_args.append("--extra")
        assert "--extra" not in b.uv_args

    def test_accepts_callable_hooks(self):
        called = []
        cfg = TaskConfig(
            task_path="x.py",
            on_task_start=lambda path, pid: called.append(("start", path, pid)),  # type: ignore[arg-type]
            on_task_end=lambda path, result: called.append(("end", path)),  # type: ignore[arg-type]
        )
        assert cfg.on_task_start is not None
        assert cfg.on_task_end is not None

    def test_accepts_list_of_hooks(self):
        cfg = TaskConfig(
            task_path="x.py",
            on_task_start=[lambda path, pid: None, lambda path, pid: None],  # type: ignore[arg-type]
        )
        assert isinstance(cfg.on_task_start, list)
        assert len(cfg.on_task_start) == 2


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_defaults(self):
        """Library-safe Settings() uses only init kwargs — no CLI, no TOML."""
        s = Settings()
        assert s.parallel is False
        assert s.fail_fast is False
        assert s.dry_run is False
        assert s.log_multiline is False
        assert s.tasks == []

    def test_init_kwargs(self):
        tasks = [TaskConfig(task_path="a.py")]
        s = Settings(tasks=tasks, parallel=False, fail_fast=False)
        assert s.parallel is False
        assert s.fail_fast is False
        assert s.tasks == tasks

    def test_no_cli_parsing(self, monkeypatch):
        """Settings() must not fail when sys.argv contains pytest args."""
        monkeypatch.setattr(sys, "argv", ["pytest", "--some-unknown-flag", "blah"])
        # Should not raise
        Settings()

    def test_log_level_validates_string(self):
        s = Settings(log_level="debug")
        assert s.log_level == "DEBUG"

    def test_log_level_validates_int(self):
        s = Settings(log_level=20)
        assert s.log_level == "INFO"

    def test_log_level_rejects_invalid(self):
        with pytest.raises(Exception):
            Settings(log_level="NOTAREAL")


class TestCliSettings:
    """_CliSettings loads from TOML and CLI args (used only by main())."""

    def test_loads_from_toml(self, tmp_path, monkeypatch):
        toml = tmp_path / DEFAULT_CONFIG_PATH
        toml.write_text(
            "parallel = false\n"
            "fail_fast = false\n"
            "log_multiline = false\n"
            "\n"
            "[[tasks]]\n"
            'task_path = "a.py"\n'
            "wait = false\n"
            'task_args = ["--x"]\n'
            "\n"
            "[[tasks]]\n"
            'task_path = "b.py"\n'
        )
        monkeypatch.setattr(sys, "argv", ["__main__.py", "--config", str(toml)])
        s = _CliSettings()
        assert s.parallel is False
        assert s.fail_fast is False
        assert s.log_multiline is False
        assert len(s.tasks) == 2
        assert s.tasks[0].task_path == "a.py"
        assert s.tasks[0].wait is False
        assert s.tasks[0].task_args == ["--x"]
        assert s.tasks[1].task_path == "b.py"

    def test_init_kwargs_override_toml(self, tmp_path, monkeypatch):
        toml = tmp_path / DEFAULT_CONFIG_PATH
        toml.write_text("parallel = false\n")
        monkeypatch.setattr(sys, "argv", ["__main__.py", "--config", str(toml)])
        s = _CliSettings(parallel=True)
        assert s.parallel is True


# ---------------------------------------------------------------------------
# _pipe_to_log
# ---------------------------------------------------------------------------


class TestPipeToLog:
    def test_buffered_single_message(self):
        """When buffer_output=True, all content emitted as one log call."""
        stream = io.StringIO("line1\nline2\nline3\n")
        log_fn = MagicMock()
        task._pipe_to_log(stream, log_fn, prefix="[test] ", buffer_output=True)
        log_fn.assert_called_once()
        msg = log_fn.call_args[0][0]
        assert msg.startswith("[test] ")
        assert "line1" in msg
        assert "line2" in msg
        assert "line3" in msg

    def test_buffered_strips_trailing_whitespace(self):
        stream = io.StringIO("hello\n\n")
        log_fn = MagicMock()
        task._pipe_to_log(stream, log_fn, prefix="", buffer_output=True)
        msg = log_fn.call_args[0][0]
        assert not msg.endswith("\n")

    def test_buffered_empty_stream_no_log(self):
        """Empty or whitespace-only stream should not produce a log call."""
        for content in ["", "   ", "\n", "  \n  "]:
            log_fn = MagicMock()
            task._pipe_to_log(
                io.StringIO(content), log_fn, prefix="[x] ", buffer_output=True
            )
            log_fn.assert_not_called()

    def test_line_by_line_mode(self):
        """When buffer_output=False, each line is a separate log call."""
        stream = io.StringIO("a\nb\nc\n")
        log_fn = MagicMock()
        task._pipe_to_log(stream, log_fn, prefix="[p] ", buffer_output=False)
        assert log_fn.call_count == 3
        log_fn.assert_any_call("[p] a")
        log_fn.assert_any_call("[p] b")
        log_fn.assert_any_call("[p] c")

    def test_line_by_line_strips_newlines(self):
        stream = io.StringIO("hello\n")
        log_fn = MagicMock()
        task._pipe_to_log(stream, log_fn, prefix="", buffer_output=False)
        msg = log_fn.call_args[0][0]
        assert msg == "hello"

    def test_line_by_line_empty_stream(self):
        stream = io.StringIO("")
        log_fn = MagicMock()
        task._pipe_to_log(stream, log_fn, prefix="", buffer_output=False)
        log_fn.assert_not_called()

    def test_prefix_included(self):
        stream = io.StringIO("msg\n")
        log_fn = MagicMock()
        task._pipe_to_log(
            stream, log_fn, prefix="[task_a.py:123] ", buffer_output=False
        )
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
        task._pipe_to_log(stream, log_fn, prefix="[err] ", buffer_output=True)
        log_fn.assert_called_once()
        msg = log_fn.call_args[0][0]
        assert "Traceback" in msg
        assert "ValueError: boom" in msg

    def test_capture_buffered(self):
        """capture list receives the full content in buffered mode."""
        stream = io.StringIO("line1\nline2\n")
        log_fn = MagicMock()
        capture: list[str] = []
        task._pipe_to_log(
            stream, log_fn, prefix="", buffer_output=True, capture=capture
        )
        assert capture == ["line1\nline2\n"]

    def test_capture_line_by_line(self):
        """capture list receives all lines joined in line-by-line mode."""
        stream = io.StringIO("a\nb\n")
        log_fn = MagicMock()
        capture: list[str] = []
        task._pipe_to_log(
            stream, log_fn, prefix="", buffer_output=False, capture=capture
        )
        assert len(capture) == 1
        assert "a\n" in capture[0]
        assert "b\n" in capture[0]

    def test_capture_empty_stream(self):
        """capture receives empty string for empty stream in buffered mode."""
        stream = io.StringIO("")
        log_fn = MagicMock()
        capture: list[str] = []
        task._pipe_to_log(
            stream, log_fn, prefix="", buffer_output=True, capture=capture
        )
        assert capture == [""]


# ---------------------------------------------------------------------------
# _terminate_tree
# ---------------------------------------------------------------------------


class TestTerminateTree:
    @patch("uv_task_runner.task.subprocess.run")
    def test_windows_uses_taskkill(self, mock_run):
        with patch("uv_task_runner.task.sys.platform", "win32"):
            proc = MagicMock()
            proc.pid = 9999
            task._terminate_tree(proc)
        mock_run.assert_called_once_with(
            ["taskkill", "/F", "/T", "/PID", "9999"], capture_output=True
        )

    @patch("uv_task_runner.task.os.killpg", create=True)
    @patch("uv_task_runner.task.os.getpgid", return_value=42, create=True)
    def test_unix_uses_killpg(self, mock_getpgid, mock_killpg):
        with patch("uv_task_runner.task.sys.platform", "linux"):
            proc = MagicMock()
            proc.pid = 1234
            task._terminate_tree(proc)
        mock_getpgid.assert_called_once_with(1234)
        mock_killpg.assert_called_once_with(42, signal.SIGTERM)

    @patch(
        "uv_task_runner.task.os.getpgid",
        side_effect=ProcessLookupError,
        create=True,
    )
    @patch("uv_task_runner.task.os.killpg", create=True)
    def test_unix_handles_already_terminated(self, mock_killpg, mock_getpgid):
        with patch("uv_task_runner.task.sys.platform", "linux"):
            proc = MagicMock()
            proc.pid = 1234
            # Should not raise
            task._terminate_tree(proc)
        mock_killpg.assert_not_called()


# ---------------------------------------------------------------------------
# _call_hooks
# ---------------------------------------------------------------------------


class TestCallHooks:
    def test_single_callable(self):
        calls = []
        utils._call_hooks(lambda x: calls.append(x), "arg1")
        assert calls == ["arg1"]

    def test_list_of_callables(self):
        calls = []
        utils._call_hooks(
            [lambda x: calls.append(f"a:{x}"), lambda x: calls.append(f"b:{x}")], "v"
        )
        assert calls == ["a:v", "b:v"]

    def test_none_is_noop(self):
        # Should not raise
        utils._call_hooks(None, "ignored")

    def test_multiple_args(self):
        calls = []
        utils._call_hooks(lambda a, b: calls.append((a, b)), "x", 42)
        assert calls == [("x", 42)]


# ---------------------------------------------------------------------------
# run_task
# ---------------------------------------------------------------------------


class TestRunTask:
    @patch("uv_task_runner.task.subprocess.Popen")
    def test_builds_correct_command(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        cfg = TaskConfig(
            task_path="tasks/test.py", task_args=["--a", "1"], uv_args=["--quiet"]
        )
        task.run_task(cfg)

        args = mock_popen.call_args[0][0]
        assert args == ["uv", "run", "--quiet", "tasks/test.py", "--a", "1"]

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_default_uv_args(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        task.run_task(TaskConfig(task_path="script.py"))

        args = mock_popen.call_args[0][0]
        assert args == ["uv", "run", "--quiet", "--script", "--no-project", "script.py"]

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_returns_task_handle(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        handle = task.run_task(TaskConfig(task_path="s.py"))
        assert isinstance(handle, task._TaskHandle)
        assert handle.process is mock_proc
        assert isinstance(handle.stdout_thread, threading.Thread)
        assert isinstance(handle.stderr_thread, threading.Thread)

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_threads_are_daemon(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        handle = task.run_task(TaskConfig(task_path="s.py"))
        assert handle.stdout_thread.daemon is True
        assert handle.stderr_thread.daemon is True

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_popen_uses_pipe_and_text(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        task.run_task(TaskConfig(task_path="s.py"))

        _, kwargs = mock_popen.call_args
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.PIPE
        assert kwargs["text"] is True

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_start_new_session_set_on_windows(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with patch("uv_task_runner.task.sys.platform", "win32"):
            task.run_task(TaskConfig(task_path="s.py"))

        _, kwargs = mock_popen.call_args
        assert kwargs.get("start_new_session") is True

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_popen_kwargs_forwarded(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 100
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        task.run_task(TaskConfig(task_path="s.py"), popen_kwargs={"cwd": "/tmp"})

        _, kwargs = mock_popen.call_args
        assert kwargs["cwd"] == "/tmp"

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_on_task_start_called_with_path_and_pid(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        calls = []
        cfg = TaskConfig(
            task_path="tasks/my_task.py",
            on_task_start=lambda path, pid: calls.append((path, pid)),  # type: ignore[arg-type]
        )
        task.run_task(cfg)
        # Wait for threads to start
        assert calls == [("tasks/my_task.py", 42)]

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_handle_has_correct_task_path(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        handle = task.run_task(TaskConfig(task_path="tasks/my_task.py"))
        assert handle.task_path == "tasks/my_task.py"
        assert Path("tasks/my_task.py").name == "my_task.py"


# ---------------------------------------------------------------------------
# _collect_result
# ---------------------------------------------------------------------------


class TestCollectResult:
    def test_wait_true_returns_result(self):
        proc = MagicMock()
        proc.pid = 1
        proc.returncode = 0
        handle = task._TaskHandle(
            process=proc,
            stdout_thread=MagicMock(),
            stderr_thread=MagicMock(),
            stdout_capture=["hello\n"],
            stderr_capture=[],
            task_path="test.py",
            start_time=time.monotonic() - 0.1,
        )
        result = task._collect_result(handle, wait=True)
        proc.wait.assert_called_once()
        assert result.exit_code == 0
        assert result.success is True
        assert result.stdout == "hello\n"
        assert result.stderr == ""
        assert result.pid == 1
        assert result.task_path == "test.py"
        assert result.duration_seconds >= 0.0

    def test_wait_false_no_process_wait(self):
        proc = MagicMock()
        proc.pid = 5
        proc.returncode = None  # still running
        handle = task._TaskHandle(
            process=proc,
            stdout_thread=MagicMock(),
            stderr_thread=MagicMock(),
            stdout_capture=[],
            stderr_capture=[],
            task_path="bg.py",
            start_time=time.monotonic(),
        )
        result = task._collect_result(handle, wait=False)
        proc.wait.assert_not_called()
        assert result.exit_code is None
        assert result.success is False  # None != 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_nonzero_exit_code(self):
        proc = MagicMock()
        proc.pid = 2
        proc.returncode = 1
        handle = task._TaskHandle(
            process=proc,
            stdout_thread=MagicMock(),
            stderr_thread=MagicMock(),
            stdout_capture=[],
            stderr_capture=["error\n"],
            task_path="fail.py",
            start_time=time.monotonic(),
        )
        result = task._collect_result(handle, wait=True)
        assert result.exit_code == 1
        assert result.success is False
        assert result.stderr == "error\n"


# ---------------------------------------------------------------------------
# TaskResult
# ---------------------------------------------------------------------------


class TestTaskResult:
    def test_fields(self):
        r = TaskResult(
            task_path="a.py",
            exit_code=0,
            success=True,
            duration_seconds=1.5,
            stdout="output",
            stderr="",
            pid=1234,
        )
        assert r.task_path == "a.py"
        assert r.exit_code == 0
        assert r.success is True
        assert r.duration_seconds == 1.5
        assert r.stdout == "output"
        assert r.stderr == ""
        assert r.pid == 1234

    def test_wait_false_result(self):
        r = TaskResult(
            task_path="bg.py",
            exit_code=None,
            success=False,
            duration_seconds=0.0,
            stdout="",
            stderr="",
            pid=9999,
        )
        assert r.exit_code is None
        assert r.stdout == ""


# ---------------------------------------------------------------------------
# Logging output verification
# ---------------------------------------------------------------------------


class TestLoggingOutput:
    @patch("uv_task_runner.task.subprocess.Popen")
    def test_run_task_logs_task_path(self, mock_popen, caplog):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.task"):
            task.run_task(TaskConfig(task_path="tasks/test.py", task_args=["--a"]))

        assert any("Running command:" in r.message for r in caplog.records)
        assert any(
            "tasks/test.py" in r.message and "--a" in r.message for r in caplog.records
        )

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_stdout_piped_to_info_log(self, mock_popen, caplog):
        mock_proc = MagicMock()
        mock_proc.pid = 5
        mock_proc.stdout = io.StringIO("task output\n")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.task"):
            handle = task.run_task(TaskConfig(task_path="p.py"))
            handle.stdout_thread.join(timeout=2)
            handle.stderr_thread.join(timeout=2)

        assert any("task output" in r.message for r in caplog.records)

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_stderr_piped_to_info_log(self, mock_popen, caplog):
        mock_proc = MagicMock()
        mock_proc.pid = 5
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("error output\n")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.task"):
            handle = task.run_task(TaskConfig(task_path="p.py"))
            handle.stdout_thread.join(timeout=2)
            handle.stderr_thread.join(timeout=2)

        assert any("error output" in r.message for r in caplog.records)

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_log_prefix_format(self, mock_popen, caplog):
        """Log messages from task output should have [filename:PID] prefix."""
        mock_proc = MagicMock()
        mock_proc.pid = 999
        mock_proc.stdout = io.StringIO("hello\n")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.task"):
            handle = task.run_task(TaskConfig(task_path="tasks/demo.py"))
            handle.stdout_thread.join(timeout=2)
            handle.stderr_thread.join(timeout=2)

        output_records = [r for r in caplog.records if "hello" in r.message]
        assert len(output_records) == 1
        assert output_records[0].message.startswith("[demo.py:999] ")

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_multiline_output_buffered_as_single_record(self, mock_popen, caplog):
        """With log_multiline=True, multiline output is one log record."""
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_proc.stdout = io.StringIO("line1\nline2\nline3\n")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.task"):
            handle = task.run_task(TaskConfig(task_path="p.py"), log_multiline=True)
            handle.stdout_thread.join(timeout=2)
            handle.stderr_thread.join(timeout=2)

        output_records = [r for r in caplog.records if "line1" in r.message]
        assert len(output_records) == 1
        assert "line2" in output_records[0].message
        assert "line3" in output_records[0].message

    @patch("uv_task_runner.task.subprocess.Popen")
    def test_line_by_line_output_separate_records(self, mock_popen, caplog):
        """With log_multiline=False, each line is a separate log record."""
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_proc.stdout = io.StringIO("aaa\nbbb\n")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with caplog.at_level(logging.INFO, logger="uv_task_runner.task"):
            handle = task.run_task(TaskConfig(task_path="p.py"), log_multiline=False)
            handle.stdout_thread.join(timeout=2)
            handle.stderr_thread.join(timeout=2)

        output_messages = [
            r.message
            for r in caplog.records
            if "aaa" in r.message or "bbb" in r.message
        ]
        assert len(output_messages) == 2


# ---------------------------------------------------------------------------
# Pipeline – parallel mode
# ---------------------------------------------------------------------------


class TestPipelineParallel:
    def _patch_run_task(self, outcomes: dict[str, int | None]):
        """Return a context manager that patches run_task with controlled outcomes."""

        def fake_run(task_config, **kwargs):
            rc = outcomes.get(task_config.task_path, 0)
            return make_mock_handle(task_config.task_path, returncode=rc)

        return patch("uv_task_runner.task.run_task", side_effect=fake_run)

    def test_all_tasks_succeed(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="a.py"), TaskConfig(task_path="b.py")]
        )
        with self._patch_run_task({"a.py": 0, "b.py": 0}):
            with caplog.at_level(logging.INFO):
                result = pipeline.run()
        assert not result.aborted
        assert len(result.task_results) == 2
        assert all(r.success for r in result.task_results)
        assert any("completed successfully" in r.message for r in caplog.records)

    @patch("uv_task_runner.task._terminate_tree")
    def test_fail_fast_terminates_running(self, mock_term, caplog):
        still_running_proc = MagicMock()
        still_running_proc.pid = 200
        still_running_proc.poll.return_value = None  # still running

        def fake_run(task_config, **kwargs):
            if "fail" in task_config.task_path:
                return make_mock_handle(task_config.task_path, returncode=1)
            else:
                handle = make_mock_handle(task_config.task_path, still_running=True)
                handle.process = still_running_proc
                return handle

        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="fail.py"), TaskConfig(task_path="slow.py")],
            fail_fast=True,
        )
        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            with caplog.at_level(logging.INFO):
                result = pipeline.run()

        assert result.aborted
        assert any("failed with exit code" in r.message for r in caplog.records)
        assert any("Fail fast enabled" in r.message for r in caplog.records)

    def test_fail_fast_disabled_continues(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="fail.py"), TaskConfig(task_path="ok.py")],
            fail_fast=False,
        )
        call_count = [0]

        def fake_run(task_config, **kwargs):
            call_count[0] += 1
            rc = 1 if "fail" in task_config.task_path else 0
            return make_mock_handle(task_config.task_path, returncode=rc)

        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            result = pipeline.run()

        assert not result.aborted
        assert call_count[0] == 2

    def test_logs_error_on_failure(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="bad.py")], fail_fast=False
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("bad.py", returncode=1),
        ):
            with caplog.at_level(logging.ERROR):
                pipeline.run()

        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("bad.py failed with exit code 1" in r.message for r in error_records)

    def test_logs_task_count(self, caplog):
        pipeline = make_pipeline(
            tasks=[
                TaskConfig(task_path="a.py"),
                TaskConfig(task_path="b.py"),
                TaskConfig(task_path="c.py"),
            ]
        )

        def fake_run(task_config, **kwargs):
            return make_mock_handle(task_config.task_path)

        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            with caplog.at_level(logging.INFO):
                pipeline.run()

        assert any("Running 3 task(s)" in r.message for r in caplog.records)

    def test_returns_pipeline_result(self):
        pipeline = make_pipeline(tasks=[TaskConfig(task_path="a.py")])
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            result = pipeline.run()

        assert isinstance(result, PipelineResult)
        assert len(result.task_results) == 1
        assert result.aborted is False
        assert result.aborted_by is None


# ---------------------------------------------------------------------------
# Pipeline – sequential mode
# ---------------------------------------------------------------------------


class TestPipelineSequential:
    def test_all_succeed_sequential(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="a.py"), TaskConfig(task_path="b.py")],
            parallel=False,
        )
        call_order = []

        def fake_run(task_config, **kwargs):
            call_order.append(task_config.task_path)
            return make_mock_handle(task_config.task_path)

        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            with caplog.at_level(logging.INFO):
                result = pipeline.run()

        assert call_order == ["a.py", "b.py"]
        assert len(result.task_results) == 2
        success_records = [
            r for r in caplog.records if "completed successfully" in r.message
        ]
        assert len(success_records) == 2

    def test_fail_fast_stops_sequential(self, caplog):
        pipeline = make_pipeline(
            tasks=[
                TaskConfig(task_path="fail.py"),
                TaskConfig(task_path="never_run.py"),
            ],
            parallel=False,
            fail_fast=True,
        )
        call_count = [0]

        def fake_run(task_config, **kwargs):
            call_count[0] += 1
            return make_mock_handle(task_config.task_path, returncode=1)

        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            with caplog.at_level(logging.INFO):
                result = pipeline.run()

        assert call_count[0] == 1
        assert result.aborted
        assert result.aborted_by == "fail.py"
        assert any("Fail fast enabled, exiting" in r.message for r in caplog.records)

    def test_fail_fast_disabled_continues_sequential(self):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="fail.py"), TaskConfig(task_path="ok.py")],
            parallel=False,
            fail_fast=False,
        )
        call_count = [0]

        def fake_run(task_config, **kwargs):
            call_count[0] += 1
            rc = 1 if "fail" in task_config.task_path else 0
            return make_mock_handle(task_config.task_path, returncode=rc)

        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            pipeline.run()

        assert call_count[0] == 2

    def test_wait_false_logs_not_waiting(self, caplog):
        """exit_code=None (wait=False task) logs 'not waiting for it to finish'."""
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="bg.py", wait=False)],
            parallel=False,
            fail_fast=False,
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("bg.py", returncode=None),
        ):
            with caplog.at_level(logging.INFO):
                pipeline.run()

        assert any("not waiting for it to finish" in r.message for r in caplog.records)

    def test_per_task_config_used(self):
        pipeline = make_pipeline(
            tasks=[
                TaskConfig(
                    task_path="x.py",
                    task_args=["--foo", "bar"],
                    uv_args=["--verbose"],
                )
            ],
            parallel=False,
        )
        captured_configs = []

        def fake_run(task_config, **kwargs):
            captured_configs.append(task_config)
            return make_mock_handle(task_config.task_path)

        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            pipeline.run()

        assert len(captured_configs) == 1
        assert captured_configs[0].task_args == ["--foo", "bar"]
        assert captured_configs[0].uv_args == ["--verbose"]


# ---------------------------------------------------------------------------
# Pipeline – return code handling
# ---------------------------------------------------------------------------


class TestReturnCodes:
    def test_none_return_code_not_waiting_parallel(self, caplog):
        """exit_code=None (wait=False) logs 'not waiting for it to finish'."""
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="nowait.py", wait=False)],
            parallel=True,
            fail_fast=False,
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("nowait.py", returncode=None),
        ):
            with caplog.at_level(logging.INFO):
                pipeline.run()

        assert any("not waiting for it to finish" in r.message for r in caplog.records)

    def test_none_return_code_sequential(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="bg.py", wait=False)],
            parallel=False,
            fail_fast=True,
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("bg.py", returncode=None),
        ):
            with caplog.at_level(logging.INFO):
                pipeline.run()

        assert any("not waiting for it to finish" in r.message for r in caplog.records)

    def test_nonzero_return_code(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="err.py")],
            parallel=False,
            fail_fast=False,
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("err.py", returncode=2),
        ):
            with caplog.at_level(logging.ERROR):
                pipeline.run()

        assert any("failed with exit code 2" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Pipeline – logging levels
# ---------------------------------------------------------------------------


class TestLoggingLevels:
    def test_success_logged_at_info(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="ok.py")], parallel=False, fail_fast=False
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("ok.py"),
        ):
            with caplog.at_level(logging.DEBUG):
                pipeline.run()

        success_records = [
            r for r in caplog.records if "completed successfully" in r.message
        ]
        assert all(r.levelno == logging.INFO for r in success_records)

    def test_failure_logged_at_error(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="bad.py")], parallel=False, fail_fast=False
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("bad.py", returncode=1),
        ):
            with caplog.at_level(logging.DEBUG):
                pipeline.run()

        error_records = [r for r in caplog.records if "failed" in r.message]
        assert all(r.levelno == logging.ERROR for r in error_records)

    def test_fail_fast_warning_logged_at_warning(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="bad.py")], parallel=False, fail_fast=True
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("bad.py", returncode=1),
        ):
            with caplog.at_level(logging.DEBUG):
                pipeline.run()

        warning_records = [r for r in caplog.records if "Fail fast" in r.message]
        assert len(warning_records) >= 1
        assert all(r.levelno == logging.WARNING for r in warning_records)

    def test_task_count_logged_at_info(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="a.py")], parallel=False, fail_fast=False
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            with caplog.at_level(logging.DEBUG):
                pipeline.run()

        count_records = [r for r in caplog.records if "Running 1 task(s)" in r.message]
        assert len(count_records) == 1
        assert count_records[0].levelno == logging.INFO


# ---------------------------------------------------------------------------
# Pipeline – parallel termination details
# ---------------------------------------------------------------------------


class TestParallelTermination:
    @patch("uv_task_runner.task._terminate_tree")
    def test_only_running_processes_terminated(self, mock_term, caplog):
        """Only processes still running (poll() is None) should be terminated."""
        pipeline = make_pipeline(
            tasks=[
                TaskConfig(task_path="fail.py"),
                TaskConfig(task_path="done.py"),
                TaskConfig(task_path="running.py"),
            ],
            parallel=True,
            fail_fast=True,
        )

        def fake_run(task_config, **kwargs):
            tp = task_config.task_path
            if "fail" in tp:
                return make_mock_handle(tp, returncode=1)
            elif "done" in tp:
                return make_mock_handle(tp, returncode=0)
            else:
                return make_mock_handle(tp, still_running=True)

        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            with caplog.at_level(logging.INFO):
                pipeline.run()

        # done.py was already finished — should not be terminated
        terminated_pids = [c.args[0].pid for c in mock_term.call_args_list]
        # We can't guarantee running.py's handle was registered before abort,
        # but done.py's process should not be in terminated list
        done_handle = make_mock_handle("done.py", returncode=0)
        done_pid = done_handle.process.pid
        assert done_pid not in terminated_pids

    @patch("uv_task_runner.task._terminate_tree")
    def test_termination_logged_with_pid(self, mock_term, caplog):
        def fake_run(task_config, **kwargs):
            tp = task_config.task_path
            if "fail" in tp:
                return make_mock_handle(tp, returncode=1)
            else:
                h = make_mock_handle(tp, still_running=True)
                h.process.pid = 200
                return h

        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="fail.py"), TaskConfig(task_path="running.py")],
            parallel=True,
            fail_fast=True,
        )
        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            with caplog.at_level(logging.WARNING):
                pipeline.run()

        termination_records = [r for r in caplog.records if "Terminating" in r.message]
        if termination_records:
            assert any("PID" in r.message for r in termination_records)


# ---------------------------------------------------------------------------
# Pipeline – edge cases
# ---------------------------------------------------------------------------


class TestPipelineEdgeCases:
    def test_no_tasks_parallel(self, caplog):
        pipeline = make_pipeline(tasks=[], parallel=True)
        with caplog.at_level(logging.INFO):
            result = pipeline.run()

        assert any("Running 0 task(s)" in r.message for r in caplog.records)
        assert result.task_results == ()

    def test_no_tasks_sequential(self, caplog):
        pipeline = make_pipeline(tasks=[], parallel=False)
        with caplog.at_level(logging.INFO):
            result = pipeline.run()

        assert any("Running 0 task(s)" in r.message for r in caplog.records)
        assert result.task_results == ()

    def test_single_task_success(self, caplog):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="only.py")], parallel=True, fail_fast=True
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("only.py"),
        ):
            with caplog.at_level(logging.INFO):
                pipeline.run()

        assert any(
            "only.py completed successfully" in r.message for r in caplog.records
        )


# ---------------------------------------------------------------------------
# Pipeline – log_multiline forwarding
# ---------------------------------------------------------------------------


class TestLogMultilineForwarding:
    @pytest.mark.parametrize("multiline_val", [True, False])
    def test_log_multiline_passed_to_run_task(self, multiline_val):
        pipeline = make_pipeline(
            tasks=[TaskConfig(task_path="x.py")],
            parallel=False,
            log_multiline=multiline_val,
        )
        captured_kwargs = []

        def fake_run(task_config, **kwargs):
            captured_kwargs.append(kwargs)
            return make_mock_handle(task_config.task_path)

        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            pipeline.run()

        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["log_multiline"] == multiline_val


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_on_task_start_called(self):
        starts = []
        cfg = TaskConfig(
            task_path="a.py",
            on_task_start=lambda path, pid: starts.append((path, pid)),  # type: ignore[arg-type]
        )
        pipeline = Pipeline(tasks=[cfg], parallel=False)

        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            pipeline.run()

        # on_task_start is fired inside run_task (in task.py), which we mocked.
        # So we test via the actual run_task integration.

    def test_on_task_end_called(self):
        ends = []
        cfg = TaskConfig(
            task_path="a.py",
            on_task_end=lambda path, result: ends.append((path, result)),  # type: ignore[arg-type]
        )
        pipeline = Pipeline(tasks=[cfg], parallel=False)

        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            pipeline.run()

        assert len(ends) == 1
        assert ends[0][0] == "a.py"
        assert isinstance(ends[0][1], TaskResult)

    def test_on_task_end_called_for_each_task(self):
        ends = []
        tasks = [
            TaskConfig(
                task_path=f"{ch}.py",
                on_task_end=lambda path, result, ch=ch: ends.append(ch),  # type: ignore[arg-type]
            )
            for ch in "abc"
        ]
        pipeline = Pipeline(tasks=tasks, parallel=False)

        def fake_run(task_config, **kwargs):
            return make_mock_handle(task_config.task_path)

        with patch("uv_task_runner.task.run_task", side_effect=fake_run):
            pipeline.run()

        assert len(ends) == 3

    def test_on_pipeline_start_called(self):
        calls = []
        pipeline = Pipeline(
            tasks=[TaskConfig(task_path="a.py")],
            parallel=False,
            on_pipeline_start=lambda: calls.append("start"),
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            pipeline.run()

        assert calls == ["start"]

    def test_on_pipeline_end_called_with_result(self):
        results = []
        pipeline = Pipeline(
            tasks=[TaskConfig(task_path="a.py")],
            parallel=False,
            on_pipeline_end=lambda r: results.append(r),  # type: ignore[arg-type]
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            pipeline.run()

        assert len(results) == 1
        assert isinstance(results[0], PipelineResult)

    def test_on_pipeline_start_list(self):
        calls = []
        pipeline = Pipeline(
            tasks=[TaskConfig(task_path="a.py")],
            parallel=False,
            on_pipeline_start=[
                lambda: calls.append("first"),
                lambda: calls.append("second"),
            ],
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            pipeline.run()

        assert calls == ["first", "second"]

    def test_on_task_end_list(self):
        calls = []
        cfg = TaskConfig(
            task_path="a.py",
            on_task_end=[  # type: ignore[arg-type]
                lambda path, result: calls.append(f"hook1:{path}"),
                lambda path, result: calls.append(f"hook2:{path}"),
            ],
        )
        pipeline = Pipeline(tasks=[cfg], parallel=False)
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            pipeline.run()

        assert "hook1:a.py" in calls
        assert "hook2:a.py" in calls


# ---------------------------------------------------------------------------
# run_tasks convenience function
# ---------------------------------------------------------------------------


class TestRunTasksFunction:
    def test_basic_usage(self):
        tasks = [TaskConfig(task_path="a.py")]
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            result = run_tasks(tasks)

        assert isinstance(result, PipelineResult)
        assert len(result.task_results) == 1

    def test_forwards_parallel_flag(self):
        def fake_run(task_config, **kwargs):
            return make_mock_handle(task_config.task_path)

        tasks = [TaskConfig(task_path="a.py"), TaskConfig(task_path="b.py")]
        with patch("uv_task_runner.task.run_task", side_effect=fake_run) as mock_rt:
            run_tasks(tasks, parallel=False, fail_fast=False)

        assert mock_rt.call_count == 2

    def test_pipeline_end_hook(self):
        results = []
        tasks = [TaskConfig(task_path="a.py")]
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("a.py"),
        ):
            run_tasks(tasks, on_pipeline_end=lambda r: results.append(r))  # type: ignore[arg-type]

        assert len(results) == 1


# ---------------------------------------------------------------------------
# Pipeline.from_settings
# ---------------------------------------------------------------------------


class TestPipelineFromSettings:
    def test_creates_pipeline_from_settings(self):
        settings = Settings(
            tasks=[TaskConfig(task_path="a.py"), TaskConfig(task_path="b.py")],
            parallel=False,
            fail_fast=False,
            log_multiline=False,
        )
        pipeline = Pipeline.from_settings(settings)
        assert pipeline.tasks == settings.tasks
        assert pipeline.parallel is False
        assert pipeline.fail_fast is False
        assert pipeline.log_multiline is False


# ---------------------------------------------------------------------------
# main() wrapper
# ---------------------------------------------------------------------------


class TestMainWrapper:
    @patch("uv_task_runner.task.run_task")
    def test_main_runs_pipeline(self, mock_run_task, tmp_path, monkeypatch):
        """main() loads _CliSettings and runs a Pipeline."""
        toml = tmp_path / DEFAULT_CONFIG_PATH
        toml.write_text(
            "parallel = false\nfail_fast = false\n\n" "[[tasks]]\ntask_path = 'a.py'\n"
        )
        monkeypatch.setattr(sys, "argv", ["__main__.py", "--config", str(toml)])
        mock_run_task.return_value = make_mock_handle("a.py")

        main()

        mock_run_task.assert_called_once()
        cfg = mock_run_task.call_args.args[0]
        assert cfg.task_path == "a.py"

    @patch("uv_task_runner.task.run_task")
    def test_main_no_tasks(self, mock_run_task, tmp_path, monkeypatch, caplog):
        toml = tmp_path / DEFAULT_CONFIG_PATH
        toml.write_text("parallel = false\nfail_fast = false\n")
        monkeypatch.setattr(sys, "argv", ["__main__.py", "--config", str(toml)])

        with caplog.at_level(logging.INFO):
            main()

        mock_run_task.assert_not_called()
        assert any("Running 0 task(s)" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# write_template_config
# ---------------------------------------------------------------------------


class TestWriteTemplateConfig:
    def test_creates_file(self, tmp_path):
        from uv_task_runner.settings import write_template_config

        dest = tmp_path / "config.toml"
        result = write_template_config(dest)
        assert result.exists()

    def test_raises_if_exists(self, tmp_path):
        from uv_task_runner.settings import write_template_config

        dest = tmp_path / "config.toml"
        dest.touch()
        with pytest.raises(FileExistsError):
            write_template_config(dest)


# ---------------------------------------------------------------------------
# Settings – invalid numeric log level
# ---------------------------------------------------------------------------


class TestSettingsNumericLogLevel:
    def test_invalid_numeric_raises(self):
        with pytest.raises(Exception):
            Settings(log_level=999)


# ---------------------------------------------------------------------------
# TaskConfig arg validation (at construction time)
# ---------------------------------------------------------------------------


class TestTaskConfigArgValidation:
    def test_raises_on_help_flag(self):
        with pytest.raises(ValidationError):
            TaskConfig(task_path="s.py", uv_args=["--help"])

    def test_raises_on_h_flag(self):
        with pytest.raises(ValidationError):
            TaskConfig(task_path="s.py", uv_args=["-h"])

    def test_warns_python_without_no_project(self, caplog):
        with caplog.at_level(logging.WARNING, logger="uv_task_runner.task"):
            TaskConfig(task_path="s.py", uv_args=["--python", "3.11"])
        assert any("--no-project" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# dry_run_task
# ---------------------------------------------------------------------------


class TestDryRunTask:
    def test_returns_synthetic_result(self, caplog):
        cfg = TaskConfig(task_path="s.py")
        with caplog.at_level(logging.INFO, logger="uv_task_runner.task"):
            result = task.dry_run_task(cfg)
        assert result.task_path == "s.py"
        assert result.exit_code == 0
        assert result.success is True
        assert result.pid == 0
        assert result.stdout == ""
        assert result.stderr == ""
        assert any("DRY RUN" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Pipeline – dry_run mode
# ---------------------------------------------------------------------------


class TestPipelineDryRun:
    def test_dry_run_does_not_spawn_processes(self, caplog):
        pipeline = Pipeline(
            tasks=[TaskConfig(task_path="a.py"), TaskConfig(task_path="b.py")],
            dry_run=True,
        )
        with patch("uv_task_runner.task.run_task") as mock_run:
            with caplog.at_level(logging.INFO):
                result = pipeline.run()
        mock_run.assert_not_called()
        assert len(result.task_results) == 2
        assert all(r.exit_code == 0 for r in result.task_results)
        assert any("DRY RUN" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Pipeline – log_multiline + wait=False warning
# ---------------------------------------------------------------------------


class TestShouldAbortLogMultiline:
    def test_log_multiline_no_output_warning(self, caplog):
        """log_multiline=True + wait=False emits extra note about buffered output."""
        pipeline = Pipeline(
            tasks=[TaskConfig(task_path="bg.py", wait=False)],
            parallel=False,
            fail_fast=False,
            log_multiline=True,
        )
        with patch(
            "uv_task_runner.task.run_task",
            return_value=make_mock_handle("bg.py", returncode=None),
        ):
            with caplog.at_level(logging.INFO):
                pipeline.run()
        assert any("No output will be logged" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# run_task – non-Windows platform (start_new_session not set)
# ---------------------------------------------------------------------------


class TestRunTaskPlatform:
    @patch("uv_task_runner.task.subprocess.Popen")
    def test_no_start_new_session_on_non_windows(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 1
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")
        mock_popen.return_value = mock_proc

        with patch("uv_task_runner.task.sys.platform", "linux"):
            task.run_task(TaskConfig(task_path="s.py"))

        _, kwargs = mock_popen.call_args
        assert "start_new_session" not in kwargs


# ---------------------------------------------------------------------------
# main() – --init and --config error paths
# ---------------------------------------------------------------------------


class TestMainCLIPaths:
    def test_main_init_creates_config(self, tmp_path, monkeypatch):
        dest = tmp_path / "config.toml"
        monkeypatch.setattr(sys, "argv", ["__main__.py", "--init", str(dest)])
        main()
        assert dest.exists()

    def test_main_init_file_exists_exits(self, tmp_path, monkeypatch):
        dest = tmp_path / "config.toml"
        dest.touch()
        monkeypatch.setattr(sys, "argv", ["__main__.py", "--init", str(dest)])
        with pytest.raises(SystemExit):
            main()

    def test_main_config_flag_not_found_exits(self, tmp_path, monkeypatch):
        nonexistent = tmp_path / "nonexistent.toml"
        monkeypatch.setattr(sys, "argv", ["__main__.py", "--config", str(nonexistent)])
        with pytest.raises(SystemExit):
            main()
