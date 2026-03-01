from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydantic_settings import BaseSettings
from pathlib import Path
import os
import signal
import subprocess
import sys
import logging

logger = logging.getLogger(__name__)


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Terminate a process and all its children."""
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
    
class Settings(BaseSettings):
    plugins_dir: Path = Path('./plugins').resolve()
    plugins_glob_pattern: str = '**/*.py*' # can run .py and .pyw files
    quiet_install: bool = True
    parallel: bool = False # or run in series
    fail_fast: bool = True
    wait: bool = True
    
def run_plugin(
    plugin_path: Path,
    plugin_args: list[str],
    uv_run_args: list[str] | None = None,
    popen_kwargs: dict[str, Any] | None = None
):
    logger.info(f'Running {plugin_path.name}')
    args = ['uv', 'run', '--script', plugin_path.as_posix()] + (uv_run_args or []) + plugin_args
    kwargs = popen_kwargs or {}
    if sys.platform != 'win32':
        kwargs.setdefault('start_new_session', True)
    process = subprocess.Popen(args, **kwargs)
    return process

def main():
    settings = Settings()
    logger.info(f'Loading plugins from: {settings.plugins_dir}')
    plugin_paths = list(settings.plugins_dir.glob(settings.plugins_glob_pattern))
    for p in plugin_paths:
        if not p.suffix in ['.py', '.pyw']:
            raise ValueError(f"Invalid plugin file type: {p}. Only .py and .pyw files are allowed.")
    logger.info(f'Found {len(plugin_paths)} plugin(s).')
    
    popen_kwargs = {}
    uv_run_args = ['--quiet'] if settings.quiet_install else []
    plugin_args: list[str] = []
    pluging_path_to_procs: dict[Path, subprocess.Popen] = {}
    def run_and_wait(plugin_path: Path, wait: bool) -> tuple[Path, int]:
        process = run_plugin(plugin_path, plugin_args=plugin_args, uv_run_args=uv_run_args, popen_kwargs=popen_kwargs)
        pluging_path_to_procs[plugin_path] = process
        if wait:
            process.wait()
        return plugin_path, process.returncode
    
    if settings.parallel:
        executor = ThreadPoolExecutor()
        futures = {executor.submit(run_and_wait, p, settings.wait): p for p in plugin_paths}
        for future in as_completed(futures):
            plugin_path, rc = future.result()
            if rc != 0:
                logger.info(f'{plugin_path.name} failed with return code {rc}')
                if settings.fail_fast:
                    logger.info('Fail fast enabled: terminating any plugins still running.')
                    for plugin_path, proc in pluging_path_to_procs.items():
                        if proc.poll() is None:  # process is still running
                            logger.info(f'Terminating {plugin_path.name} with PID {proc.pid}')
                            _terminate_tree(proc)
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
            else:
                logger.info(f'{plugin_path.name} completed successfully.')
        else:
            executor.shutdown(wait=True)
    else:
        for plugin_path in plugin_paths:
            plugin_path, rc = run_and_wait(plugin_path, wait=settings.wait)
            if rc != 0:
                logger.info(f'{plugin_path.name} failed with return code {rc}')
                if settings.fail_fast:
                    logger.info('Fail fast enabled, exiting.')
                    break
            else:
                logger.info(f'{plugin_path.name} completed successfully.')
        
        
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
    main()