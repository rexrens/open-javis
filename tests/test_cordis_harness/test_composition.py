"""Composition layers: reading, relative paths, patch addressing, ordering."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness import composition
from harness.composition import CompositionError


def _write(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")
    return path


def test_read_rows_accepts_a_list_and_an_insert_payload(tmp_path):
    listed = _write(tmp_path / "a.yml", [{"id": "one", "name": "harness.plugins.llm"}])
    assert composition.read_rows(listed) == [{"id": "one", "name": "harness.plugins.llm"}]

    wrapped = _write(tmp_path / "b.yml", {"insert": [{"id": "two", "name": "x"}]})
    assert composition.read_rows(wrapped) == [{"id": "two", "name": "x"}]

    empty = _write(tmp_path / "c.yml", None)
    assert composition.read_rows(empty) == []


def test_read_rows_rejects_broken_layers(tmp_path):
    with pytest.raises(CompositionError, match="must be a list"):
        composition.read_rows(_write(tmp_path / "bad.yml", {"id": "x"}))
    with pytest.raises(CompositionError, match="not found"):
        composition.read_rows(tmp_path / "missing.yml")


def test_patch_updates_existing_rows_and_appends_new_ones():
    rows = [
        {"id": "agent", "name": "harness.plugins.agent", "config": {"model": "old", "maxSteps": 12}},
        {"id": "cli", "name": "harness.plugins.cli"},
    ]
    patched = composition.apply_patch(
        rows,
        [
            {"id": "agent", "config": {"model": "new"}},
            {"id": "clock", "name": "/tmp/clock.py"},
            {"id": "cli", "disabled": True},
        ],
        source="patch.yml",
    )
    assert [row["id"] for row in patched] == ["agent", "cli", "clock"]
    assert patched[0]["name"] == "harness.plugins.agent"  # untouched fields survive
    assert patched[0]["config"] == {"model": "new"}  # config is replaced wholesale
    assert patched[1]["disabled"] is True
    assert patched[2]["name"] == "/tmp/clock.py"
    assert rows[0]["config"] == {"model": "old", "maxSteps": 12}  # input untouched


def test_patch_rows_need_an_id():
    with pytest.raises(CompositionError, match="needs an `id`"):
        composition.apply_patch([], [{"name": "/tmp/plugin.py"}], source="patch.yml")


def test_relative_names_resolve_against_their_own_layer(tmp_path):
    base_dir = tmp_path / "base"
    project_dir = tmp_path / "project"
    base = _write(base_dir / "cordis.yml", [{"id": "a", "name": "./a.py"}])
    patch = _write(project_dir / "cordis.patch.yml", [{"id": "b", "name": "./plugins/b.py"}])

    rows, applied = composition.compose(base, [patch])
    assert [row["name"] for row in rows] == [
        str((base_dir / "a.py").resolve()),
        str((project_dir / "plugins" / "b.py").resolve()),
    ]
    # Package names and absolute paths pass through untouched.
    passthrough = composition.resolve_names(
        [{"id": "c", "name": "harness.plugins.tools"}, {"id": "d", "name": "/abs/d.py"}],
        project_dir,
    )
    assert passthrough[0]["name"] == "harness.plugins.tools"
    assert passthrough[1]["name"] == "/abs/d.py"
    assert applied == [patch.resolve()]


def test_group_rows_resolve_their_members(tmp_path):
    patch = _write(tmp_path / "p.yml", [{"id": "g", "group": [{"id": "m", "name": "./m.py"}]}])
    rows = composition.resolve_names(composition.read_rows(patch), tmp_path)
    assert rows[0]["group"][0]["name"] == str((tmp_path / "m.py").resolve())


def test_patch_paths_are_candidates_and_layer_patches_filters(tmp_path):
    from harness.boot import layer_patches

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()

    extra = tmp_path / "overlay.yml"
    extra.write_text("[]", encoding="utf-8")
    # Candidates are ordered home -> project -> explicit overlays, whether or
    # not they exist yet.
    assert composition.patch_paths(home=home, project=project, extra=[extra]) == [
        home / composition.PATCH_NAME,
        project / composition.PATCH_NAME,
        extra,
    ]
    assert layer_patches(home=home, project=project, extra=[extra]) == [extra]

    (home / composition.PATCH_NAME).write_text("[]", encoding="utf-8")
    (project / composition.PATCH_NAME).write_text("[]", encoding="utf-8")
    assert layer_patches(home=home, project=project, extra=[extra]) == [
        home / composition.PATCH_NAME,
        project / composition.PATCH_NAME,
        extra,
    ]


def test_dump_marks_the_layers_it_merged(tmp_path):
    base = _write(tmp_path / "cordis.yml", [{"id": "a", "name": "harness.plugins.llm"}])
    patch = _write(tmp_path / "cordis.patch.yml", [{"id": "b", "name": "/abs/b.py"}])
    rows, applied = composition.compose(base, [patch])
    text = composition.dump(rows, base=base, patches=applied)
    assert text.startswith("# cdh composition")
    assert f"# base:    {base.resolve()}" in text
    assert f"# patch:   {patch.resolve()}" in text
    assert "edits are overwritten" in text
    assert composition.write(rows, tmp_path / "out.yml") .is_file()
