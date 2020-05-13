import unittest
from pathlib import Path

from rsyncwrap.main import Line, Stats, TransferStats, rsync_available


class TestLine(unittest.TestCase):
    def test_init_raises(self):
        tests = [
            (
                "no line ending here!",
                ValueError,
                r"Please provide `raw_line` including the line ending\.",
            ),
        ]

        for line, exc, regex in tests:
            with self.subTest(line=line, exc=exc, regex=regex):
                with self.assertRaisesRegex(exc, regex):
                    Line(line, Path())

    def test_is_stats(self):
        tests = [
            ("    600,417,190 100%  100.56MB/s    0:00:05\r", True),
            ("this.isn't.correct\r", False),
            ("600,417,190 100%  100.56MB/s    nope\r", False),
            ("600,417,190 100%  100.56MB    0:00:05\r", False),
            ("600,417,190 100%  100.56MB    0:00:05\r", False),
            ("600,417,190 haha  100.56MB/s    0:00:05\r", False),
            ("nope 100%  100.56MB/s    0:00:05\r", False),
            ("/some/path/from/rsync\n", False),
            ("sending incremental file list\n", False),
            (
                "     264,000,000,000,000 100%   68.57MB/s   10:11:49 (xfr#1545, "
                "to-chk=0/1659)\n",
                True,
            ),
        ]

        for line, expected in tests:
            line = Line(line, Path("."))
            with self.subTest(line=line):
                if expected:
                    self.assertTrue(line.is_stats_line)
                else:
                    self.assertFalse(line.is_stats_line)

    def test_is_completed_stats(self):
        tests = [
            (
                "  4,260,869,539 100%   95.23MB/s    0:00:42 (xfr#1, "
                "ir-chk=1045/1063)\n",
                True,
            ),
            ("    600,417,190 100%  100.56MB/s    0:00:05 (xfr#1, to-chk=0/2)\n", True),
        ]
        for line, expected in tests:
            line = Line(line, Path())
            with self.subTest(line=line):
                if expected:
                    self.assertTrue(line.is_completed_stats_line)
                else:
                    self.assertFalse(line.is_completed_stats_line)

    def test_is_source_root(self):
        tests = [
            ("    your_mom/\n", Path("/home/your_mom"), True),
            ("    your_mom/has/dirs\n", Path("/home/your_mom"), False),
            (
                "    600,417,190 100%  100.56MB/s    0:00:05 (xfr#1, to-chk=0/2)\n",
                Path("/"),
                False,
            ),
        ]

        for line, source_path, expected in tests:
            with self.subTest(line=line, source_path=source_path):
                if expected:
                    self.assertTrue(Line(line, source_path).is_source_root)
                else:
                    self.assertFalse(Line(line, source_path).is_source_root)

    def test_as_path(self):
        tests = [
            (
                "/has/dirs/some_big_file.iso    \n",
                Path("/home/your_mom"),
                Path("/home/your_mom/has/dirs/some_big_file.iso"),
            ),
            (
                "    600,417,190 100%  100.56MB/s    0:00:05 (xfr#1, to-chk=0/2)\n",
                Path("/home/your_mom"),
                None,
            ),
            ("sending incremental file list\n", Path("/home/your_mom"), None),
            ("\n", Path("/home/your_mom"), None),
        ]

        for line, source_path, expected in tests:
            with self.subTest(line=line, source_path=source_path):
                self.assertEqual(
                    Line(line, source_path).as_path, expected,
                )

    def test_is_irrelevant(self):
        tests = [
            ("sending incremental file list\n", True),
            ("\n", True),
            (
                "    600,417,190 100%  100.56MB/s    0:00:05 (xfr#1, to-chk=0/2)\n",
                False,
            ),
        ]
        for line, expected in tests:
            with self.subTest(line=line):
                line = Line(line, Path())

                if expected:
                    self.assertTrue(line.is_irrelevant)
                else:
                    self.assertFalse(line.is_irrelevant)


