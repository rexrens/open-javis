"""插件：provide "sessions" —— SessionStore（dsh：session 是一等服务）。"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.session import SessionStore


def apply(ctx) -> None:
    """装配：provide "sessions"——SessionStore 服务（session 一等服务）。"""
    ctx.provide("sessions", SessionStore(ctx))
