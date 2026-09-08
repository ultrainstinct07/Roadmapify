"""Console entry point.

Wraps dispatch in the BrokenPipeError guard graphify learned to need: piping any
command into ``head`` otherwise prints a traceback on exit, which looks like a
crash in a tool whose whole job is to be trusted.
"""

from __future__ import annotations

import errno
import sys


def main() -> int:
    from roadmapify.cli import dispatch

    try:
        return dispatch(sys.argv[1:])
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    except BrokenPipeError:
        # Silence the interpreter's shutdown flush of a closed stdout.
        try:
            devnull = __import__("os").open(__import__("os").devnull,
                                            __import__("os").O_WRONLY)
            __import__("os").dup2(devnull, sys.stdout.fileno())
        except OSError:
            pass
        return 0
    except OSError as exc:
        if getattr(exc, "errno", None) in (errno.EPIPE, errno.EINVAL):
            return 0
        raise


if __name__ == "__main__":
    raise SystemExit(main())
