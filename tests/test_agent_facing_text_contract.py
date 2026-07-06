"""Agent-facing text teaches the global path contract."""
from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Iterator

import yaml


ROOT = Path(__file__).resolve().parent.parent

STATIC_TEXT_FILES = [
    "README.md",
    "SKILL.md",
    "hooks/README.md",
    "plugins/conv/SKILL.md",
    "plugins/conv/.claude-plugin/plugin.json",
    "plugins/conv/.codex-plugin/plugin.json",
    "plugins/conv/hooks/README.md",
    "plugins/conv/skills/conversate/SKILL.md",
    "commands/claude/conv/.claude-plugin/plugin.json",
    "references/branching.md",
    "references/cli.md",
    "references/list.md",
    "references/resume.md",
    "references/save.md",
]

DIRECT_COMMON_PATH_SKILLS = {
    "save": (
        "plugins/conv/skills/save/SKILL.md",
        "commands/claude/conv/skills/save/SKILL.md",
    ),
    "list": (
        "plugins/conv/skills/list/SKILL.md",
        "commands/claude/conv/skills/list/SKILL.md",
    ),
    "resume": (
        "plugins/conv/skills/resume/SKILL.md",
        "commands/claude/conv/skills/resume/SKILL.md",
    ),
    "park": (
        "plugins/conv/skills/park/SKILL.md",
        "commands/claude/conv/skills/park/SKILL.md",
    ),
    "sidekick": (
        "plugins/conv/skills/sidekick/SKILL.md",
        "commands/claude/conv/skills/sidekick/SKILL.md",
    ),
    "continue": (
        "plugins/conv/skills/continue/SKILL.md",
        "commands/claude/conv/skills/continue/SKILL.md",
    ),
    "return": (
        "plugins/conv/skills/return/SKILL.md",
        "commands/claude/conv/skills/return/SKILL.md",
    ),
}

DIRECT_COMMON_PATH_COMMANDS = {
    "save": (
        "python ~/.conversate/scripts/conv_cli.py init",
        "python ~/.conversate/scripts/conv_cli.py upsert --stdin",
    ),
    "list": (
        "python ~/.conversate/scripts/conv_cli.py list --limit 10",
    ),
    "resume": (
        "python ~/.conversate/scripts/conv_cli.py search",
        "python ~/.conversate/scripts/conv_cli.py show",
        "python ~/.conversate/scripts/conv_cli.py set-status",
    ),
    "park": (
        "python ~/.conversate/scripts/conv_cli.py init",
        "python ~/.conversate/scripts/conv_cli.py upsert --stdin --status parked",
    ),
    "sidekick": (
        "python ~/.conversate/scripts/conv_cli.py sidekick",
    ),
    "continue": (
        "python ~/.conversate/scripts/conv_cli.py continue",
    ),
    "return": (
        "python ~/.conversate/scripts/conv_cli.py return",
    ),
}


def _is_shipped(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    return not any(part.startswith(".") for part in rel.parts)


def shipped_skill_and_reference_files() -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            dirname for dirname in dirnames
            if not dirname.startswith(".") and dirname != "__pycache__"
        ]
        path = Path(dirpath)
        if "SKILL.md" in filenames and _is_shipped(path / "SKILL.md"):
            files.append(path / "SKILL.md")
        if path.name == "references":
            files.extend(
                path / filename
                for filename in filenames
                if filename.endswith(".md") and _is_shipped(path / filename)
            )
    return sorted(set(files))


def covered_files() -> list[Path]:
    files = [ROOT / rel for rel in STATIC_TEXT_FILES]
    files.extend(shipped_skill_and_reference_files())
    return sorted(set(files))


