"""Tutorial chapter 9: what happens when ``apply`` throws.

A plugin whose ``apply`` raises lands in ``FAILED`` state. The loader keeps
the other plugins running — one broken plugin does not take the composition
down — and the runner reports the failure and exits 1 (see ``runner.py``).

This chapter is deliberately broken: ``bad.py`` raises, ``good.py`` runs
normally. Run it to see the failure report.

API shown: ``FiberState.FAILED`` · ``fiber._error`` · the runner's
failure handling (``settle`` + fiber inspection).
"""

from __future__ import annotations

name = "bad-plugin"


def apply(ctx):
    del ctx
    print("[bad] about to raise…")
    raise RuntimeError("boom: config typo or an invalid dependency")
