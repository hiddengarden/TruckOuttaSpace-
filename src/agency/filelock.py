import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def locked(path: str | Path):
    """Exclusive advisory lock (fcntl.flock) on a sibling `<path>.lock` file,
    held for the duration of the `with` block. Guards read-modify-write
    cycles on shared JSON state files against concurrent writers -- a
    scheduled `run-all`/`loop` overlapping a manual `resume`/`draft` -- since
    without it, two processes racing a read-modify-write can silently drop
    one writer's update.
    """
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