class StatsTestCase(unittest.TestCase):
    def test_get_stats(self):
        # We start out without a previous stats.  Durr.
        previous_stats = None

        # We update stats with our first line which should be something like "sending
        # incremental file list"

        first_stats_update = Stats.get_stats_data(
            previous_stats=previous_stats,
            transferring_path=None,  # Not yet transferring anything
            in_progress_stats=None,  # Nothing in progress
            last_completed_path=None,  # Haven't completed anything
            last_completed_path_stats=None,  # ...so no stats from last completed
            total_transferred=0,  # surprise, nothing transferred yet
            raw_output="sending incremental file list\n",
        )

        expected_first_stats_update = Stats(
            in_progress_stats=None,
            transferring_path=None,
            last_completed_path=None,
            completed_paths={},
            last_completed_path_stats=None,
            total_transferred=0,
            raw_output=["sending incremental file list\n"],
        )

        self.assertEqual(
            first_stats_update, expected_first_stats_update,
        )

        # ok, now we'll get info about the path that's going to transfer next
        the_path = "/the_source"
        first_path_stats_update = Stats.get_stats_data(
            previous_stats=expected_first_stats_update,
            transferring_path=Path(the_path),
            in_progress_stats=None,
            last_completed_path=None,
            last_completed_path_stats=None,
            total_transferred=0,
            raw_output=the_path + "\n",
        )

        expected_first_path_stats_update = Stats(
            in_progress_stats=None,  # not an in-progress line
            # this line is indicating the path
            # about to be transferred
            transferring_path=Path(the_path),
            last_completed_path=None,  # no paths transferred yet
            completed_paths={},  # no paths transferred yet
            last_completed_path_stats=None,  # no paths transferred yet
            total_transferred=0,  # no paths transferred yet
            # previous lines plus current line
            raw_output=expected_first_stats_update.raw_output + [f"{the_path}\n"],
        )

        self.assertEqual(first_path_stats_update, expected_first_path_stats_update)

        # Now we're at our first in-progress-stats line
        # It looks  like this:
        #   "600,417,190 11%  100.56MB/s    0:00:05\r"

        transferred_for_line = 600_417_190
        first_transfer_stats = TransferStats(
            transferred_bytes=transferred_for_line,
            percent=11,
            time="0:00:05",
            transfer_rate=100.56,
            transfer_rate_unit="MB/s",
            is_completed_stats=False,
        )

        raw_first_line = "600,417,190 11%  100.56MB/s    0:00:05\r"
        first_progress_stats_update = Stats.get_stats_data(
            previous_stats=expected_first_path_stats_update,
            transferring_path=Path(the_path),
            in_progress_stats=first_transfer_stats,
            last_completed_path=None,  # still haven't completed a file yet.
            last_completed_path_stats=None,  # still haven't completed a file yet.
            # the total transferred is only what this line tells us since it's
            # the first stats line we've seen.
            total_transferred=transferred_for_line,
            raw_output=raw_first_line,
        )

        expected_first_progress_stats_update = Stats(
            in_progress_stats=first_transfer_stats,
            transferring_path=Path(the_path),
            last_completed_path=None,
            completed_paths={},
            last_completed_path_stats=None,
            total_transferred=transferred_for_line,
            raw_output=expected_first_path_stats_update.raw_output + [raw_first_line],
        )

        self.assertEqual(
            first_progress_stats_update, expected_first_progress_stats_update
        )


class MiscTestCase(unittest.TestCase):
    # this is stupid but it also makes me laugh
    @unittest.skipIf(not rsync_available(), "Requires rsync")
    def test_rsync_available(self):
        self.assertTrue(rsync_available())


class TransferStatsTestCase(unittest.TestCase):
    def test_transfer_stats_good(self):
        bytes_ = 600_417_190
        rate = 100.56
        stats_lines = [
            (
                "    600,417,190 100%  100.56MB/s    0:00:05\n",
                TransferStats(
                    **{
                        "transferred_bytes": bytes_,
                        "percent": 100,
                        "time": "0:00:05",
                        "transfer_rate": rate,
                        "transfer_rate_unit": "MB/s",
                        "is_completed_stats": False,
                    }
                ),
            ),
            (
                "    600,417,190 100%  100.56MB/s    0:00:05 (xfr#1, to-chk=0/2)\n",
                TransferStats(
                    **{
                        "transferred_bytes": bytes_,
                        "percent": 100,
                        "time": "0:00:05",
                        "transfer_rate": rate,
                        "transfer_rate_unit": "MB/s",
                        "is_completed_stats": True,
                    }
                ),
            ),
        ]

        for line, expected in stats_lines:
            with self.subTest(line=line):
                line = Line(line, Path())
                self.assertEqual(line.stats, expected)
                self.assertEqual(line.stats.transfer_rate_bytes, 105444803)
