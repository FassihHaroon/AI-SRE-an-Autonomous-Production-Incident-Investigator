"""Load and parse knowledge sources into :class:`Document` objects with normalised metadata.

Supported inputs: Markdown (YAML front matter), plain text (``key: value`` header lines), JSON
incident records, PDF (text layer + document info). Metadata not given explicitly is derived
from the folder (``incidents/`` → incident, ``runbooks/`` → runbook, …) and the content.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_SUFFIXES = (".md", ".markdown", ".txt", ".json", ".pdf")
DOC_TYPES = (
    "incident",
    "root_cause_report",
    "runbook",
    "troubleshooting",
    "service_architecture",
    "architecture",
    "procedure",
    "operational",
)
_FOLDER_TYPES = {
    "incidents": "incident",
    "postmortems": "incident",
    "rca": "root_cause_report",
    "runbooks": "runbook",
    "troubleshooting": "troubleshooting",
    "services": "service_architecture",
    "architecture": "architecture",
    "procedures": "procedure",
    "operations": "operational",
}
_HEADER = re.compile(r"^([a-z_]+):\s*(.+)$")


class DocumentError(ValueError):
    pass


@dataclass
class Document:
    id: str
    source: str  # path relative to the knowledge root (POSIX)
    doc_type: str
    title: str
    body: str  # Markdown-ish text (headings preserved where the format has them)
    services: list[str] = field(default_factory=list)
    environment: str | None = None
    incident_id: str | None = None
    severity: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    timestamp: datetime | None = None
    supersedes: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        meta = json.dumps(
            [
                self.id,
                self.doc_type,
                self.title,
                self.services,
                self.environment,
                self.incident_id,
                self.severity,
                self.category,
                self.tags,
                str(self.timestamp),
                self.supersedes,
            ],
            sort_keys=True,
        )
        return hashlib.sha256((meta + "\n" + self.body).encode()).hexdigest()


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value]


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _first_heading(body: str) -> str | None:
    match = re.search(r"^#\s+(.+)$", body, re.M)
    return match.group(1).strip() if match else None


def _build(meta: dict[str, Any], body: str, rel: str, folder_type: str | None) -> Document:
    doc_type = str(meta.get("type") or meta.get("doc_type") or folder_type or "operational")
    if doc_type not in DOC_TYPES:
        raise DocumentError(
            f"{rel}: unknown document type {doc_type!r} (valid: {', '.join(DOC_TYPES)})"
        )
    incident_id = meta.get("incident_id") or (meta.get("id") if doc_type == "incident" else None)
    doc_id = str(meta.get("id") or incident_id or _slug(Path(rel).with_suffix("").as_posix()))
    title = str(meta.get("title") or _first_heading(body) or Path(rel).stem)
    known = {
        "id",
        "type",
        "doc_type",
        "title",
        "services",
        "service",
        "environment",
        "incident_id",
        "severity",
        "category",
        "tags",
        "date",
        "timestamp",
        "supersedes",
    }
    return Document(
        id=doc_id,
        source=rel,
        doc_type=doc_type,
        title=title,
        body=body.strip(),
        services=_as_list(meta.get("services") or meta.get("service")),
        environment=meta.get("environment"),
        incident_id=str(incident_id) if incident_id else None,
        severity=meta.get("severity"),
        category=meta.get("category"),
        tags=_as_list(meta.get("tags")),
        timestamp=_as_datetime(meta.get("timestamp") or meta.get("date")),
        supersedes=meta.get("supersedes"),
        extra={k: v for k, v in meta.items() if k not in known},
    )


def parse_markdown(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            meta = yaml.safe_load(parts[1]) or {}
            if not isinstance(meta, dict):
                raise DocumentError("front matter must be a mapping")
            return meta, parts[2]
    return {}, text


def parse_text(text: str) -> tuple[dict[str, Any], str]:
    """Plain text: optional `key: value` lines after an optional title line, then a blank line."""
    lines = text.splitlines()
    meta: dict[str, Any] = {}
    title = lines[0].strip() if lines and not _HEADER.match(lines[0].strip()) else None
    i = 1 if title else 0
    while i < len(lines) and (m := _HEADER.match(lines[i].strip())):
        meta[m.group(1)] = m.group(2).strip()
        i += 1
    if title:
        meta.setdefault("title", title)
    body = "\n".join(lines[i:]).strip()
    return meta, (f"# {title}\n\n{body}" if title else body)


def parse_incident_json(text: str) -> tuple[dict[str, Any], str]:
    record = json.loads(text)
    if not isinstance(record, dict) or not record.get("incident_id"):
        raise DocumentError("JSON incident records need an object with `incident_id`")
    meta = {**record, "type": record.get("type", "incident")}
    sections = [f"# {record['incident_id']}: {record.get('title', '')}"]
    for key, heading in (
        ("summary", "Summary"),
        ("root_cause", "Root cause"),
        ("detection", "Detection"),
        ("resolution", "Resolution"),
        ("lessons", "Lessons learned"),
    ):
        if record.get(key):
            sections.append(f"## {heading}\n\n{record[key]}")
    return meta, "\n\n".join(sections)


def parse_pdf(path: Path) -> tuple[dict[str, Any], str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    info: Any = reader.metadata or {}
    meta: dict[str, Any] = {}
    if info.get("/Title"):
        meta["title"] = str(info["/Title"])
    subject = str(info.get("/Subject") or "")
    if m := re.search(r"services:\s*([\w\-, ]+)", subject):
        meta["services"] = m.group(1)
    pages = [p.extract_text() or "" for p in reader.pages]
    body = "\n\n".join(f"## Page {i}\n\n{t.strip()}" for i, t in enumerate(pages, 1) if t.strip())
    if not body:
        raise DocumentError(f"{path.name}: no extractable text (scanned PDF?)")
    return meta, (f"# {meta['title']}\n\n{body}" if meta.get("title") else body)


def load_document(path: Path, root: Path) -> Document:
    rel = path.relative_to(root).as_posix()
    folder_type = (
        _FOLDER_TYPES.get(path.relative_to(root).parts[0])
        if len(path.relative_to(root).parts) > 1
        else None
    )
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentError(f"{rel}: unsupported file type {suffix}")
    try:
        if suffix == ".pdf":
            meta, body = parse_pdf(path)
        else:
            text = path.read_text(encoding="utf-8")
            if suffix == ".json":
                meta, body = parse_incident_json(text)
            elif suffix == ".txt":
                meta, body = parse_text(text)
            else:
                meta, body = parse_markdown(text)
    except (yaml.YAMLError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DocumentError(f"{rel}: cannot parse: {exc}") from exc
    if not body.strip():
        raise DocumentError(f"{rel}: empty document")
    return _build(meta, body, rel, folder_type)


def discover(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
