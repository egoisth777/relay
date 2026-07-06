#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


STATUSES = {"active", "parked", "closed"}
REL_REVERSE = {
    "spawned-from": "spawned-to",
    "spawned-to": "spawned-from",
    "continued-from": "continued-as",
    "continued-as": "continued-from",
    "informed-by": "informed",
    "informed": "informed-by",
}
FORWARD_REF_RELS = {"spawned-from", "continued-from", "informed-by"}
BRANCH_PARENT_RELS = {"spawned-from", "continued-from"}
MANDATORY_SECTIONS = ("summary", "dict", "qa")
# Resumption sections: always written (as "(none)" when empty) but never hard-fail upsert.
ALWAYS_SECTIONS = ("resume", "user-instructions", "condensed-transcript")
INFORMATIONAL_SECTIONS = (
    "sources",
    "insights",
    "decisions",
    "digest",
)
SECTION_ORDER = MANDATORY_SECTIONS + INFORMATIONAL_SECTIONS + ALWAYS_SECTIONS
NONE_PLACEHOLDER = "(none)"
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}
INDEX_REQUIRED_FIELDS = ("id", "topic", "status", "tags", "refs", "created", "updated", "file", "open")
CANONICAL_GITIGNORE = ".semble/\nindex.jsonl\n__pycache__/\n"


class ConvError(Exception):
    pass


@dataclass
class Conversation:
    path: Path
    meta: dict[str, Any]
    body: str

    @property
    def id(self) -> str:
        return str(self.meta["id"])


DEFAULT_ROOT_NAME = ".conversate"


@dataclass
class Resolution:
    root: Path | None
    layer: str  # "default-global" | "compat-flag"
    compatibility: bool = False


def default_plugin_installation_root() -> Path:
    return (Path.home() / DEFAULT_ROOT_NAME).expanduser().resolve()


def plugin_installation_root(args: argparse.Namespace | None = None) -> Path:
    explicit = getattr(args, "conv_root", None) if args else None
    if explicit:
        return Path(explicit).expanduser().resolve()
    return default_plugin_installation_root()


def conversation_database(root: Path) -> Path:
    return root / "convs"


def resolve_conv_root(args: argparse.Namespace | None = None) -> Resolution:
    """Resolve the Plugin installation root.

    Normal CLI operation always defaults to ~/.conversate. The legacy --conv-root flag is
    retained only as an explicit compatibility override; cwd markers and historical env
    vars do not participate in default resolution.
    """
    explicit = getattr(args, "conv_root", None) if args else None
    if explicit:
        return Resolution(Path(explicit).expanduser().resolve(), "compat-flag", compatibility=True)
    return Resolution(default_plugin_installation_root(), "default-global")


def _root_or_raise(res: Resolution) -> Path:
    if res.root is None:
        raise ConvError("cannot resolve the Plugin installation root")
    return res.root


def conv_root(args: argparse.Namespace | None = None) -> Path:
    return _root_or_raise(resolve_conv_root(args))


def resolution_report(res: Resolution) -> dict[str, Any]:
    ignored_env = [name for name in ("CONVERSATE_ROOT", "BRAIN_CONV") if os.environ.get(name)]
    return {
        "layer": res.layer,
        "compatibility": res.compatibility,
        "ignored_legacy_env": ignored_env,
    }


def convs_dir(root: Path) -> Path:
    return conversation_database(root)


def index_path(root: Path) -> Path:
    return root / "index.jsonl"


def gitignore_path(root: Path) -> Path:
    return root / ".gitignore"


def ensure_layout(root: Path) -> None:
    if root.exists() and not root.is_dir():
        raise ConvError(f"Plugin installation root must be a directory, not a file: {root}")
    try:
        convs_dir(root).mkdir(parents=True, exist_ok=True)
        (root / ".semble").mkdir(parents=True, exist_ok=True)
        index_path(root).touch(exist_ok=True)
    except OSError as exc:
        raise ConvError(f"cannot create Plugin installation root layout at {root}: {exc}") from exc


def write_gitignore(root: Path) -> None:
    """Ignore derived/cache artifacts inside the store; conversation records stay trackable."""
    path = gitignore_path(root)
    if not path.exists():
        path.write_text(CANONICAL_GITIGNORE, encoding="utf-8", newline="\n")


