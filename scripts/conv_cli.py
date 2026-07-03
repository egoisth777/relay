#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
MANDATORY_SECTIONS = ("summary", "dict", "qa")
# Resumption sections: always written (as "(none)" when empty) but never hard-fail upsert.
ALWAYS_SECTIONS = ("resume", "user-instructions", "condensed-transcript")
SECTION_ORDER = (
    "summary",
    "dict",
    "qa",
    "resume",
    "user-instructions",
    "condensed-transcript",
    "sources",
    "insights",
    "decisions",
    "digest",
)
NONE_PLACEHOLDER = "(none)"


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


SENTINEL = ".conv-root"


@dataclass
class Resolution:
    root: Path | None
    layer: str  # "flag" | "env-conversate" | "env-brain" | "marker" | "none"
    marker: Path | None = None


def _ancestor_dirs(*starts: Path):
    """Yield each start dir and its ancestors, nearest-first, in the order of `starts`
    and de-duplicated across them."""
    seen: set[Path] = set()
    for start in starts:
        start = start.resolve()
        for d in (start, *start.parents):
            if d not in seen:
                seen.add(d)
                yield d
            if (d / ".git").exists():
                break  # do not resolve a store from outside the enclosing repo


def find_marker(cwd: Path, script_dir: Path) -> tuple[Path, Path] | None:
    """Search cwd's ancestors, then the script dir's ancestors (nearest-first) for a
    store marker. Precedence within each dir:
      - the dir is itself named `.conversate`  -> that dir IS the root (cwd inside it)
      - a `.conv-root` sentinel file           -> that dir IS the root
      - a `.conversate/` subdirectory          -> `<dir>/.conversate` is the root
      - (legacy) a `conv/` subdirectory        -> `<dir>/conv` is the root
    Returns (root, marker_dir) or None."""
    for d in _ancestor_dirs(cwd, script_dir):
        if d.name == ".conversate":
            return d, d
        if (d / SENTINEL).is_file():
            return d, d
        if (d / ".conversate").is_dir():
            return d / ".conversate", d
        if (d / "conv").is_dir():
            return d / "conv", d
    return None


def resolve_conv_root(args: argparse.Namespace | None = None) -> Resolution:
    """Resolve the store root by layer: --conv-root flag, then $CONVERSATE_ROOT, then the
    legacy $BRAIN_CONV, then a marker search from the cwd and the script dir. Never guesses
    by path arithmetic."""
    explicit = getattr(args, "conv_root", None) if args else None
    if explicit:
        return Resolution(Path(explicit).expanduser().resolve(), "flag")
    env = os.environ.get("CONVERSATE_ROOT")
    if env:
        return Resolution(Path(env).expanduser().resolve(), "env-conversate")
    env = os.environ.get("BRAIN_CONV")
    if env:
        return Resolution(Path(env).expanduser().resolve(), "env-brain")
    found = find_marker(Path.cwd(), Path(__file__).resolve().parent)
    if found:
        root, marker = found
        return Resolution(root.resolve(), "marker", marker.resolve())
    return Resolution(None, "none")


def _root_or_raise(res: Resolution) -> Path:
    if res.root is None:
        raise ConvError(
            "cannot resolve a conv store root: pass --conv-root PATH, set $CONVERSATE_ROOT "
            "(or legacy $BRAIN_CONV), or create a .conversate/ directory (or a .conv-root "
            "marker) in the working directory or an ancestor; run `init` to create one here"
        )
    return res.root


def conv_root(args: argparse.Namespace | None = None) -> Path:
    return _root_or_raise(resolve_conv_root(args))


def resolution_report(res: Resolution) -> dict[str, Any]:
    return {"layer": res.layer, "marker": str(res.marker) if res.marker else None}


def write_sentinel(root: Path) -> None:
    sentinel = root / SENTINEL
    if not sentinel.exists():
        sentinel.write_text("# conv store root marker (resolved by scripts/conv_cli.py)\n", encoding="utf-8")


def convs_dir(root: Path) -> Path:
    return root / "convs"


def index_path(root: Path) -> Path:
    return root / "index.jsonl"


def gitignore_path(root: Path) -> Path:
    return root / ".gitignore"


def ensure_layout(root: Path) -> None:
    convs_dir(root).mkdir(parents=True, exist_ok=True)
    (root / ".semble").mkdir(parents=True, exist_ok=True)
    index_path(root).touch(exist_ok=True)


def write_gitignore(root: Path) -> None:
    """Ignore derived/cache artifacts inside the store; conversation records stay trackable."""
    path = gitignore_path(root)
    if not path.exists():
        path.write_text(".semble/\nindex.jsonl\n__pycache__/\n", encoding="utf-8", newline="\n")


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
    rid = str(ref.get("id", "")).strip()
    rel = str(ref.get("rel", "")).strip()
    if not rid or not rel:
        raise ConvError(f"invalid ref: {ref!r}")
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
    cid = str(raw.get("id", existing.get("id") if existing else "")).strip()
    if not cid:
        cid = make_unique_id(topic, root if root is not None else conv_root())
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
    meta = tomllib.loads(front)
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


def sections_from_body(body: str) -> dict[str, str]:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
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
    """Render the structured `resume` payload into markdown bullets; empty -> ""."""
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
    """Rendered text for the always-on resumption sections, from structured payload keys."""
    return {
        "resume": render_resume(raw.get("resume")),
        "user-instructions": render_user_instructions(raw.get("user_instructions")),
        "condensed-transcript": render_condensed_transcript(raw.get("condensed_transcript")),
    }


