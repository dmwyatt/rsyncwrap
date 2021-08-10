import atexit
import decimal
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Union

from rsyncwrap.helpers import is_remote, parse_location, parts_to_path

try:
    from functools import cached_property
except ImportError:
    from rsyncwrap.helpers import cached_property


def _rsync(
    source_locations: Sequence[str], dest_location: str, ssh_config: Dict
) -> Iterator[Union[str, int]]:
    """
    Runs rsync and yields lines of output.
    """
    # If remote is local, make sure dir exists
    dest_user, dest_remote, dest_path = parse_location(dest_location)
    if not is_remote(dest_location):
        assert dest_path.is_dir(), f"{dest_path} is not a directory."

    # Make sure local sources must exist
    for l in source_locations:
        if not is_remote(l):
            path = parse_location(l)[-1]
            assert path.exists(), f"Local source '{path}' does not exist."

    # Build list of SSH config
    ssh = [f"{o[0]} {o[1]}" for o in ssh_config]

    # Build command line. Start with basic rsync
    cmd = ["rsync", "--archive", "--progress"]
    # Add SSH specific config
    if ssh:
        # TODO: Remove ";" for security
        ssh.insert(0, "-e ssh")
        cmd.append(" ".join(ssh))
    # Add sources
    cmd.append(*[str(p) for p in source_locations])
    # Add destination
    cmd.append(str(dest_path))

    # print(cmd)
    # exit(0)
    cp = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf8"
    )

    def cleanup():
        """Helper to ensure that the rsync process gets killed at end of script.

        Very occasionally, the rsync subprocess seems to keep running if we're killed
        by Ctrl-C.
        """
        timeout_seconds = 5
        waiting = 0
        for second in range(5):
            if cp.poll() is None:
                time.sleep(1)
                waiting += 1
            if waiting >= timeout_seconds:
                cp.kill()

    atexit.register(cleanup)

    # Popen doesn't allow us to send `newline` to the open call for stdout/stderr,
    # so here we call the `reconfigure` method on the stream's TextIOWrapper.
    cp.stdout.reconfigure(newline="")

    char_buffer = ""
    while True:
        char = cp.stdout.read(1)

        if not char and cp.poll() is not None:
            break
        if char:
            char_buffer += char
            if char == "\r" or char == "\n":
                yield char_buffer
                char_buffer = ""
        sys.stdout.flush()
    yield cp.returncode


def rsync_available():
    cmd = ["rsync", "--version"]
    cp = subprocess.run(cmd, capture_output=True)
    if cp.returncode or cp.stderr:
        return False
    match = re.search(r"version\s+(\d+\.\d+\.\d+)", cp.stdout.decode("utf8")).groups()

    return bool(match)


def get_finish_stat_from_completed_stat_line(line: str) -> str:
    """
    A completed stats line looks like:

        600,417,190 100%  100.56MB/s    0:00:05 (xfr#1, to-chk=0/2)

    We only want this part:

        600,417,190 100%  100.56MB/s    0:00:05

    So that's what this function does!

    If you pass in an in-progress stats line you'll just get it back since it
    already looks like we want it to look.
    """
    return line.split(" (")[0].strip()


@dataclass
class TransferStats:
    """
    Represent the transfer stats at a single point in time.
    """

    transferred_bytes: int
    percent: int
    time: str
    transfer_rate: float
    transfer_rate_unit: str
    is_completed_stats: bool

    @cached_property
    def transfer_rate_bytes(self) -> int:
        if self.transfer_rate_unit != "MB/s":
            raise ValueError("Transfer rate unit must be 'MB/s'.")

        return int(
            Decimal(self.transfer_rate * 1048576).quantize(
                Decimal("1"), rounding=decimal.ROUND_HALF_UP
            )
        )


_transfer_line_cache = {}


