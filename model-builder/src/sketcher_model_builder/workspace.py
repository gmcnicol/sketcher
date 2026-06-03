"""Workspace initialization and source upload persistence."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import uuid_utils


SCHEMA_VERSION = 1
ALLOWED_SVG_CONTENT_TYPES = {"image/svg+xml"}


class UploadValidationError(ValueError):
    """Raised when an uploaded source is not an acceptable SVG."""


@dataclass(frozen=True)
class SourceRecord:
    id: str
    filename: str
    content_type: str
    byte_size: int
    sha256: str
    artifact_path: str
    created_at: str


@dataclass(frozen=True)
class RunRecord:
    id: str
    source_id: str
    status: str
    created_at: str


@dataclass(frozen=True)
class UploadResult:
    source: SourceRecord
    run: RunRecord


def default_workspace_path() -> Path:
    configured = os.environ.get("SKETCHER_WORKSPACE")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[3] / ".sketcher").resolve()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_uuid7() -> str:
    return str(uuid_utils.uuid7())


def ensure_workspace(workspace: Path) -> Path:
    workspace = workspace.expanduser().resolve()
    for directory in (
        workspace / "artifacts" / "sources",
        workspace / "artifacts" / "candidates",
        workspace / "models",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    with connect(workspace) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY,
                original_filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                artifact_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES sources(id)
            )
            """
        )
        db.execute(
            """
            INSERT INTO workspace_meta (key, value)
            VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(SCHEMA_VERSION),),
        )
        db.execute(
            """
            INSERT INTO workspace_meta (key, value)
            VALUES ('initialized_at', ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (utc_now(),),
        )
    return workspace


def connect(workspace: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(workspace / "sketcher.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def validate_svg_upload(filename: str, content_type: str | None, data: bytes) -> None:
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_content_type not in ALLOWED_SVG_CONTENT_TYPES:
        raise UploadValidationError("Upload must use the image/svg+xml content type.")
    if not filename.lower().endswith(".svg"):
        raise UploadValidationError("Upload filename must end with .svg.")
    if not data.strip():
        raise UploadValidationError("Uploaded SVG cannot be empty.")

    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise UploadValidationError("Uploaded file must be valid SVG XML.") from error

    local_name = root.tag.rsplit("}", 1)[-1].lower()
    if local_name != "svg":
        raise UploadValidationError("Uploaded XML document must have an <svg> root.")


def store_source_upload(
    workspace: Path,
    *,
    filename: str,
    content_type: str,
    data: bytes,
) -> UploadResult:
    validate_svg_upload(filename, content_type, data)

    workspace = ensure_workspace(workspace)
    source_id = new_uuid7()
    run_id = new_uuid7()
    created_at = utc_now()
    sha256 = hashlib.sha256(data).hexdigest()
    artifact_path = Path("artifacts") / "sources" / f"{sha256[:12]}-{sanitize_filename(filename)}"
    absolute_artifact_path = workspace / artifact_path
    absolute_artifact_path.parent.mkdir(parents=True, exist_ok=True)

    absolute_artifact_path.write_bytes(data)

    source = SourceRecord(
        id=source_id,
        filename=filename,
        content_type=content_type,
        byte_size=len(data),
        sha256=sha256,
        artifact_path=artifact_path.as_posix(),
        created_at=created_at,
    )
    run = RunRecord(
        id=run_id,
        source_id=source_id,
        status="active",
        created_at=created_at,
    )

    with connect(workspace) as db:
        db.execute(
            """
            INSERT INTO sources (
                id,
                original_filename,
                content_type,
                byte_size,
                sha256,
                artifact_path,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.id,
                source.filename,
                source.content_type,
                source.byte_size,
                source.sha256,
                source.artifact_path,
                source.created_at,
            ),
        )
        db.execute(
            """
            INSERT INTO runs (id, source_id, status, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (run.id, run.source_id, run.status, run.created_at),
        )

    return UploadResult(source=source, run=run)


def sanitize_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "source.svg"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
    safe = safe.strip(".-")
    return safe or "source.svg"
