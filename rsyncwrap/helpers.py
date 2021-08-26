import re
from pathlib import Path
from typing import Sequence, Union, Tuple


class cached_property:
    """
    Decorator that converts a method with a single self argument into a
    property cached on the instance.

    Optional ``name`` argument allows you to make cached properties of other
    methods. (e.g.  url = cached_property(get_absolute_url, name='url') )
    """

    def __init__(self, func, name=None):
        self.func = func
        self.__doc__ = getattr(func, "__doc__")
        self.name = name or func.__name__

    def __get__(self, instance, cls=None):
        """
        Call the function and put the return value in instance.__dict__ so that
        subsequent attribute access on the instance returns the cached value
        instead of calling cached_property.__get__().
        """
        if instance is None:
            return self
        res = instance.__dict__[self.name] = self.func(instance)
        return res


def parts_to_path(parts: Sequence[str]) -> Path:
    """Convert sequence of strings into a Path"""
    path = Path()
    for part in parts:
        path /= part
    return path


def is_remote(location: str) -> bool:
    """
    Is location remote (<user>@<remote>:<path>)?
    """
    user, remote, path = parse_location(location)
    return bool(user and remote and path)


def parse_location(location: str) -> Tuple[Union[str, None], Union[str, None], Path]:
    """
    Parse a location and return user, remote & path.
    If location is local, user & remote are None.
    """
    # Remove ";" as a security meassure
    location = location.replace(";", "")
    try:
        # Matching "<user>@<remote>:<path>"
        p = re.compile(r"^(.+)@(.+):(.+)$")
        m = p.match(location)
        user, remote = m.group(1, 2)
        path = Path(m.group(3))
    except AttributeError:
        # Did not match all 3 groups. Expect a path
        user = None
        remote = None
        path = Path(location)

    assert path.is_absolute(), f"'{path}' is not an absolute path."

    return user, remote, path