def text_for(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def bytes_for(path: Path) -> bytes:
    return path.read_bytes()


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""


def _construct_mapping_without_duplicates(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_without_duplicates,
)


def _frontmatter_block(text: str, path: Path) -> str:
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", text, re.DOTALL)
    assert match, f"{path.relative_to(ROOT)} has no YAML frontmatter block"
    return match.group(1)


def _frontmatter(path: Path) -> dict:
    data = yaml.load(_frontmatter_block(text_for(path), path), Loader=UniqueKeyLoader)
    assert isinstance(data, dict), f"{path.relative_to(ROOT)} frontmatter is not a YAML mapping"
    return data


def agent_facing_skill_files() -> list[Path]:
    return sorted(path for path in shipped_skill_and_reference_files() if path.name == "SKILL.md")


def plugin_skill_files() -> list[Path]:
    return sorted((ROOT / "plugins" / "conv" / "skills").glob("*/SKILL.md"))


def claude_command_skill_files() -> list[Path]:
    return sorted((ROOT / "commands" / "claude" / "conv" / "skills").glob("*/SKILL.md"))


def plugin_verb_skill_names() -> set[str]:
    return {path.parent.name for path in plugin_skill_files()} - {"conversate"}


def direct_common_path_skill_files() -> list[Path]:
    return [
        path
        for path in sorted(plugin_skill_files() + claude_command_skill_files())
        if "## Common Path" in text_for(path)
    ]


def _json_strings(value) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_strings(item)


def hook_prompt_texts() -> Iterator[tuple[Path, str]]:
    hook_roots = [ROOT / "hooks", ROOT / "plugins" / "conv" / "hooks"]
    for root in hook_roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            data = json.loads(text_for(path))
            for value in _json_strings(data):
                yield path, value

        for path in sorted(root.rglob("conv-turn-counter.ps1")):
            for match in re.finditer(r'Write-Output\s+"([^"]+)"', text_for(path)):
                yield path, match.group(1)

        for path in sorted(root.rglob("conv_turn_counter.py")):
            tree = ast.parse(text_for(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                if not any(isinstance(target, ast.Name) and target.id == "REMINDER" for target in node.targets):
                    continue
                value = ast.literal_eval(node.value)
                if isinstance(value, str):
                    yield path, value

        for path in sorted(root.rglob("conv-turn-counter.ts")):
            text = text_for(path)
            match = re.search(r"const\s+REMINDER\s*=\s*(.*?);", text, re.DOTALL)
            if match:
                yield path, " ".join(re.findall(r'"([^"]*)"', match.group(1)))


def test_agent_text_names_the_runtime_model_terms_and_paths() -> None:
    corpus = "\n".join(text_for(path) for path in covered_files())

    for required in (
        "Plugin source",
        "universal installation root",
        "Plugin installation root",
        "canonical installed plugin root",
        "canonical hook root",
        "real agent config surface",
        "Conversation database",
        "~/.conversate/",
        "~/.conversate/conv/",
        "~/.conversate/hooks/",
        "~/.conversate/convs/",
        "~/.codex/",
        "~/.claude/",
    ):
        assert required in corpus

    assert "python ~/.conversate/scripts/conv_cli.py" in corpus
    assert "python .conversate/scripts/conv_cli.py" not in corpus


def test_every_shipped_skill_file_names_the_runtime_model() -> None:
    for path in [path for path in shipped_skill_and_reference_files() if path.name == "SKILL.md"]:
        text = text_for(path)
        for required in (
            "Plugin source",
            "Plugin installation root",
            "Conversation database",
            "~/.conversate/",
        ):
            assert required in text, f"{path.relative_to(ROOT)} is missing {required!r}"


def test_agent_text_avoids_old_path_model_and_terms() -> None:
    forbidden = (
        "conversation store",
        ".conversate store",
        "runtime store",
        "data root",
        "active store",
        "payload",
        "bundle",
        "<cwd>/.conversate",
        "CONVERSATE_ROOT",
        "BRAIN_CONV",
        "~/.conversate/.agents/skills/conv",
        "~/.conversate/.claude/skills/conv",
        "~/.conversate/.codex/hooks.json",
        "~/.conversate/.claude/settings.json",
        "<plugin-root>/.codex/hooks.json",
        "<plugin-root>/.claude/settings.json",
    )

    for path in covered_files():
        text = text_for(path)
        lowered = text.lower()
        for phrase in forbidden:
            assert phrase.lower() not in lowered, f"{path.relative_to(ROOT)} contains {phrase!r}"


def _frontmatter_value(text: str, key: str) -> str | None:
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", text, re.DOTALL)
    if not match:
        return None
    data = yaml.load(match.group(1), Loader=UniqueKeyLoader)
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    if value is None:
        return None
    return str(value).lower() if isinstance(value, bool) else str(value)


def _common_path_text(path: Path) -> str:
    text = text_for(path)
    assert "## Common Path" in text, f"{path.relative_to(ROOT)} has no common path section"
    assert "## Lazy References" in text, f"{path.relative_to(ROOT)} has no lazy reference section"
    return text.split("## Lazy References", 1)[0]


def _pre_first_cli_action_text(path: Path) -> str:
    common = _common_path_text(path)
    cli = "python ~/.conversate/scripts/conv_cli.py"
    assert cli in common, f"{path.relative_to(ROOT)} has no CLI action in the common path"
    return common.split(cli, 1)[0]


def test_agent_facing_skill_frontmatter_is_valid_yaml_with_required_scalars() -> None:
    for path in agent_facing_skill_files():
        frontmatter = _frontmatter(path)
        for key in ("name", "description"):
            value = frontmatter.get(key)
            assert isinstance(value, str) and value.strip(), (
                f"{path.relative_to(ROOT)} frontmatter {key!r} must be a non-empty string"
            )
        assert frontmatter["name"] == path.parent.name, (
            f"{path.relative_to(ROOT)} frontmatter name must match its containing folder"
        )

        if path.parent.parent.name == "skills" and frontmatter["name"] != "conversate":
            value = frontmatter.get("disable-model-invocation")
            assert isinstance(value, bool), (
                f"{path.relative_to(ROOT)} frontmatter 'disable-model-invocation' must be a YAML boolean"
            )
        argument_hint = frontmatter.get("argument-hint")
        if argument_hint is not None:
            assert isinstance(argument_hint, str) and argument_hint.strip(), (
                f"{path.relative_to(ROOT)} frontmatter 'argument-hint' must be a non-empty string scalar"
            )


def test_direct_common_paths_do_not_front_load_broad_docs() -> None:
    for verb, rel_paths in DIRECT_COMMON_PATH_SKILLS.items():
        for rel_path in rel_paths:
            path = ROOT / rel_path
            common = _common_path_text(path)
            assert "Do not load broad instructions for the common path" in common
            assert "~/.conversate/references/" not in common
            assert "~/.conversate/SKILL.md" not in text_for(path)
            assert "follow it exactly" not in common
            for command in DIRECT_COMMON_PATH_COMMANDS[verb]:
                assert command in common, f"{rel_path} does not teach {command!r}"


def test_pre_action_common_paths_do_not_reference_broad_docs_by_relative_or_bare_paths() -> None:
    forbidden = (
        re.compile(r"(?<!~/.conversate/)references/[\w*.-]+\.md"),
        re.compile(r"\.\./references(?:/|$)"),
        re.compile(r"\bSKILL\.md\b"),
    )
    for path in direct_common_path_skill_files():
        pre_action = _pre_first_cli_action_text(path)
        for pattern in forbidden:
            assert not pattern.search(pre_action), (
                f"{path.relative_to(ROOT)} front-loads broad docs before its first CLI action: {pattern.pattern}"
            )


def test_direct_references_are_lazy_after_first_cli_action() -> None:
    for rel_paths in DIRECT_COMMON_PATH_SKILLS.values():
        for rel_path in rel_paths:
            path = ROOT / rel_path
            text = text_for(path)
            first_cli = text.index("python ~/.conversate/scripts/conv_cli.py")
            lazy_refs = text.index("## Lazy References")
            first_reference = text.index("~/.conversate/references/")
            lazy_text = text[lazy_refs:]
            assert first_cli < first_reference
            assert lazy_refs < first_reference
            assert re.search(r"Only after .*advanced", lazy_text), f"{rel_path} does not gate refs on advanced behavior"


def test_direct_branch_common_paths_use_primitives_not_manual_record_flows() -> None:
    forbidden = ("upsert --stdin", "set-status", "regen-refs", "follow it exactly")
    for verb in ("sidekick", "continue", "return"):
        for rel_path in DIRECT_COMMON_PATH_SKILLS[verb]:
            common = _common_path_text(ROOT / rel_path)
            for phrase in forbidden:
                assert phrase not in common, f"{rel_path} teaches manual branch flow {phrase!r}"


def test_claude_command_skill_names_match_plugin_verb_skill_names() -> None:
    command_names = {path.parent.name for path in claude_command_skill_files()}
    plugin_names = plugin_verb_skill_names()

    assert command_names == plugin_names, (
        f"Claude command mirror skills mismatch plugin verb skills: "
        f"missing={sorted(plugin_names - command_names)}, extra={sorted(command_names - plugin_names)}"
    )


def test_claude_command_skill_text_matches_plugin_skill_text_for_every_command_skill() -> None:
    plugin_root = ROOT / "plugins" / "conv" / "skills"
    command_files = claude_command_skill_files()
    assert command_files, "expected Claude command mirror skills to be present"
    for command_path in command_files:
        plugin_path = plugin_root / command_path.parent.name / "SKILL.md"
        assert plugin_path.is_file(), f"missing plugin source for {command_path.parent.name}"
        assert bytes_for(command_path) == bytes_for(plugin_path), (
            f"{command_path.relative_to(ROOT)} byte-drifted from {plugin_path.relative_to(ROOT)}"
        )


def test_claude_command_skill_flags_match_plugin_skill_flags() -> None:
    plugin_root = ROOT / "plugins" / "conv" / "skills"
    command_root = ROOT / "commands" / "claude" / "conv" / "skills"
    for command_skill in sorted(command_root.glob("*/SKILL.md")):
        plugin_skill = plugin_root / command_skill.parent.name / "SKILL.md"
        assert plugin_skill.is_file(), f"missing plugin source for {command_skill.parent.name}"
        plugin_flag = _frontmatter_value(text_for(plugin_skill), "disable-model-invocation")
        command_flag = _frontmatter_value(text_for(command_skill), "disable-model-invocation")
        assert command_flag == plugin_flag, f"{command_skill.relative_to(ROOT)} flag drifted from plugin copy"


def test_agent_text_never_teaches_repo_local_convs_as_default_database() -> None:
    local_convs = re.compile(r"(?<!~/)\.conversate[/\\]convs", re.IGNORECASE)
    local_cli = re.compile(r"python\s+\.conversate[/\\]scripts[/\\]conv_cli\.py", re.IGNORECASE)
    for path in covered_files():
        text = text_for(path)
        assert not local_convs.search(text), f"{path.relative_to(ROOT)} mentions repo-local convs/"
        assert not local_cli.search(text), f"{path.relative_to(ROOT)} mentions repo-local conv_cli.py"


def test_hook_snippets_and_reminders_do_not_teach_the_old_root_model() -> None:
    forbidden = (
        "python .conversate",
        "<cwd>/.conversate",
        "cwd-local",
        "repo-local",
        "project-local",
        "local compatibility marker",
        "conversation store",
        "data root",
        "active store",
    )
    seen = 0
    for path, prompt_text in hook_prompt_texts():
        seen += 1
        lowered = prompt_text.lower()
        for phrase in forbidden:
            assert phrase not in lowered, f"{path.relative_to(ROOT)} prompt text contains {phrase!r}"
        if "conv-turn-counter" in prompt_text or "conv_turn_counter" in prompt_text:
            assert "~/.conversate/hooks/" in prompt_text or "hooks/" in prompt_text
        if "CONV AUTO-SAVE" in prompt_text:
            assert "conv plugin" in prompt_text
            assert "conv:save" in prompt_text
            assert "consider saving" not in lowered
    assert seen, "expected hook prompt-facing text to be checked"


def test_cwd_local_conversate_is_only_documented_as_compatibility() -> None:
    for path in covered_files():
        for line in text_for(path).splitlines():
            if "cwd-local `.conversate/`" in line:
                assert "non-default" in line and "compatibility" in line


def test_conversation_database_is_not_named_as_the_root() -> None:
    bad_phrasings = (
        "Conversation database is `~/.conversate/`",
        "Conversation database at `~/.conversate/`",
        "Conversation database under `~/.conversate/`",
    )
    for path in covered_files():
        text = text_for(path)
        for phrase in bad_phrasings:
            assert phrase not in text, f"{path.relative_to(ROOT)} calls the root the database"
