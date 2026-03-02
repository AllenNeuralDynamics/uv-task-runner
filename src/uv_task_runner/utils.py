from __future__ import annotations

from typing import Any, Callable


def _call_hooks(
    hooks: Callable[..., None] | list[Callable[..., None]] | None,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Call a single hook or each hook in a list with the given inputs.
    No results are collected.

    Hooks are called synchronously and block the caller until they return.
    Keep hooks fast. If a hook needs to do slow I/O (e.g. network requests),
    spawn a background thread inside the hook itself.

    To pass extra context to a hook, use a closure or functools.partial:

        def on_start(task_path: str, pid: int) -> None:
            my_client.notify(task_path, pid, env=env)

        TaskConfig(task_path="...", on_task_start=on_start)
    """
    if hooks is None:
        return
    for hook in [hooks] if callable(hooks) else hooks:
        hook(*args, **kwargs)  # type: ignore[call-top-callable]
