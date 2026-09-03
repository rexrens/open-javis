"""Skill registry + filesystem provider (dsh skill seam, trimmed)."""
from core.skill import FileSkillProvider, SkillRegistry, is_skill_name


def test_is_skill_name():
    assert is_skill_name("poetic-note")
    assert is_skill_name("abc123")
    assert not is_skill_name("Poetic Note")
    assert not is_skill_name("-bad")
    assert not is_skill_name("bad/name")


def test_file_provider_discovers_and_loads(tmp_path):
    skill_dir = tmp_path / "poetic-note"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: poetic-note\ndescription: Write notes as poetry.\n---\n"
        "Always answer with a two-line poem.\n",
        encoding="utf-8",
    )
    provider = FileSkillProvider(tmp_path)
    summaries = provider.list()
    assert [s.name for s in summaries] == ["poetic-note"]
    assert summaries[0].description == "Write notes as poetry."
    definition = provider.get("poetic-note")
    assert definition is not None
    assert "two-line poem" in definition.content
    assert provider.get("nope") is None


def test_registry_merges_providers_and_runtime():
    from core.skill import SkillDefinition, SkillSummary

    class P:
        name = "p"
        def list(self):
            return [SkillSummary(name="only-provider", description="from provider")]
        def get(self, name):
            if name == "only-provider":
                return SkillDefinition(name="only-provider", description="from provider", content="body")
            return None

    registry = SkillRegistry(ctx=None)
    registry.register_provider(P())
    registry.register_skill(SkillDefinition(name="runtime-skill", description="from runtime", content="rt"))
    names = {s.name for s in registry.list()}
    assert names == {"only-provider", "runtime-skill"}
    assert registry.get("runtime-skill").content == "rt"
    assert registry.get("only-provider").content == "body"