def repair_gitignore(root: Path) -> bool:
    path = gitignore_path(root)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == CANONICAL_GITIGNORE:
        return False
    path.write_text(CANONICAL_GITIGNORE, encoding="utf-8", newline="\n")
    return True


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_value(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(value)


def toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def toml_list(values: list[str]) -> str:
    return "[" + ", ".join(toml_quote(str(v)) for v in values) + "]"


def normalize_ref(ref: dict[str, Any]) -> dict[str, str]:
    raw_id = ref.get("id", "")
    rid = "" if raw_id is None else str(raw_id)
    rel = str(ref.get("rel", "")).strip()
    if not rid or not rel:
        raise ConvError(f"invalid ref: {ref!r}")
    validate_conversation_id(rid)
    if rel not in REL_REVERSE:
        raise ConvError(f"unknown ref rel {rel!r}; expected one of {sorted(REL_REVERSE)}")
    return {"id": rid, "rel": rel}


def normalize_refs(refs: Any) -> list[dict[str, str]]:
    if refs is None:
        return []
    if not isinstance(refs, list):
        raise ConvError("refs must be a list")
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for raw in refs:
        if not isinstance(raw, dict):
            raise ConvError(f"ref must be an object: {raw!r}")
        ref = normalize_ref(raw)
        key = (ref["id"], ref["rel"])
        if key not in seen:
            seen.add(key)
            out.append(ref)
    return sorted(out, key=lambda item: (item["id"], item["rel"]))


def normalize_meta(raw: dict[str, Any], *, existing: dict[str, Any] | None = None, root: Path | None = None) -> dict[str, Any]:
    topic = str(raw.get("topic", existing.get("topic") if existing else "")).strip()
    if not topic:
        raise ConvError("topic is required")
    status = str(raw.get("status", existing.get("status", "active") if existing else "active")).strip()
    if status not in STATUSES:
        raise ConvError(f"status must be one of {sorted(STATUSES)}")
    tags = raw.get("tags", existing.get("tags", []) if existing else [])
    if tags is None:
        tags = []
    if not isinstance(tags, list):
        raise ConvError("tags must be a list")
    tags = sorted({str(tag).strip() for tag in tags if str(tag).strip()})
    created = raw.get("created", existing.get("created") if existing else None) or now_utc()
    updated = raw.get("updated") or now_utc()
    if "id" in raw:
        raw_id = raw.get("id")
    elif existing:
        raw_id = existing.get("id")
    else:
        raw_id = ""
    cid = "" if raw_id is None else str(raw_id)
    if not cid:
        cid = make_unique_id(topic, root if root is not None else conv_root())
    else:
        validate_conversation_id(cid)
    refs = normalize_refs(raw.get("refs", existing.get("refs", []) if existing else []))
    return {
        "id": cid,
        "topic": topic,
        "status": status,
        "tags": tags,
        "refs": refs,
        "created": iso_value(created),
        "updated": iso_value(updated),
    }


def slugify(topic: str, limit: int = 64) -> str:
    slug = topic.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "conversation"
    return slug[:limit].strip("-") or "conversation"


def make_id(topic: str, date: datetime | None = None) -> str:
    date = date or datetime.now()
    return f"conv_{date.strftime('%y%m%d')}_{slugify(topic)}"


def make_unique_id(topic: str, root: Path) -> str:
    base = make_id(topic)
    candidate = base
    existing = {conv.id for conv in read_all(root, tolerate=True)}
    n = 2
    while candidate in existing:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def file_name_for_id(cid: str) -> str:
    match = re.match(r"^conv_(\d{2})(\d{2})(\d{2})_(.+)$", cid)
    if not match:
        return f"{cid}.md"
    yy, mm, dd, slug = match.groups()
    return f"20{yy}-{mm}-{dd}_{slug}.md"


def validate_portable_filename_component(value: str, *, kind: str) -> None:
    if not value:
        raise ConvError(f"{kind} must be a portable filename component")
    if value != value.strip(" "):
        raise ConvError(f"{kind} must be a portable filename component")
    if value.endswith((".", " ")):
        raise ConvError(f"{kind} must be a portable filename component")
    if value in {".", ".."} or INVALID_FILENAME_CHARS.search(value):
        raise ConvError(f"{kind} must be a portable filename component")
    stem = value.split(".", 1)[0].upper()
    if stem in WINDOWS_DEVICE_NAMES:
        raise ConvError(f"{kind} must be a portable filename component")


def validate_conversation_id(cid: str) -> None:
    if "/" in cid or "\\" in cid:
        raise ConvError("conversation id must produce a file inside the Conversation database")
    validate_portable_filename_component(cid, kind="conversation id")


def record_path_for_id(root: Path, cid: str) -> Path:
    validate_conversation_id(cid)
    filename = file_name_for_id(cid)
    validate_portable_filename_component(filename, kind="conversation id")
    base = convs_dir(root).resolve()
    path = (base / filename).resolve()
    if path.parent != base:
        raise ConvError("conversation id must produce a file inside the Conversation database")
    return path


def split_frontmatter(text: str, path: Path) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "+++":
        raise ConvError(f"{path} is missing TOML +++ frontmatter")
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "+++":
            end = idx
            break
    if end is None:
        raise ConvError(f"{path} has unterminated TOML frontmatter")
    return "".join(lines[1:end]), "".join(lines[end + 1 :])


def read_conv(path: Path) -> Conversation:
    text = path.read_text(encoding="utf-8")
    front, body = split_frontmatter(text, path)
    try:
        meta = tomllib.loads(front)
    except tomllib.TOMLDecodeError as exc:
        raise ConvError(f"{path} has invalid TOML frontmatter: {exc}") from exc
    meta["created"] = iso_value(meta.get("created", ""))
    meta["updated"] = iso_value(meta.get("updated", ""))
    meta["refs"] = normalize_refs(meta.get("refs", []))
    for key in ("id", "topic", "status"):
        if key not in meta:
            raise ConvError(f"{path} frontmatter missing {key}")
    return Conversation(path=path, meta=meta, body=body)


def read_all(root: Path, *, tolerate: bool = False) -> list[Conversation]:
    ensure_layout(root)
    convs: list[Conversation] = []
    for path in sorted(convs_dir(root).glob("*.md")):
        try:
            convs.append(read_conv(path))
        except Exception:
            if not tolerate:
                raise
    return convs


def find_by_id(root: Path, cid: str) -> Conversation | None:
    for conv in read_all(root, tolerate=True):
        if conv.id == cid:
            return conv
    return None


def find_existing_for_upsert(root: Path, cid: str) -> tuple[Conversation | None, list[dict[str, Any]] | None]:
    if not cid:
        return None, None
    path = record_path_for_id(root, cid)
    if path.exists():
        conv = read_conv(path)
        if conv.id == cid:
            return conv, None
        raise ConvError(f"conversation file collision for {cid}: {path} has frontmatter id {conv.id}")
    try:
        records = read_index(root)
    except ConvError:
        return find_by_id(root, cid), None
    for record in records:
        if record.get("id") != cid:
            continue
        indexed_path = root / str(record["file"])
        if indexed_path.is_file():
            try:
                indexed = read_conv(indexed_path)
                if indexed.id == cid:
                    return indexed, records
            except ConvError:
                pass
        return find_by_id(root, cid), records
    scanned = find_by_id(root, cid) if any(convs_dir(root).glob("*.md")) else None
    if scanned:
        return scanned, records
    return None, records


def section_matches(body: str) -> list[re.Match[str]]:
    return list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))


def duplicate_section_names(body: str) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for match in section_matches(body):
        name = match.group(1).strip().lower()
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return sorted(duplicates)