@dataclass
class Line:
    raw_line: str
    source_path: Path

    def __post_init__(self):
        if self.raw_line[-1] not in ("\n", "\r"):
            raise ValueError("Please provide `raw_line` including the line ending.")

    @cached_property
    def is_irrelevant(self) -> bool:
        """Tells us if the line is something we can use.

        Some lines of rsync output are of no use to us.  Either they're blank or
        they're info we don't do anything with yet.
        """
        if self.raw_line.strip().casefold() == "sending incremental file list":
            # TODO: Maybe we should include this line in our stats output so client
            #  programs can use it to display a spinner or something?
            return True
        if not self.raw_line.strip():
            return True

    @cached_property
    def as_path(self) -> Optional[Path]:
        """Get a line as path relative to the source path."""
        if not self.raw_line.endswith("\n") or self.is_stats_line or self.is_irrelevant:
            return None
        line_path = Path(self.raw_line.strip())

        # rsync prints a leading slash so we have to remove that.
        return self.source_path / parts_to_path(line_path.parts[1:])

    @cached_property
    def is_path(self) -> bool:
        """Tells us if the line is a path that exists."""
        # Does not exist if on a remote. If it's reported by rsync
        # don't we know that it exists? Do we need to run .exists()?
        # return self.as_path and self.as_path.exists()
        # Can we check if it is valid path syntax?
        return self.as_path

    @cached_property
    def is_source_root(self) -> bool:
        return (
            self.raw_line.endswith("\n")
            and self.raw_line.strip().rstrip("/") == self.source_path.stem
        )

    @staticmethod
    def _is_transfer_stats(data: str) -> bool:
        """
        Determines if the data is a transfer stats line.

        Returns True for lines that look like:

            600,417,190 100%  100.56MB/s    0:00:05 (xfr#1, to-chk=0/2)

        and:

            600,417,190 100%  100.56MB/s    0:00:05
        """
        if data in _transfer_line_cache:
            return _transfer_line_cache[data]
        else:
            # works with in-progress or completed stats lines
            line = data.strip().split("(")[0].split()
            result = True
            # TODO: Clean all of this up into a single regex...maybe?
            if len(line) != 4:
                result = False
            elif not re.match(r"^\d+:\d\d:\d\d$", line[3]):
                result = False
            elif not line[2].endswith("/s"):
                result = False
            elif not line[1].endswith("%"):
                result = False
            elif re.search(r"[^\d,]", line[0]):
                result = False

            _transfer_line_cache[data] = result
            return result

    @cached_property
    def is_completed_stats_line(self) -> bool:
        if not self.raw_line.endswith("\n"):
            return False

        if "xfr" not in self.raw_line:
            return False

        need_one_of = ["ir-chk", "to-chk"]
        if not any(check in self.raw_line for check in need_one_of):
            return False

        if not self._is_transfer_stats(self.raw_line):
            return False

        line = self.raw_line.split(" (")
        if not len(line) == 2:
            return False

        return True

    @cached_property
    def is_progress_stats_line(self) -> bool:
        return self.raw_line.endswith("\r") and self._is_transfer_stats(
            self.raw_line.strip()
        )

    @cached_property
    def is_stats_line(self) -> bool:
        """
        Determine if a string is a line of rsync stats info.

        A stats line starts with something like:

            600,417,190 100%  100.56MB/s    0:00:05
        """
        return self._is_transfer_stats(self.raw_line)

    @cached_property
    def stats(self) -> Optional[TransferStats]:
        if not self.is_stats_line:
            return None

        line = self.raw_line

        complete = False
        if self.is_completed_stats_line:
            line = line.split(" (")[0].strip()
            complete = True

        components = line.split()

        info = {
            "transferred_bytes": int(components[0].replace(",", "")),
            "percent": int(components[1][:-1]),
            "time": components[3],
            "is_completed_stats": complete,
        }

        transfer_rate, transfer_rate_unit = re.match(
            r"^([\d,]+\.\d+)([^\d]+)$", components[2]
        ).groups()

        info["transfer_rate"] = float(transfer_rate)
        info["transfer_rate_unit"] = transfer_rate_unit

        return TransferStats(**info)


@dataclass
class Stats:
    """
    A summary of rsync stats.
    """

    in_progress_stats: Union[None, TransferStats]
    transferring_path: Union[None, Path]
    last_completed_path: Union[None, Path]
    completed_paths: Dict[Path, TransferStats]
    last_completed_path_stats: Union[None, TransferStats]
    total_transferred: int
    raw_output: List[str] = field(default_factory=list)

    @staticmethod
    def get_stats_data(
        previous_stats: Union["Stats", None],
        transferring_path: Union[None, Path],
        in_progress_stats: Union[None, TransferStats],
        last_completed_path: Union[None, Path],
        last_completed_path_stats: Union[None, TransferStats],
        total_transferred: int,
        raw_output: Union[None, str],
    ) -> "Stats":
        """A builder of `Stats` instances."""
        completed_paths = getattr(previous_stats, "completed_paths", {})

        if raw_output is not None:
            raw_output = [raw_output]

            if previous_stats:
                raw_output = previous_stats.raw_output + raw_output
        else:
            raw_output = []

        if last_completed_path_stats:
            completed_paths[last_completed_path] = last_completed_path_stats

        return Stats(
            in_progress_stats=in_progress_stats,
            transferring_path=transferring_path,
            last_completed_path=last_completed_path,
            last_completed_path_stats=last_completed_path_stats,
            completed_paths=completed_paths,
            total_transferred=total_transferred,
            raw_output=raw_output,
        )


