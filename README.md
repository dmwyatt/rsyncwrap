# A library for using rsync from python.

```python
from pathlib import Path

from rsyncwrap import rsyncwrap

source = Path('/the_source')
dest = Path('/the_destination')

for update in rsyncwrap(source, dest):
    print(update)
```

`rsyncwrap` yields progress updates via `Stats` objects that have the following properties:

* `in_progress_stats`: An instance of `TransferStats`.
* `transferring_path`: An instance of `pathlib.Path` that indicates the path that is currently
transferring.
* `last_completed_path`: An instance of `pathlib.Path` that indicates the last path to finish
transferring.
* `completed_paths`: A dictionary with `pathlib.Path` keys and `TransferStats` objects as values.
  The value for each key is the stats for that completed transfer.
* `last_completed_path_stats`: An instance of `TransferStats` for the last *completed* transfer.
* `total_transferred`: The total bytes transferred so far.  Does not include bytes already at the
 destination when the transfer started.
* `raw_output`: A list of lines we got from the underlying rsync command.  Useful for debugging
during development.


Notes about how we use rsync:

* We call rsync with the `-a` option. This means it's recursive and preserves everything except
hard links.
* This early version only supports transferring the `source` directory *into* the destination
directory.  In other words, in the above example we'd end up with `/the_destination/the_source`.
* Of course, since this is rsync, we only transfer changed bytes and this means it's a resumable
process.
* In my testing rsync yields a stats update about once a second, so that's how often we yield
them to the caller.
