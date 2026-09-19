"""``python -m harness`` entry point (equivalent to the ``cdh`` script)."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
