from contextlib import contextmanager
from pathlib import Path


@contextmanager
def get_logs_content(logger_path: Path):
    with open(logger_path, "r+") as logger_handle:
        logs = logger_handle.read()
        yield logs
        logger_handle.truncate(0)
