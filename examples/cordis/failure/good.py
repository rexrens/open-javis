"""Tutorial chapter 9: the healthy neighbor that keeps running."""

from __future__ import annotations

name = "good-plugin"


def apply(ctx):
    del ctx
    print("[good] I keep running even though bad.py FAILED")
