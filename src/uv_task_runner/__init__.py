"""A simple utility to run multiple Python scripts sequentially or in parallel, with isolated environments, monitoring and error handling."""

from uv_task_runner import errors, pipeline, settings, task

ConfigError = errors.ConfigError
TaskError = errors.TaskError
OnPipelineEnd = pipeline.OnPipelineEnd
OnPipelineStart = pipeline.OnPipelineStart
Pipeline = pipeline.Pipeline
PipelineResult = pipeline.PipelineResult
run_tasks = pipeline.run_tasks
OnTaskEnd = task.OnTaskEnd
OnTaskStart = task.OnTaskStart
Settings = settings.Settings
TaskConfig = task.TaskConfig
TaskResult = task.TaskResult

__all__ = [
    "ConfigError",
    "TaskError",
    "OnPipelineEnd",
    "OnPipelineStart",
    "OnTaskEnd",
    "OnTaskStart",
    "Pipeline",
    "PipelineResult",
    "run_tasks",
    "Settings",
    "TaskConfig",
    "TaskResult",
]