def sections_from_body(body: str, *, reject_duplicates: bool = False) -> dict[str, str]:
    matches = section_matches(body)
    if reject_duplicates:
        duplicates = duplicate_section_names(body)
        if duplicates:
            raise ConvError(f"duplicate section(s): {', '.join(duplicates)}")
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        name = match.group(1).strip().lower()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        sections[name] = body[start:end].strip()
    return sections


def count_open(body: str) -> int:
    sections = sections_from_body(body)
    qa = sections.get("qa", "")
    return sum(1 for line in qa.splitlines() if re.search(r"\bq\s*\(open\)|\bopen\s*:", line, re.I))


def normalize_section_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item).rstrip() for item in value if str(item).strip())
    return str(value).strip()


def _as_items(value: Any) -> list[str]:
    """Coerce a scalar or list into a list of non-empty, stripped strings."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


def render_resume(value: Any) -> str:
    """Render the structured `resume` input into markdown bullets; empty -> ""."""
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        raise ConvError("resume must be an object or a string")
    goal = str(value.get("goal", "")).strip()
    groups = (
        ("next-steps", _as_items(value.get("next_steps"))),
        ("open-questions", _as_items(value.get("open_questions"))),
        ("suggested-skills", _as_items(value.get("suggested_skills"))),
    )
    if not goal and not any(items for _, items in groups):
        return ""
    lines = [f"- goal: {goal or NONE_PLACEHOLDER}"]
    for label, items in groups:
        lines.append(f"- {label}:")
        for item in items or [NONE_PLACEHOLDER]:
            lines.append(f"  - {item}")
    return "\n".join(lines)


def render_user_instructions(value: Any) -> str:
    """Render standing user directives as bullets (list) or verbatim text (str)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return "\n".join(f"- {item}" for item in _as_items(value))


def render_condensed_transcript(value: Any) -> str:
    """Render the chronological exchange log as `- U:` / `- A:` bullets. Each entry is a
    `{"u": ..., "a": ...}` object (canonical) or a plain string."""
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        raise ConvError("condensed_transcript must be a list")
    lines: list[str] = []
    for entry in value:
        if isinstance(entry, dict):
            u = str(entry.get("u", "")).strip()
            a = str(entry.get("a", "")).strip()
            if u:
                lines.append(f"- U: {u}")
            if a:
                lines.append(f"- A: {a}")
        else:
            text = str(entry).strip()
            if text:
                lines.append(f"- {text}")
    return "\n".join(lines)


def resumption_sections(raw: dict[str, Any]) -> dict[str, str]:
    """Rendered text for the always-on resumption sections, from structured input keys."""
    return {
        "resume": render_resume(raw.get("resume")),
        "user-instructions": render_user_instructions(raw.get("user_instructions")),
        "condensed-transcript": render_condensed_transcript(raw.get("condensed_transcript")),
    }


def normalize_sections(sections: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_name, value in sections.items():
        name = str(raw_name).strip().lower()
        if name:
            out[name] = normalize_section_value(value)
    return out


def render_canonical_body(sections: dict[str, Any], *, always: dict[str, Any] | None = None) -> str:
    normalized = normalize_sections(sections)
    recovery = normalize_sections(always or {})
    parts: list[str] = []
    emitted: set[str] = set()
    for name in SECTION_ORDER:
        if name in ALWAYS_SECTIONS:
            content = recovery.get(name) or normalized.get(name) or NONE_PLACEHOLDER
        else:
            content = normalized.get(name, "")
            if not content:
                continue
        parts.append(f"## {name}\n{content.strip()}\n")
        emitted.add(name)
    for name in sorted(normalized):
        if name in emitted:
            continue
        content = normalized.get(name, "")
        if content:
            parts.append(f"## {name}\n{content.strip()}\n")
    return "\n".join(parts).rstrip() + "\n"


def build_body(raw: dict[str, Any]) -> str:
    always = resumption_sections(raw)
    if raw.get("body"):
        body = str(raw["body"]).strip() + "\n"
        present = sections_from_body(body, reject_duplicates=True)
        missing = [name for name in MANDATORY_SECTIONS if name not in present]
        if missing:
            raise ConvError(f"body missing mandatory sections: {', '.join(missing)}")
        return render_canonical_body(present, always=always)

    sections = raw.get("sections")
    if not isinstance(sections, dict):
        raise ConvError("sections object is required when body is not provided")

    missing = [name for name in MANDATORY_SECTIONS if not normalize_section_value(sections.get(name))]
    if missing:
        raise ConvError(f"missing mandatory sections: {', '.join(missing)}")

    return render_canonical_body(sections, always=always)


def dump_frontmatter(meta: dict[str, Any]) -> str:
    refs = normalize_refs(meta.get("refs", []))
    lines = [
        "+++",
        f"id = {toml_quote(str(meta['id']))}",
        f"topic = {toml_quote(str(meta['topic']))}",
        f"status = {toml_quote(str(meta['status']))}",
        f"tags = {toml_list([str(tag) for tag in meta.get('tags', [])])}",
    ]
    if refs:
        lines.append("refs = [")
        for ref in refs:
            lines.append(f"  {{ id = {toml_quote(ref['id'])}, rel = {toml_quote(ref['rel'])} }},")
        lines.append("]")
    else:
        lines.append("refs = []")
    lines.extend(
        [
            f"created = {iso_value(meta['created'])}",
            f"updated = {iso_value(meta['updated'])}",
            "+++",
            "",
        ]
    )
    return "\n".join(lines)


def write_conv(path: Path, meta: dict[str, Any], body: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = dump_frontmatter(meta) + body.strip() + "\n"
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        return False
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


def index_record(root: Path, conv: Conversation) -> dict[str, Any]:
    return {
        "id": conv.id,
        "topic": str(conv.meta.get("topic", "")),
        "status": str(conv.meta.get("status", "")),
        "tags": list(conv.meta.get("tags", [])),
        "refs": normalize_refs(conv.meta.get("refs", [])),
        "created": iso_value(conv.meta.get("created", "")),
        "updated": iso_value(conv.meta.get("updated", "")),
        "file": str(conv.path.relative_to(root)).replace("\\", "/"),
        "open": count_open(conv.body),
    }


def rebuild_index(root: Path, *, tolerate_parse_errors: bool = False) -> list[dict[str, Any]]:
    ensure_layout(root)
    records = [index_record(root, conv) for conv in read_all(root, tolerate=tolerate_parse_errors)]
    records.sort(key=lambda item: item["id"])
    text = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)
    index_path(root).write_text(text, encoding="utf-8", newline="\n")
    return records


def write_index_records(root: Path, records: list[dict[str, Any]]) -> None:
    records.sort(key=lambda item: item["id"])
    text = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)
    index_path(root).write_text(text, encoding="utf-8", newline="\n")


