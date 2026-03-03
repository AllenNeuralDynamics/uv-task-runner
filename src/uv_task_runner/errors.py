"""Custom exception types for uv-task-runner."""


class ConfigError(Exception):
    """Raised for invalid configuration: bad TOML, missing fields, invalid settings values."""


class TaskError(Exception):
    """Raised for task infrastructure failures: uv not found, script path missing, etc."""
