"""``assert_entries_settled``: fail loud on FAILED / PENDING composition rows."""

from __future__ import annotations

import pytest

from javis.cordis import Context
from javis.cordis.loader import Loader, assert_entries_settled
from javis.cordis.registry import settle


def _write(tmp_path, body: str):
    path = tmp_path / "cordis.yml"
    path.write_text(body, encoding="utf-8")
    return path


async def _boot(tmp_path, body: str) -> Context:
    ctx = Context()
    fiber = ctx.plugin(Loader, {"file": str(_write(tmp_path, body))})
    await fiber
    await settle(ctx)
    return ctx


@pytest.mark.asyncio
async def test_active_entries_pass(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("def apply(ctx):\n    ctx.provide('thing', 1)\n", encoding="utf-8")
    ctx = await _boot(tmp_path, "- id: ok\n  name: ./ok.py\n")
    assert_entries_settled(ctx)  # does not raise
    assert ctx.get("thing") == 1


@pytest.mark.asyncio
async def test_failed_entry_reports_original_error(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("def apply(ctx):\n    raise ValueError('boom')\n", encoding="utf-8")
    ctx = await _boot(tmp_path, "- id: bad\n  name: ./bad.py\n")
    with pytest.raises(RuntimeError, match=r"'bad'.*boom"):
        assert_entries_settled(ctx)


@pytest.mark.asyncio
async def test_pending_entry_lists_missing_services(tmp_path):
    late = tmp_path / "late.py"
    late.write_text("def apply(ctx):\n    pass\n", encoding="utf-8")
    ctx = await _boot(
        tmp_path,
        "- id: late\n  name: ./late.py\n  inject: [notARealService]\n",
    )
    with pytest.raises(RuntimeError, match="notARealService"):
        assert_entries_settled(ctx)
