from __future__ import annotations

import concurrent.futures as cf
import logging
import sys
from dataclasses import dataclass
from typing import Protocol

from uv_task_runner import settings, task, utils

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    task_results: tuple[task.TaskResult, ...]
    aborted: bool
    aborted_by: str | None  # task_path that triggered fail_fast, or None


class OnPipelineStart(Protocol):
    def __call__(self) -> None: ...  # pragma: no cover


class OnPipelineEnd(Protocol):
    def __call__(self, pipeline_result: PipelineResult) -> None: ...  # pragma: no cover


@dataclass
class Pipeline:
    tasks: list[task.TaskConfig]
    parallel: bool = False
    fail_fast: bool = False
    log_multiline: bool = False
    dry_run: bool = False
    on_pipeline_start: OnPipelineStart | list[OnPipelineStart] | None = None
    on_pipeline_end: OnPipelineEnd | list[OnPipelineEnd] | None = None

    @classmethod
    def from_settings(cls, s: settings.Settings) -> Pipeline:
        return cls(
            tasks=s.tasks,
            parallel=s.parallel,
            fail_fast=s.fail_fast,
            log_multiline=s.log_multiline,
            dry_run=s.dry_run,
        )

    def run(self) -> PipelineResult:
        utils._call_hooks(self.on_pipeline_start)
        if self.dry_run:
            logger.info(f"DRY RUN: {len(self.tasks)} task(s) would run.")
        else:
            logger.info(f"Running {len(self.tasks)} task(s).")

        task_results: list[task.TaskResult] = []
        task_handles: dict[str, task._TaskHandle] = {}
        aborted = False
        aborted_by: str | None = None

        def _execute(task_config: task.TaskConfig) -> task.TaskResult:
            if self.dry_run:
                result = task.dry_run_task(task_config)
                utils._call_hooks(task_config.on_task_end, task_config.task_path, result)
                return result
            handle = task.run_task(task_config, log_multiline=self.log_multiline)
            task_handles[task_config.task_path] = handle
            result = task._collect_result(handle, wait=task_config.wait)
            utils._call_hooks(task_config.on_task_end, task_config.task_path, result)
            return result

        def _should_abort(result: task.TaskResult) -> bool:
            """Log task outcome. Return True if fail_fast should trigger."""
            if result.exit_code is None:
                msg = f"{result.task_path} is running: not waiting for it to finish."
                if self.log_multiline:
                    msg += " No output will be logged for this task, because log_multiline=true buffers until process exit."
                logger.info(msg)
                return False
            if result.exit_code != 0:
                logger.error(
                    f"{result.task_path} failed with exit code {result.exit_code}"
                )
                return self.fail_fast
            logger.info(f"{result.task_path} completed successfully.")
            return False

        if self.parallel:
            with cf.ThreadPoolExecutor() as executor:
                future_to_config = {
                    executor.submit(_execute, tc): tc for tc in self.tasks
                }
                for future in cf.as_completed(future_to_config):
                    result = future.result()
                    task_results.append(result)
                    if _should_abort(result):
                        aborted = True
                        aborted_by = result.task_path
                        logger.warning(
                            "Fail fast enabled: terminating any tasks still running."
                        )
                        for tp, handle in task_handles.items():
                            if handle.process.poll() is None:
                                logger.warning(
                                    f"Terminating {tp} with PID {handle.process.pid}"
                                )
                                task._terminate_tree(handle.process)
                        if sys.version_info >= (3, 9):
                            executor.shutdown(wait=False, cancel_futures=True)  # pragma: no cover
                        else:
                            logger.warning("Python <3.9: pending futures cannot be cancelled.")
                            executor.shutdown(wait=False)
                        break
        else:
            for task_config in self.tasks:
                result = _execute(task_config)
                task_results.append(result)
                if _should_abort(result):
                    aborted = True
                    aborted_by = result.task_path
                    logger.warning("Fail fast enabled, exiting.")
                    break

        # Warn about still-running background processes
        for tp, handle in task_handles.items():
            if handle.process.poll() is None:
                logger.warning(
                    f"{tp} with PID {handle.process.pid} is still running after main "
                    "process completed: subsequent messages from the task will not be "
                    "captured (Hint: set TaskConfig.wait=true to change this behavior)"
                )

        pipeline_result = PipelineResult(
            task_results=tuple(task_results),
            aborted=aborted,
            aborted_by=aborted_by,
        )
        utils._call_hooks(self.on_pipeline_end, pipeline_result)
        return pipeline_result


def run_tasks(
    tasks: list[task.TaskConfig],
    parallel: bool = False,
    fail_fast: bool = False,
    log_multiline: bool = False,
    dry_run: bool = False,
    on_pipeline_start: OnPipelineStart | list[OnPipelineStart] | None = None,
    on_pipeline_end: OnPipelineEnd | list[OnPipelineEnd] | None = None,
) -> PipelineResult:
    """Convenience wrapper: construct a Pipeline and run it."""
    return Pipeline(
        tasks=tasks,
        parallel=parallel,
        fail_fast=fail_fast,
        log_multiline=log_multiline,
        dry_run=dry_run,
        on_pipeline_start=on_pipeline_start,
        on_pipeline_end=on_pipeline_end,
    ).run()
