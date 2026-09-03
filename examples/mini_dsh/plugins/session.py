"""插件：provide "sessions" —— SessionStore（dsh：session 是一等服务）。"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.session import SessionStore


def apply(ctx) -> None:
    ctx.provide("sessions", SessionStore(ctx))