def rsyncwrap(
    source: str,
    dest: str,
    ssh_config: Dict = {},
    include_raw_output: bool = False,
) -> Iterator[Union[int, Stats]]:
    """
    Copy the directory "source" into the directory "dest".

    Serves as a wrapper around the rsync binary command.  Yields a succession of
    status updates with information about what has been transferred.

    Use like so:

    >>> for update in rsyncwrap(Path("/the_source_dir"), Path("/the/destination")):
    ...     print(update)

    This gives you a succession of stats updates while copying the source and ending
    up with the source located at "/the/destination/the_source_dir".

    :param source: The directory we want to copy
    :param dest: The directory we want to copy source into.
    :param include_raw_output: A debugging helper that includes the raw output text
        from rsync with the yielded stats.
    """
    # TODO: Support multiple source paths.
    # TODO: Get rsync summary information and return stats indicating total
    #  transferred. This will give us info about how much of the source was already
    #  at the destination.

    # Rsync does not support remote to remote syncing
    assert not (
        is_remote(source) and is_remote(dest)
    ), "Source and destination both remote."

    transferring_path: Union[None, Path] = None
    last_completed_path: Union[None, Path] = None
    last_completed_path_stats: Union[None, TransferStats] = None
    stats = None

    transferred_bytes = 0
    last_stats_update: Union[None, TransferStats] = None

    for line in _rsync([source], dest, ssh_config):
        # The _rsync callable returns the integer exit code as the last thing.
        if isinstance(line, int):
            yield line
            continue

        # line = Line(line, source)
        # Would it be nicer to add support for location parsing in the Line class?
        line = Line(line, parse_location(source)[-1])

        if line.is_stats_line:
            # Sanity check.  We shouldn't have a stats line until
            # we've previously had a line telling us which path is transferring.
            assert transferring_path, (
                "Have a progress stats line, but do "
                "not know currently transferring path."
            )

            # track how much has been transferred so far
            last_transferred = (
                last_stats_update.transferred_bytes if last_stats_update else 0
            )
            last_was_completed_line = (
                last_stats_update.is_completed_stats if last_stats_update else False
            )
            transferred_bytes = calculate_transferred(
                transferred_bytes,
                line.stats.transferred_bytes,
                last_transferred,
                last_was_completed_line,
            )
            # Each time we calculate how much has been transferred, we need to know
            # what the last line with transfer stats looked like.
            last_stats_update = line.stats

        if line.is_progress_stats_line:
            # We don't need to do anything here, but at the end of this if/else we
            # capture any cases we've forgotten to handle. If we don't have this
            # clause then valid progress lines will seem like lines we didn't handle.
            pass
        elif line.is_completed_stats_line:
            last_completed_path_stats = line.stats
            last_completed_path = transferring_path
        elif line.is_source_root:
            transferring_path = source
        elif line.is_path:
            transferring_path = line.as_path
        elif line.is_irrelevant:
            continue
        else:
            raise ValueError(f"Unexpected line in rsync output: {line}")

        stats = Stats.get_stats_data(
            previous_stats=stats,
            transferring_path=transferring_path,
            in_progress_stats=line.stats if line.is_progress_stats_line else None,
            last_completed_path=last_completed_path,
            last_completed_path_stats=last_completed_path_stats,
            total_transferred=transferred_bytes,
            raw_output=line.raw_line if include_raw_output else None,
        )
        yield stats


def calculate_transferred(
    total_transferred: int,
    current_transferred: int,
    last_transferred: int = 0,
    last_transferred_was_completed_line: Optional[bool] = False,
) -> int:
    if not last_transferred and not total_transferred:
        # This will be the case on our first stats line
        return current_transferred

    if last_transferred_was_completed_line:
        return total_transferred + current_transferred
    else:
        return total_transferred - last_transferred + current_transferred