def upsert_index_record(
    root: Path,
    record: dict[str, Any],
    *,
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if records is None:
        try:
            records = read_index(root)
        except ConvError:
            return rebuild_index(root)
    if not records and len(list(convs_dir(root).glob("*.md"))) > 1:
        return rebuild_index(root)
    updated = False
    merged: list[dict[str, Any]] = []
    for existing in records:
        if existing.get("id") == record["id"]:
            if not updated:
                merged.append(record)
                updated = True
            continue
        merged.append(existing)
    if not updated:
        merged.append(record)
    if len(merged) != len(list(convs_dir(root).glob("*.md"))):
        return rebuild_index(root)
    write_index_records(root, merged)
    return merged


def read_index(root: Path, *, tolerate_malformed: bool = False) -> list[dict[str, Any]]:
    ensure_layout(root)
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(index_path(root).read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                if tolerate_malformed:
                    return []
                raise ConvError(f"{index_path(root)} has malformed JSON on line {line_no}: {exc}") from exc
            if not isinstance(record, dict):
                if tolerate_malformed:
                    return []
                raise ConvError(f"{index_path(root)} has non-object JSON on line {line_no}")
            try:
                records.append(validate_index_record(root, record))
            except ConvError as exc:
                if tolerate_malformed:
                    return []
                raise ConvError(f"{index_path(root)} has invalid index record on line {line_no}: {exc}") from exc
    return records


def normalize_index_file(root: Path, value: Any) -> str:
    del root
    if not isinstance(value, str) or not value:
        raise ConvError("index file must be a non-empty relative path")
    if "\\" in value:
        raise ConvError("index file must use normalized forward slashes")
    rel = PurePosixPath(value)
    if rel.is_absolute() or rel.as_posix() != value or "." in rel.parts or ".." in rel.parts:
        raise ConvError("index file must be a normalized relative path")
    if len(rel.parts) != 2 or rel.parts[0] != "convs":
        raise ConvError("index file must point inside the Conversation database")
    if not rel.name.endswith(".md"):
        raise ConvError("index file must point to a markdown record")
    validate_portable_filename_component(rel.name, kind="index file")
    return rel.as_posix()


def validate_index_record(root: Path, record: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in INDEX_REQUIRED_FIELDS if field not in record]
    if missing:
        raise ConvError(f"missing required fields: {', '.join(missing)}")
    for field in ("id", "topic", "status", "created", "updated"):
        if not isinstance(record[field], str):
            raise ConvError(f"{field} must be a string")
    if not record["id"]:
        raise ConvError("id must be a non-empty string")
    if not isinstance(record["tags"], list) or any(not isinstance(tag, str) for tag in record["tags"]):
        raise ConvError("tags must be a list of strings")
    refs = normalize_refs(record["refs"])
    if not isinstance(record["open"], int) or isinstance(record["open"], bool):
        raise ConvError("open must be an integer")
    file = normalize_index_file(root, record["file"])
    if not (root / file).is_file():
        raise ConvError(f"index file points to a missing conversation record: {file}")
    return {
        **record,
        "refs": refs,
        "file": file,
    }


def upsert(
    root: Path,
    raw: dict[str, Any],
    *,
    status_override: str | None = None,
    create_only: bool = False,
) -> dict[str, Any]:
    ensure_layout(root)
    index_records: list[dict[str, Any]] | None = None
    if raw.get("id"):
        existing, index_records = find_existing_for_upsert(root, str(raw.get("id", "")))
    else:
        existing = None
    if create_only and existing:
        raise ConvError(f"conversation already exists: {existing.id}")
    existing_meta = existing.meta if existing else None
    if not raw.get("id"):
        raw = {**raw, "id": make_unique_id(str(raw.get("topic", "")), root)}
    if status_override:
        raw = {**raw, "status": status_override}
    meta = normalize_meta(raw, existing=existing_meta, root=root)
    body = build_body(raw)
    path = existing.path if existing else record_path_for_id(root, meta["id"])
    if create_only and path.exists():
        raise ConvError(f"conversation already exists: {meta['id']}")
    changed = write_conv(path, meta, body)
    refs_before = normalize_refs(existing_meta.get("refs", [])) if existing_meta else []
    refs_after = normalize_refs(meta.get("refs", []))
    if refs_before or refs_after:
        ref_changes = regen_refs(root)
        records = rebuild_index(root)
    else:
        ref_changes = 0
        records = upsert_index_record(
            root,
            index_record(root, Conversation(path=path, meta=meta, body=body)),
            records=index_records,
        )
    return {
        "id": meta["id"],
        "file": str(path.relative_to(root)).replace("\\", "/"),
        "changed": changed,
        "ref_changes": ref_changes,
        "index_records": len(records),
    }


def set_status(root: Path, cid: str, status: str) -> dict[str, Any]:
    if status not in STATUSES:
        raise ConvError(f"status must be one of {sorted(STATUSES)}")
    conv = find_by_id(root, cid)
    if not conv:
        raise ConvError(f"conversation not found: {cid}")
    meta = dict(conv.meta)
    meta["status"] = status
    meta["updated"] = now_utc()
    changed = write_conv(conv.path, meta, conv.body)
    records = rebuild_index(root)
    return {"id": cid, "status": status, "changed": changed, "index_records": len(records)}


def section_text(conv: Conversation, name: str) -> str:
    return sections_from_body(conv.body).get(name, "")


def canonicalize_conversation_body(conv: Conversation) -> bool:
    if duplicate_section_names(conv.body):
        return False
    sections = sections_from_body(conv.body)
    missing = [name for name in MANDATORY_SECTIONS if name not in sections]
    if missing:
        return False
    canonical = render_canonical_body(sections)
    if conv.body.strip() + "\n" == canonical:
        return False
    meta = dict(conv.meta)
    meta["updated"] = now_utc()
    return write_conv(conv.path, meta, canonical)


def seed_branch_sections(parent: Conversation, *, topic: str, rel: str) -> dict[str, str]:
    parent_sections = sections_from_body(parent.body)
    parent_topic = str(parent.meta.get("topic", ""))
    source_label = "continued-from" if rel == "continued-from" else "spawned-from"
    summary_prefix = "Continuation" if rel == "continued-from" else "Sidekick"
    resume_goal = f"Continue {parent_topic}" if rel == "continued-from" else f"Explore {topic}"
    qa = (
        parent_sections.get("qa")
        if rel == "continued-from"
        else "- **Q (open):** What should this sidekick resolve?\n  **A:** (none)"
    )
    source_lines = [f"- {source_label}: {parent.id}", f"- parent-topic: {parent_topic}"]
    parent_sources = parent_sections.get("sources", "")
    if rel == "continued-from" and parent_sources:
        source_lines.extend(line for line in parent_sources.splitlines() if line.strip())
    sections = {
        "summary": f"{summary_prefix} of {parent.id}: {topic}",
        "dict": parent_sections.get("dict") or NONE_PLACEHOLDER,
        "qa": qa or NONE_PLACEHOLDER,
        "resume": parent_sections.get("resume")
        or "\n".join(
            [
                f"- goal: {resume_goal}",
                "- next-steps:",
                "  - Capture progress and save the record",
                "- open-questions:",
                f"  - {topic}",
                "- suggested-skills:",
                "  - conv:save",
            ]
        ),
        "sources": "\n".join(source_lines),
    }
    for name in ("user-instructions", "insights", "decisions"):
        content = parent_sections.get(name)
        if content:
            sections[name] = content
    return sections


def parent_ref_id(branch: Conversation, explicit_parent: str | None = None) -> str:
    refs = normalize_refs(branch.meta.get("refs", []))
    parents = sorted({ref["id"] for ref in refs if ref["rel"] in BRANCH_PARENT_RELS})
    if explicit_parent:
        validate_conversation_id(explicit_parent)
        if explicit_parent not in parents:
            raise ConvError(f"{explicit_parent} is not a branch parent of {branch.id}: {parents}")
        return explicit_parent
    if not parents:
        raise ConvError(f"conversation has no branch parent ref: {branch.id}")
    if len(parents) > 1:
        raise ConvError(f"conversation has multiple branch parent refs: {parents}")
    return parents[0]


def regen_refs(root: Path) -> int:
    convs = read_all(root, tolerate=True)
    by_id = {conv.id: conv for conv in convs}
    desired: dict[str, set[tuple[str, str]]] = {
        conv.id: {
            (ref["id"], ref["rel"])
            for ref in normalize_refs(conv.meta.get("refs", []))
            if ref["rel"] in FORWARD_REF_RELS
        }
        for conv in convs
    }

    for conv in convs:
        for target_id, rel in list(desired[conv.id]):
            reverse = REL_REVERSE.get(rel)
            if reverse and target_id in by_id:
                desired[target_id].add((conv.id, reverse))

    changed = 0
    for conv in convs:
        old = {(ref["id"], ref["rel"]) for ref in normalize_refs(conv.meta.get("refs", []))}
        new = desired[conv.id]
        if old != new:
            meta = dict(conv.meta)
            meta["refs"] = [{"id": rid, "rel": rel} for rid, rel in sorted(new)]
            meta["updated"] = now_utc()
            write_conv(conv.path, meta, conv.body)
            changed += 1
    return changed


def stopwords() -> set[str]:
    return {
        "a",
        "an",
        "and",
        "conv",
        "conversation",
        "discussion",
        "for",
        "in",
        "of",
        "on",
        "the",
        "to",
        "where",
        "we",
        "with",
    }


def query_terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.lower())
    return [word for word in words if word not in stopwords()]


def text_score(text: str, terms: list[str]) -> int:
    low = text.lower()
    return sum(1 for term in terms if term in low)


def search(root: Path, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    import shutil
    import subprocess

    validate_limit(limit)
    ensure_layout(root)
    records = read_index(root, tolerate_malformed=True)
    if not records:
        rebuild_index(root)
        records = read_index(root, tolerate_malformed=True)
    terms = query_terms(query)
    if not terms:
        terms = [query.lower().strip()] if query.strip() else []

    def decorate(record: dict[str, Any], layer: str, score: int) -> dict[str, Any]:
        return {**record, "layer": layer, "score": score}

    filename_hits = []
    for record in records:
        haystack = f"{record.get('id','')} {record.get('file','')}".lower()
        score = text_score(haystack, terms)
        if score:
            filename_hits.append(decorate(record, "fff", score))
    if len(filename_hits) == 1:
        return filename_hits
    if filename_hits:
        return sorted(filename_hits, key=lambda r: (-r["score"], r["updated"]), reverse=False)[:limit]

    index_hits_by_id: dict[str, dict[str, Any]] = {}
    if shutil.which("rg"):
        for term in terms:
            proc = subprocess.run(
                ["rg", "--ignore-case", "--fixed-strings", term, str(index_path(root))],
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.returncode not in (0, 1):
                continue
            for line in proc.stdout.splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    record = validate_index_record(root, record)
                except ConvError:
                    continue
                hit = index_hits_by_id.setdefault(record["id"], decorate(record, "rg-index", 0))
                hit["score"] += 1

    index_hits = list(index_hits_by_id.values())
    if not index_hits:
        for record in records:
            haystack = json.dumps(
                {
                    "id": record.get("id"),
                    "topic": record.get("topic"),
                    "status": record.get("status"),
                    "tags": record.get("tags"),
                    "refs": record.get("refs"),
                },
                ensure_ascii=False,
            ).lower()
            score = text_score(haystack, terms)
            if score:
                index_hits.append(decorate(record, "rg-index-fallback", score))
    if len(index_hits) == 1:
        return index_hits
    if index_hits:
        return sorted(index_hits, key=lambda r: (-r["score"], r["updated"]), reverse=False)[:limit]

    semble_hits = search_semble(root, records, query, limit)
    if semble_hits:
        return semble_hits

    body_hits = []
    for conv in read_all(root, tolerate=True):
        score = text_score(conv.body, terms)
        if score:
            body_hits.append(decorate(index_record(root, conv), "semble-body-fallback", score))
    return sorted(body_hits, key=lambda r: (-r["score"], r["updated"]), reverse=False)[:limit]


def search_semble(root: Path, records: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    import shutil
    import subprocess

    command: list[str] | None = None
    if shutil.which("semble"):
        command = ["semble"]
    elif os.environ.get("CONV_USE_UVX_SEMBLE") == "1" and shutil.which("uvx"):
        command = ["uvx", "semble"]
    if not command:
        return []

    try:
        proc = subprocess.run(
            [*command, "search", "-k", str(limit), query, str(convs_dir(root)), "--content", "docs"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode != 0:
        return []

    output = proc.stdout
    hits: list[dict[str, Any]] = []
    for record in records:
        rel = str(record.get("file", ""))
        basename = Path(rel).name
        absolute = str((root / rel).resolve())
        positions = [pos for needle in (rel, basename, absolute) if (pos := output.find(needle)) >= 0]
        if not positions:
            continue
        best = min(positions)
        hits.append({**record, "layer": "semble", "score": max(1, 10_000 - best)})
    return sorted(hits, key=lambda item: item["score"], reverse=True)[:limit]


def resolve(root: Path, target: str) -> Conversation:
    exact = find_by_id(root, target)
    if exact:
        return exact
    hits = search(root, target, limit=5)
    if not hits:
        raise ConvError(
            f"conversation not found in the Conversation database for {target!r}; "
            "use list or search to find the conversation id"
        )
    if len(hits) != 1:
        ids = [str(hit.get("id", "")) for hit in hits if hit.get("id")]
        raise ConvError(
            f"ambiguous target {target!r}: {ids}; use list or search with a more specific query"
        )
    conv = find_by_id(root, hits[0]["id"])
    if not conv:
        raise ConvError(f"conversation not found after search: {hits[0]['id']}")
    return conv


def print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def print_table(records: list[dict[str, Any]]) -> None:
    headers = ["id", "topic", "status", "updated", "open"]
    rows = [[str(record.get(key, "")) for key in headers] for record in records]
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = min(max(widths[idx], len(cell)), 72)

    def trim(value: str, width: int) -> str:
        return value if len(value) <= width else value[: width - 1] + "..."

    print(" | ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers)))
    print("-|-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(trim(cell, widths[idx]).ljust(widths[idx]) for idx, cell in enumerate(row)))


def validate_limit(limit: int) -> None:
    if limit < 0:
        raise ConvError("--limit must be >= 0")


def load_json_object(text: str, *, source: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConvError(f"{source} has invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConvError(f"{source} must contain a JSON object")
    return raw


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConvError(f"cannot read JSON from {path}: {exc}") from exc
    return load_json_object(text, source=str(path))


def deprecated_aliases(root: Path | None) -> dict[str, str | None]:
    return {
        "conv_root": str(root) if root is not None else None,
        "convs": str(convs_dir(root)) if root is not None else None,
    }


def index_health(root: Path) -> dict[str, Any]:
    try:
        records = read_index(root)
    except ConvError as exc:
        return {"valid": False, "records": 0, "error": str(exc)}
    return {"valid": True, "records": len(records), "error": None}


def cmd_init(args: argparse.Namespace) -> None:
    res = resolve_conv_root(args)
    root = _root_or_raise(res)
    ensure_layout(root)
    write_gitignore(root)
    records = rebuild_index(root)
    print_json(
        {
            "plugin_installation_root": str(root),
            "conversation_database": str(convs_dir(root)),
            "deprecated": {"aliases": deprecated_aliases(root)},
            "index": str(index_path(root)),
            "records": len(records),
        }
    )


def cmd_rebuild_index(args: argparse.Namespace) -> None:
    records = rebuild_index(conv_root(args))
    print_json({"records": len(records)})


def cmd_regen_refs(args: argparse.Namespace) -> None:
    root = conv_root(args)
    changed = regen_refs(root)
    records = rebuild_index(root)
    print_json({"ref_changes": changed, "records": len(records)})


def cmd_upsert(args: argparse.Namespace) -> None:
    if args.stdin:
        raw = load_json_object(sys.stdin.read(), source="stdin")
    elif args.json:
        raw = read_json_object(Path(args.json))
    else:
        raise ConvError("upsert requires --stdin or --json PATH")
    print_json(upsert(conv_root(args), raw, status_override=args.status))


def cmd_set_status(args: argparse.Namespace) -> None:
    print_json(set_status(conv_root(args), args.id, args.status))


def cmd_sidekick(args: argparse.Namespace) -> None:
    root = conv_root(args)
    parent = resolve(root, args.parent)
    raw: dict[str, Any] = {
        "topic": args.topic,
        "status": "active",
        "tags": list(parent.meta.get("tags", [])),
        "refs": [{"id": parent.id, "rel": "spawned-from"}],
        "sections": seed_branch_sections(parent, topic=args.topic, rel="spawned-from"),
    }
    if args.new_id:
        raw["id"] = args.new_id
    result = upsert(root, raw, create_only=True)
    parent_status = None if args.keep_parent_active else set_status(root, parent.id, "parked")
    print_json(
        {
            "id": result["id"],
            "file": result["file"],
            "parent": parent.id,
            "status": "active",
            "parent_status": parent_status,
            "ref_changes": result["ref_changes"],
            "index_records": parent_status["index_records"] if parent_status else result["index_records"],
            "changed": result["changed"],
        }
    )


def cmd_continue(args: argparse.Namespace) -> None:
    root = conv_root(args)
    parent = resolve(root, args.parent)
    topic = args.topic or f"{parent.meta.get('topic', parent.id)} continued"
    raw: dict[str, Any] = {
        "topic": topic,
        "status": "active",
        "tags": list(parent.meta.get("tags", [])),
        "refs": [{"id": parent.id, "rel": "continued-from"}],
        "sections": seed_branch_sections(parent, topic=topic, rel="continued-from"),
    }
    if args.new_id:
        raw["id"] = args.new_id
    result = upsert(root, raw, create_only=True)
    parent_status = set_status(root, parent.id, "parked")
    print_json(
        {
            "id": result["id"],
            "file": result["file"],
            "parent": parent.id,
            "status": "active",
            "parent_status": parent_status,
            "ref_changes": result["ref_changes"],
            "index_records": parent_status["index_records"],
            "changed": result["changed"],
        }
    )


def cmd_return(args: argparse.Namespace) -> None:
    digest = args.digest.strip()
    if not digest:
        raise ConvError("--digest must not be empty")
    root = conv_root(args)
    branch = resolve(root, args.branch)
    parent_id = parent_ref_id(branch, args.parent)
    if not find_by_id(root, parent_id):
        raise ConvError(f"branch parent not found: {parent_id}")
    duplicate_sections = duplicate_section_names(branch.body)
    if duplicate_sections:
        raise ConvError(f"branch body has duplicate section(s): {', '.join(duplicate_sections)}")
    sections = sections_from_body(branch.body)
    missing = [name for name in MANDATORY_SECTIONS if name not in sections]
    if missing:
        raise ConvError(f"branch body missing mandatory sections: {', '.join(missing)}")
    digest_changed = sections.get("digest", "") != digest
    sections["digest"] = digest
    body = render_canonical_body(sections)
    meta = dict(branch.meta)
    status_changed = meta.get("status") != "closed"
    changed = False
    if digest_changed or status_changed or branch.body.strip() + "\n" != body:
        meta["status"] = "closed"
        meta["updated"] = now_utc()
        changed = write_conv(branch.path, meta, body)
    ref_changes = regen_refs(root)
    records = rebuild_index(root)
    print_json(
        {
            "id": branch.id,
            "parent": parent_id,
            "status": "closed",
            "digest_changed": digest_changed,
            "changed": changed,
            "ref_changes": ref_changes,
            "index_records": len(records),
        }
    )


def cmd_list(args: argparse.Namespace) -> None:
    validate_limit(args.limit)
    root = conv_root(args)
    records = read_index(root, tolerate_malformed=True)
    if not records:
        records = rebuild_index(root)
    order = {"active": 0, "parked": 1, "closed": 2}
    if args.status:
        records = [record for record in records if record.get("status") == args.status]
    records.sort(key=lambda record: str(record.get("updated", "")), reverse=True)
    records.sort(key=lambda record: order.get(record.get("status"), 9))
    records = records[: args.limit]
    if args.json:
        print_json(records)
    else:
        print_table(records)


def cmd_search(args: argparse.Namespace) -> None:
    hits = search(conv_root(args), args.query, limit=args.limit)
    print_json(hits)


def cmd_show(args: argparse.Namespace) -> None:
    root = conv_root(args)
    conv = resolve(root, args.target)
    if args.markdown:
        print(conv.path.read_text(encoding="utf-8"))
        return
    print_json({**index_record(root, conv), "body": conv.body})


def missing_always_sections(body: str) -> list[str]:
    """Resumption sections absent from a record body (legacy records predate them)."""
    present = sections_from_body(body)
    return [name for name in ALWAYS_SECTIONS if name not in present]


def run_installer_repair(root: Path) -> dict[str, Any]:
    import subprocess

    script_dir = Path(__file__).resolve().parent
    install_py = script_dir / "install.py"
    source = script_dir.parent
    command = [
        sys.executable,
        str(install_py),
        "--source",
        str(source),
        "--target",
        str(root),
        "--doctor-fix",
    ]
    report: dict[str, Any] = {
        "available": install_py.is_file(),
        "command": command,
        "returncode": None,
        "stdout": [],
        "stderr": [],
    }
    if not report["available"]:
        report["reason"] = "scripts/install.py not found next to conv_cli.py; reinstall conversate from a complete checkout to restore installer repair"
        return report
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        report["reason"] = "installer repair timed out"
        report["stdout"] = (exc.stdout or "").splitlines()
        report["stderr"] = (exc.stderr or "").splitlines()
        return report
    except OSError as exc:
        report["reason"] = str(exc)
        return report
    report["returncode"] = proc.returncode
    report["stdout"] = proc.stdout.splitlines()
    report["stderr"] = proc.stderr.splitlines()
    return report


def cmd_doctor(args: argparse.Namespace) -> None:
    import shutil

    res = resolve_conv_root(args)
    tools = {name: bool(shutil.which(name)) for name in ("rg", "fff", "semble", "uvx", "python")}
    semantic = (
        "semble"
        if tools["semble"]
        else ("uvx semble (set CONV_USE_UVX_SEMBLE=1)" if tools["uvx"] else "body fallback")
    )
    if res.root is None:
        print_json(
            {
                "plugin_installation_root": None,
                "conversation_database": None,
                "deprecated": {"aliases": deprecated_aliases(None)},
                "resolution": resolution_report(res),
                "layout": {"convs": False, "index": False, "semble_cache": False, "gitignore": False},
                "tools": tools,
                "semantic_search": semantic,
                "parse_errors": [],
                "warnings": [],
                "records": 0,
            }
        )
        return
    root = res.root
    layout_before = {
        "convs": convs_dir(root).exists(),
        "index": index_path(root).exists(),
        "semble_cache": (root / ".semble").exists(),
    }
    ensure_layout(root)
    fix = {
        "enabled": bool(args.fix),
        "layout": any(not value for value in layout_before.values()) if args.fix else False,
        "gitignore": repair_gitignore(root) if args.fix else False,
        "canonical_records": [],
        "ref_changes": 0,
        "index_records": None,
    }
    parse_errors = []
    warnings = []
    records = 0
    for path in sorted(convs_dir(root).glob("*.md")):
        try:
            conv = read_conv(path)
        except Exception as exc:
            parse_errors.append({"file": str(path), "error": str(exc)})
            continue
        duplicate_sections = duplicate_section_names(conv.body)
        if duplicate_sections:
            warnings.append(
                {
                    "file": str(path),
                    "duplicate_sections": duplicate_sections,
                }
            )
        if args.fix and not duplicate_sections and canonicalize_conversation_body(conv):
            fix["canonical_records"].append(str(path.relative_to(root)).replace("\\", "/"))
            conv = read_conv(path)
        records += 1
        missing = missing_always_sections(conv.body)
        if missing:
            warnings.append({"file": str(path), "missing_sections": missing})
    if args.fix:
        fix["ref_changes"] = regen_refs(root)
        fix["index_records"] = len(rebuild_index(root, tolerate_parse_errors=True))
        fix["installer_repair"] = run_installer_repair(root)
        if not fix["installer_repair"].get("available"):
            warnings.append(
                {
                    "installer_repair": "unavailable",
                    "reason": fix["installer_repair"].get("reason"),
                }
            )
        elif fix["installer_repair"].get("returncode") != 0:
            warnings.append(
                {
                    "installer_repair": "failed",
                    "returncode": fix["installer_repair"].get("returncode"),
                    "stderr": fix["installer_repair"].get("stderr", []),
                }
            )
    health = index_health(root)
    print_json(
        {
            "plugin_installation_root": str(root),
            "conversation_database": str(convs_dir(root)),
            "deprecated": {"aliases": deprecated_aliases(root)},
            "resolution": resolution_report(res),
            "layout": {
                "convs": convs_dir(root).exists(),
                "index": index_path(root).exists(),
                "semble_cache": (root / ".semble").exists(),
                "gitignore": gitignore_path(root).exists(),
            },
            "tools": tools,
            "semantic_search": semantic,
            "parse_errors": parse_errors,
            "warnings": warnings,
            "records": records,
            "index_health": health,
            "fix": fix,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    # --conv-root is shared so it is accepted both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--conv-root",
        default=argparse.SUPPRESS,  # absent when unset, so a value before the subcommand is not clobbered
        help="compatibility override for the Plugin installation root; default is ~/.conversate",
    )

    parser = argparse.ArgumentParser(
        description="conv Conversation database helper; defaults to the Plugin installation root at ~/.conversate",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "init",
        parents=[common],
        help="create the Plugin installation root and Conversation database, then rebuild index",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("rebuild-index", parents=[common], help="rebuild index.jsonl from the Conversation database")
    p.set_defaults(func=cmd_rebuild_index)

    p = sub.add_parser("regen-refs", parents=[common], help="reconcile bidirectional refs and rebuild index")
    p.set_defaults(func=cmd_regen_refs)

    p = sub.add_parser("upsert", parents=[common], help="create or replace a conversation markdown file")
    p.add_argument("--stdin", action="store_true", help="read conversation JSON from stdin")
    p.add_argument("--json", help="read conversation JSON from this file")
    p.add_argument("--status", choices=sorted(STATUSES), help="override status")
    p.set_defaults(func=cmd_upsert)

    p = sub.add_parser("set-status", parents=[common], help="set a conversation status")
    p.add_argument("id")
    p.add_argument("status", choices=sorted(STATUSES))
    p.set_defaults(func=cmd_set_status)

    p = sub.add_parser("sidekick", parents=[common], help="create an active sidekick branch in the Conversation database")
    p.add_argument("parent", help="parent conversation id or search query")
    p.add_argument("topic", help="topic for the sidekick branch")
    p.add_argument("--id", dest="new_id", help="explicit id for deterministic branch writes")
    p.add_argument("--keep-parent-active", action="store_true", help="do not park the parent conversation")
    p.set_defaults(func=cmd_sidekick)

    p = sub.add_parser("continue", parents=[common], help="park a conversation and continue it in a fresh record")
    p.add_argument("parent", help="parent conversation id or search query")
    p.add_argument("--topic", help="topic for the continuation record")
    p.add_argument("--id", dest="new_id", help="explicit id for deterministic continuation writes")
    p.set_defaults(func=cmd_continue)

    p = sub.add_parser("return", parents=[common], help="close a branch with a digest")
    p.add_argument("branch", help="branch conversation id or search query")
    p.add_argument("--digest", required=True, help="deterministic digest to write into the branch record")
    p.add_argument("--parent", help="explicit parent id when branch refs are ambiguous")
    p.set_defaults(func=cmd_return)

    p = sub.add_parser("list", parents=[common], help="list conversations from the Conversation database index")
    p.add_argument("--status", choices=sorted(STATUSES))
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("search", parents=[common], help="tiered search: filename, index fields, body fallback")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("show", parents=[common], help="show one conversation by id or search query")
    p.add_argument("target")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("doctor", parents=[common], help="validate layout, parseability, and optional tools")
    p.add_argument("--fix", action="store_true", help="repair layout, gitignore, refs, index, and canonical record rendering")
    p.set_defaults(func=cmd_doctor)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
        return 0
    except ConvError as exc:
        print(f"conv: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
