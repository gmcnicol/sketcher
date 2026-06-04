import hashlib
import sqlite3
import xml.etree.ElementTree as ET
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from sketcher_model_builder.api import create_app
from sketcher_model_builder.workspace import ensure_workspace


VALID_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
INKSCAPE_SVG = b"""\
<svg
  xmlns="http://www.w3.org/2000/svg"
  xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
  xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
  width="100mm"
  height="100mm"
  viewBox="0 0 100 100"
  inkscape:version="1.4"
  sodipodi:docname="source.svg">
  <metadata>editor metadata</metadata>
  <sodipodi:namedview inkscape:pageopacity="0" />
  <g inkscape:label="Layer 1" inkscape:groupmode="layer" transform="translate(10 20)">
    <path d="M 1 2 L 5 6" style="fill:none;stroke:#000;stroke-width:2" />
  </g>
</svg>
"""

LONG_TRACED_STROKE_SVG = b"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 40">
  <path id="trace" style="fill:none;stroke:#000;stroke-width:1"
    d="M 0 20 C 20 0 40 40 60 20 C 80 0 100 40 120 20 C 140 0 160 40 180 20 C 195 10 205 30 220 20" />
</svg>
"""


def row_count(workspace: Path, table: str) -> int:
    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_workspace_initialization_creates_directories_and_database(tmp_path: Path) -> None:
    workspace = ensure_workspace(tmp_path / "workspace")

    assert (workspace / "sketcher.sqlite3").exists()
    assert (workspace / "artifacts" / "sources").is_dir()
    assert (workspace / "artifacts" / "candidates").is_dir()
    assert (workspace / "models").is_dir()

    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        schema_version = db.execute(
            "SELECT value FROM workspace_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert schema_version == "3"


def test_valid_svg_upload_creates_source_run_and_artifact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.post(
        "/sources",
        files={"file": ("example.svg", VALID_SVG, "image/svg+xml")},
    )

    assert response.status_code == 201
    body = response.json()
    source = body["source"]
    run = body["run"]

    assert uuid.UUID(source["id"]).version == 7
    assert uuid.UUID(run["id"]).version == 7
    assert run == {
        "id": run["id"],
        "sourceId": source["id"],
        "status": "active",
    }
    assert source["filename"] == "example.svg"
    assert source["artifactPath"].startswith("artifacts/sources/")
    assert not Path(source["artifactPath"]).is_absolute()

    artifact = workspace / source["artifactPath"]
    artifact_bytes = artifact.read_bytes()
    assert source["byteSize"] == len(artifact_bytes)
    assert source["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()

    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        db.row_factory = sqlite3.Row
        source_row = db.execute("SELECT * FROM sources").fetchone()
        run_row = db.execute("SELECT * FROM runs").fetchone()

    assert source_row["id"] == source["id"]
    assert source_row["sha256"] == source["sha256"]
    assert source_row["artifact_path"] == source["artifactPath"]
    assert run_row["source_id"] == source["id"]
    assert run_row["status"] == "active"


def test_svg_upload_strips_inkscape_page_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.post(
        "/sources",
        files={"file": ("example.svg", INKSCAPE_SVG, "image/svg+xml")},
    )

    assert response.status_code == 201
    source = response.json()["source"]
    artifact_text = (workspace / source["artifactPath"]).read_text(encoding="utf-8")

    assert "inkscape" not in artifact_text
    assert "sodipodi" not in artifact_text
    assert "metadata" not in artifact_text
    assert "width=" not in artifact_text
    assert "height=" not in artifact_text
    assert 'viewBox="10 21 6 6"' in artifact_text
    assert source["sha256"] == hashlib.sha256(artifact_text.encode("utf-8")).hexdigest()


def test_svg_upload_splits_long_traced_strokes_before_storing_source(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.post(
        "/sources",
        files={"file": ("trace.svg", LONG_TRACED_STROKE_SVG, "image/svg+xml")},
    )

    assert response.status_code == 201
    source = response.json()["source"]
    artifact_path = workspace / source["artifactPath"]
    artifact_bytes = artifact_path.read_bytes()
    root = ET.fromstring(artifact_bytes)
    substrokes = [
        element
        for element in root.iter()
        if element.get("data-sketcher-substroke") is not None
    ]

    assert len(substrokes) > 1
    assert all(element.get("d", "").startswith("M ") for element in substrokes)
    assert all(" Q " in element.get("d", "") for element in substrokes)
    assert source["byteSize"] == len(artifact_bytes)
    assert source["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()


def test_invalid_content_type_creates_no_records_or_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.post(
        "/sources",
        files={"file": ("example.svg", VALID_SVG, "text/plain")},
    )

    assert response.status_code == 400
    assert row_count(workspace, "sources") == 0
    assert row_count(workspace, "runs") == 0
    assert list((workspace / "artifacts" / "sources").iterdir()) == []


def test_non_svg_body_creates_no_records_or_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.post(
        "/sources",
        files={"file": ("example.svg", b"not svg", "image/svg+xml")},
    )

    assert response.status_code == 400
    assert row_count(workspace, "sources") == 0
    assert row_count(workspace, "runs") == 0
    assert list((workspace / "artifacts" / "sources").iterdir()) == []


def test_health_returns_workspace_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "workspacePath": str(workspace.resolve()),
    }
