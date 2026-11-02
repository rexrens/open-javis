"""Tutorial chapter 10: the HMR watcher entry (wired into cordis.yml).

The composition mounts HMR by re-exporting the service class: ``apply = Hmr``
plus ``Config = Hmr.Config`` so the entry's ``config`` is validated against
the watcher's schema (``root`` / ``interval``).
"""

from javis.cordis.loader.hmr import Hmr

Config = Hmr.Config
apply = Hmr