def build_body(raw: dict[str, Any]) -> str:
    always = resumption_sections(raw)
    if raw.get("body"):
        body = str(raw["body"]).strip() + "\n"
        present = sections_from_body(body)
        missing = [name for name in MANDATORY_SECTIONS if name not in present]
        if missing:
            raise ConvError(f"body missing mandatory sections: {', '.join(missing)}")
        # Guarantee the resumption sections exist even when a raw body omits them.
        extra = [
            f"## {name}\n{always.get(name) or NONE_PLACEHOLDER}\n"
            for name in ALWAYS_SECTIONS
            if name not in present
        ]
        if extra:
            body = body.rstrip() + "\n\n" + "\n".join(extra)
            body = body.rstrip() + "\n"
        return body

    sections = raw.get("sections")
    if not isinstance(sections, dict):
        raise ConvError("sections object is required when body is not provided")

    missing = [name for name in MANDATORY_SECTIONS if not normalize_section_value(sections.get(name))]
    if missing:
        raise ConvError(f"missing mandatory sections: {', '.join(missing)}")

    parts: list[str] = []
    emitted: set[str] = set()
    for name in SECTION_ORDER:
        if name in ALWAYS_SECTIONS:
            content = always.get(name) or normalize_section_value(sections.get(name)) or NONE_PLACEHOLDER
        else:
            content = normalize_section_value(sections.get(name))
            if not content:
                continue
        parts.append(f"## {name}\n{content.strip()}\n")
        emitted.add(name)
    for name in sorted(sections):
        if name in emitted:
            continue
        content = normalize_section_value(sections.get(name))
        if content:
            parts.append(f"## {name}\n{content.strip()}\n")
    return "\n".join(parts).rstrip() + "\n"


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


def rebuild_index(root: Path) -> list[dict[str, Any]]:
    ensure_layout(root)
    records = [index_record(root, conv) for conv in read_all(root)]
    records.sort(key=lambda item: item["id"])
    text = "".join(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n" for record in records)
    index_path(root).write_text(text, encoding="utf-8", newline="\n")
    return records


def read_index(root: Path) -> list[dict[str, Any]]:
    ensure_layout(root)
    records: list[dict[str, Any]] = []
    for line in index_path(root).read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def upsert(root: Path, raw: dict[str, Any], *, status_override: str | None = None) -> dict[str, Any]:
    ensure_layout(root)
    existing = find_by_id(root, str(raw.get("id", ""))) if raw.get("id") else None
    existing_meta = existing.meta if existing else None
    if not raw.get("id"):
        raw = {**raw, "id": make_unique_id(str(raw.get("topic", "")), root)}
    if status_override:
        raw = {**raw, "status": status_override}
    meta = normalize_meta(raw, existing=existing_meta, root=root)
    body = build_body(raw)
    path = convs_dir(root) / file_name_for_id(meta["id"])
    changed = write_conv(path, meta, body)
    ref_changes = regen_refs(root)
    records = rebuild_index(root)
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


def regen_refs(root: Path) -> int:
    convs = read_all(root, tolerate=True)
    by_id = {conv.id: conv for conv in convs}
    desired: dict[str, set[tuple[str, str]]] = {
        conv.id: {(ref["id"], ref["rel"]) for ref in normalize_refs(conv.meta.get("refs", []))}
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
    ensure_layout(root)
    records = read_index(root)
    if not records:
        rebuild_index(root)
        records = read_index(root)
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
    if len(hits) != 1:
        raise ConvError(f"ambiguous or missing target {target!r}: {[hit['id'] for hit in hits]}")
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


def cmd_init(args: argparse.Namespace) -> None:
    res = resolve_conv_root(args)
    # init is the sole command that may create a store: with nothing to resolve it
    # bootstraps <cwd>/.conversate rather than failing loud.
    root = res.root if res.root is not None else (Path.cwd() / ".conversate").resolve()
    ensure_layout(root)
    write_sentinel(root)
    write_gitignore(root)
    records = rebuild_index(root)
    print_json(
        {
            "conv_root": str(root),
            "convs": str(convs_dir(root)),
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
        raw = json.loads(sys.stdin.read())
    elif args.json:
        raw = json.loads(Path(args.json).read_text(encoding="utf-8"))
    else:
        raise ConvError("upsert requires --stdin or --json PATH")
    print_json(upsert(conv_root(args), raw, status_override=args.status))


def cmd_set_status(args: argparse.Namespace) -> None:
    print_json(set_status(conv_root(args), args.id, args.status))


def cmd_list(args: argparse.Namespace) -> None:
    records = read_index(conv_root(args))
    if not records:
        records = rebuild_index(conv_root(args))
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


def cmd_doctor(args: argparse.Namespace) -> None:
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
                "conv_root": None,
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
    ensure_layout(root)
    parse_errors = []
    warnings = []
    for path in sorted(convs_dir(root).glob("*.md")):
        try:
            conv = read_conv(path)
        except Exception as exc:
            parse_errors.append({"file": str(path), "error": str(exc)})
            continue
        missing = missing_always_sections(conv.body)
        if missing:
            warnings.append({"file": str(path), "missing_sections": missing})
    print_json(
        {
            "conv_root": str(root),
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
            "records": len(read_index(root)),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    # --conv-root is shared so it is accepted both before and after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--conv-root",
        default=argparse.SUPPRESS,  # absent when unset, so a value before the subcommand is not clobbered
        help="path to the conv store root; otherwise $CONVERSATE_ROOT, $BRAIN_CONV, then a "
        ".conversate / .conv-root marker search",
    )

    parser = argparse.ArgumentParser(description="conv conversation store helper", parents=[common])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", parents=[common], help="create the .conversate layout and rebuild index")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("rebuild-index", parents=[common], help="rebuild index.jsonl from convs/*.md")
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

    p = sub.add_parser("list", parents=[common], help="list conversations from index.jsonl")
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
