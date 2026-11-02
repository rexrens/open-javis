"""Tutorial chapter 10: hot module replacement (``Hmr``).

The :class:`~javis.cordis.loader.hmr.Hmr` service watches plugin modules and
the composition file with a simple polling watcher. On change:

- a plugin module file is saved → the entry is disposed and re-mounted with
  fresh code (its effects unwind, dependents reload);
- ``cordis.yml`` is saved → entries are diffed by id: removed entries are
  unmounted, changed ones re-mounted, new ones mounted.

The watcher task keeps the event loop busy, so run this chapter with
``--wait``. Then edit ``hot.py`` (e.g. change the message below), save, and
watch the entry reload without restarting the process.

API shown: ``Hmr`` (a ``Service`` whose ``apply = Hmr`` entry goes into the
composition) · the loader's entry diffing.
"""

from __future__ import annotations

import time

name = "hot-module"

#: Edit this message while the chapter is running to see HMR remount the entry.
MESSAGE = "Hello from the hot module (edit me to trigger a reload)"


def apply(ctx):
    del ctx
    print(f"[hot] {MESSAGE} @ {time.strftime('%H:%M:%S')}")
