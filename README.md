# A library for using rsync from python.
* We call rsync with the `-a` option. This means it's recursive and preserves everything except
hard links.
* This early version only supports transferring the `source` directory *into* the destination
directory.  In other words, in the above example we'd end up with `/the_destination/the_source`.
* Of course, since this is rsync, we only transfer changed bytes and this means it's a resumable
process.
* In my testing rsync yields a stats update about once a second, so that's how often we yield
them to the caller.

# How to use
Perform a sync between local source and local
destination like this.
```python
from rsyncwrap import rsyncwrap

source = '/the_source'
dest = '/the_destination'

for update in rsyncwrap(source, dest):
    if isinstance(update, int):
        print("Exitcode:", update)
    else:
        print(update)
```

## Syncing from remote source
Remote sources over SSH are supported. The source MUST
be specified as `<user>@<system>:<path>`. Strings of any
other format, will be assumed to be a local path.
```python
from rsyncwrap import rsyncwrap

source = 'user@remote.system.org:/the_source')
dest = '/the_destination'

for update in rsyncwrap(source, dest):
    print(update)
```

## Custom SSH configuration
How to run with custom configuration of SSH. The SSH parameters
available are described in the [SSH documentation](https://linux.die.net/man/5/ssh_config).
The long form must be used as seen in the example below. The content of the
dictionary will be passed to rsync as `-e ssh -o <option>=<value>`.
```python
from rsyncwrap import rsyncwrap

source = 'user@remote.system.org:/the_source'
dest = '/the_destination'

ssh_config = {
    "Port": 22,
    "StrictHostKeyChecking": "no"
}
for update in rsyncwrap(source, dest, ssh_config=ssh_config):
    print(update)
```

# Statistics

`rsyncwrap` yields progress updates via `Stats` objects that have the following properties:

* `in_progress_stats`: An instance of `TransferStats` (see below for a description).
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

`TransferStats` objects look like this:

* `transferred_bytes`:  The total bytes transferred for the current line of rsync output.
* `percent`:  The percent complete for the current file being transferred.
* `time`:  Rsync's estimated time for the current file being transferred.
* `transfer_rate`:  Rsync's value for the current transfer in bytes.
* `transfer_rate_unit`: Rsync's outputted transfer rate unit.  e.g. "MB/s"
* `is_completed_stats`:  Is this the stats for a completed file transfer?
