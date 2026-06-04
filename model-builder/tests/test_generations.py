import hashlib
import json
import random
import sqlite3
import threading
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sketcher_model_builder import generations
from sketcher_model_builder.api import create_app


VALID_SOURCE_SVG = b"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path id="square" d="M 1 1 H 9 V 9 H 1 Z" />
</svg>
"""

TOM2_SOURCE_SVG = (Path(__file__).parent / "fixtures" / "tom2.svg").read_bytes()
TINY_PATH_SOURCE_SVG = b"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path id="tiny-mark" d="M 4.9 5 L 5.1 5.1" style="fill:none;stroke:#000;stroke-width:0.1" />
</svg>
"""
FILLED_BASIC_SHAPE_SOURCE_SVG = b"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <rect id="filled-box" x="1" y="1" width="8" height="8" fill="#000" />
</svg>
"""
TRANSFORMED_GROUP_SOURCE_SVG = b"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">
  <g transform="translate(5 4)">
    <path id="translated-line" d="M 1 1 L 9 9" style="fill:none;stroke:#000;stroke-width:1" />
  </g>
</svg>
"""
EMPTY_SOURCE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>'
LONG_TRACED_STROKE_SVG = b"""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 220 40">
  <path id="trace" style="fill:none;stroke:#000;stroke-width:1"
    d="M 0 20 C 20 0 40 40 60 20 C 80 0 100 40 120 20 C 140 0 160 40 180 20 C 195 10 205 30 220 20" />
</svg>
"""


def upload_source(
    client: TestClient,
    svg: bytes = VALID_SOURCE_SVG,
    *,
    filename: str = "source.svg",
) -> dict:
    response = client.post(
        "/sources",
        files={"file": (filename, svg, "image/svg+xml")},
    )
    assert response.status_code == 201
    return response.json()


def fetch_rows(workspace: Path, table: str) -> list[sqlite3.Row]:
    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        db.row_factory = sqlite3.Row
        return db.execute(f"SELECT * FROM {table} ORDER BY created_at, id").fetchall()


def fetch_candidate_parent_rows(workspace: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(workspace / "sketcher.sqlite3") as db:
        db.row_factory = sqlite3.Row
        return db.execute(
            """
            SELECT * FROM candidate_parents
            ORDER BY candidate_id, parent_index
            """
        ).fetchall()


def install_fast_renderer(monkeypatch) -> None:
    def fake_render_candidate_svg(
        source_path: Path,
        artifact_path: Path,
        render_parameters: dict,
    ) -> None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        seed = render_parameters["seed"]
        artifact_path.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path d="M 1 1 L 9 9" style="fill:none;stroke:#111;stroke-width:1;stroke-opacity:1" data-sketcher-pass="{seed}" />
</svg>
""",
            encoding="utf-8",
        )

    monkeypatch.setattr(generations, "render_candidate_svg", fake_render_candidate_svg)


def write_valid_candidate_svg(artifact_path: Path, seed: object = 1) -> None:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path d="M 1 1 L 9 9" style="fill:none;stroke:#111;stroke-width:1;stroke-opacity:1" data-sketcher-pass="{seed}" />
</svg>
""",
        encoding="utf-8",
    )


def review_generation(
    client: TestClient,
    run_id: str,
    candidates: list[dict],
    *,
    survivor_count: int,
) -> None:
    for index, candidate in enumerate(candidates):
        decision = "survived" if index < survivor_count else "rejected"
        response = client.post(
            f"/runs/{run_id}/review/decisions",
            json={"candidateId": candidate["id"], "decision": decision},
        )
        assert response.status_code == 200
    assert client.get(f"/runs/{run_id}/review/current").json()["review"]["complete"]


def test_first_generation_genomes_include_flick_defaults() -> None:
    genome = generations.build_first_generation_genome("run-1", 1)
    render_parameters = genome["renderParameters"]

    assert render_parameters["flick_strength"] == 0.45
    assert render_parameters["flick_bias"] == "start"
    assert render_parameters["flick_curve"] == "ease_out"
    assert render_parameters["flick_probability"] == 0.65
    assert render_parameters["flick_min_width"] == 0.08
    assert render_parameters["flick_min_opacity"] == 0.04


def test_generation_endpoint_creates_first_generation_with_24_ready_candidates(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    upload = upload_source(client)
    run_id = upload["run"]["id"]

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 201
    generation = response.json()["generation"]
    assert uuid.UUID(generation["id"]).version == 7
    assert generation["runId"] == run_id
    assert generation["generationNumber"] == 1
    assert generation["status"] == "ready"
    assert generation["totalCandidateCount"] == 24
    assert generation["readyCount"] == 24
    assert generation["failedCount"] == 0
    assert generation["reviewedCount"] == 0
    assert generation["survivorCount"] == 0
    assert generation["rejectedCount"] == 0
    assert generation["lowDiversity"] is False
    assert generation["canBreedNextGeneration"] is False
    assert generation["canRerollGeneration"] is False
    assert len(generation["candidates"]) == 24

    current_response = client.get(f"/runs/{run_id}/generations/current")
    assert current_response.status_code == 200
    assert current_response.json()["generation"]["id"] == generation["id"]

    rows = fetch_rows(workspace, "candidates")
    assert len(rows) == 24
    strategy_families = {
        json.loads(row["genome_json"])["strategyFamily"] for row in rows
    }
    assert strategy_families == {
        "outline_retrace",
        "directional_fill",
        "vertical_flow",
    }

    for row in rows:
        assert uuid.UUID(row["id"]).version == 7
        artifact_path = Path(row["artifact_path"])
        assert not artifact_path.is_absolute()
        assert artifact_path.parts[:2] == ("artifacts", "candidates")
        artifact_bytes = (workspace / artifact_path).read_bytes()
        assert row["byte_size"] == len(artifact_bytes)
        assert row["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()

        genome = json.loads(row["genome_json"])
        assert genome["strategyFamily"]
        assert isinstance(genome["seed"], int)
        assert genome["renderParameters"]["seed"] == genome["seed"]
        assert genome["renderParameters"]["repeats"] >= generations.REPEATS_MIN

    assert fetch_candidate_parent_rows(workspace) == []
    assert all(
        candidate["parentCandidateIds"] == []
        and candidate["parentGenerationId"] is None
        for candidate in generation["candidates"]
    )


def test_runs_history_lists_sources_generations_and_review_decisions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    upload = upload_source(client)
    run_id = upload["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]

    client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": generation["candidates"][0]["id"], "decision": "survived"},
    )
    client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": generation["candidates"][1]["id"], "decision": "rejected"},
    )

    response = client.get("/runs")

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["source"]["filename"] == "source.svg"
    assert len(runs[0]["generations"]) == 1
    history_generation = runs[0]["generations"][0]
    assert history_generation["survivorCount"] == 1
    assert history_generation["rejectedCount"] == 1
    assert history_generation["candidates"][0]["reviewDecision"] == "survived"
    assert history_generation["candidates"][1]["reviewDecision"] == "rejected"
    assert history_generation["candidates"][2]["reviewDecision"] is None


@pytest.mark.parametrize(
    ("filename", "source_svg"),
    [
        ("tom2.svg", TOM2_SOURCE_SVG),
        ("tiny-path.svg", TINY_PATH_SOURCE_SVG),
        ("filled-basic-shape.svg", FILLED_BASIC_SHAPE_SOURCE_SVG),
        ("transformed-group.svg", TRANSFORMED_GROUP_SOURCE_SVG),
    ],
)
def test_mvp_first_generation_flow_accepts_representative_svg_sources(
    tmp_path: Path,
    monkeypatch,
    filename: str,
    source_svg: bytes,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    upload = upload_source(client, source_svg, filename=filename)

    response = client.post(f"/runs/{upload['run']['id']}/generations")

    assert response.status_code == 201
    source = upload["source"]
    run = upload["run"]
    generation = response.json()["generation"]
    assert uuid.UUID(source["id"]).version == 7
    assert uuid.UUID(run["id"]).version == 7
    assert uuid.UUID(generation["id"]).version == 7
    assert source["filename"] == filename
    assert generation["runId"] == run["id"]
    assert generation["generationNumber"] == 1
    assert generation["status"] == "ready"
    assert generation["readyCount"] == 24
    assert generation["failedCount"] == 0
    assert generation["totalCandidateCount"] == 24

    source_artifact_path = Path(source["artifactPath"])
    assert not source_artifact_path.is_absolute()
    assert source_artifact_path.parts[:2] == ("artifacts", "sources")
    source_artifact = workspace / source_artifact_path
    assert source_artifact.exists()
    assert source["byteSize"] == len(source_artifact.read_bytes())

    source_rows = fetch_rows(workspace, "sources")
    run_rows = fetch_rows(workspace, "runs")
    generation_rows = fetch_rows(workspace, "generations")
    candidate_rows = fetch_rows(workspace, "candidates")
    decision_rows = fetch_rows(workspace, "candidate_decisions")
    assert source_rows[0]["id"] == source["id"]
    assert source_rows[0]["artifact_path"] == source["artifactPath"]
    assert run_rows[0]["id"] == run["id"]
    assert run_rows[0]["source_id"] == source["id"]
    assert generation_rows[0]["id"] == generation["id"]
    assert generation_rows[0]["status"] == "ready"
    assert len(candidate_rows) == 24
    assert decision_rows == []

    for candidate_row in candidate_rows:
        assert uuid.UUID(candidate_row["id"]).version == 7
        artifact_path = Path(candidate_row["artifact_path"])
        assert not artifact_path.is_absolute()
        assert artifact_path.parts[:2] == ("artifacts", "candidates")
        assert (workspace / artifact_path).exists()


def test_split_source_first_generation_uses_lower_retrace_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    upload = upload_source(client, LONG_TRACED_STROKE_SVG)
    run_id = upload["run"]["id"]

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 201
    rows = fetch_rows(workspace, "candidates")
    repeats = [
        json.loads(row["genome_json"])["renderParameters"]["repeats"]
        for row in rows
    ]
    assert min(repeats) >= generations.SPLIT_SOURCE_REPEATS_MIN
    assert max(repeats) <= generations.SPLIT_SOURCE_REPEATS_MAX


def test_duplicate_generation_creation_returns_conflict_without_extra_candidates(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]

    first_response = client.post(f"/runs/{run_id}/generations")
    duplicate_response = client.post(f"/runs/{run_id}/generations")

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert len(fetch_rows(workspace, "generations")) == 1
    assert len(fetch_rows(workspace, "candidates")) == 24


def test_unknown_run_generation_returns_not_found(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "workspace"))

    response = client.post(f"/runs/{uuid.uuid4()}/generations")

    assert response.status_code == 404


def test_invalid_source_artifact_returns_bad_request_without_partial_generation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client, EMPTY_SOURCE_SVG)["run"]["id"]

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 400
    assert fetch_rows(workspace, "generations") == []
    assert fetch_rows(workspace, "candidates") == []


def test_missing_source_artifact_returns_bad_request_without_partial_generation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    upload = upload_source(client)
    run_id = upload["run"]["id"]
    (workspace / upload["source"]["artifactPath"]).unlink()

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 400
    assert fetch_rows(workspace, "generations") == []
    assert fetch_rows(workspace, "candidates") == []


def test_failed_render_attempts_are_persisted_and_excluded_from_ready_counts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    calls = 0

    def fake_render_candidate_svg(
        source_path: Path,
        artifact_path: Path,
        render_parameters: dict,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise RuntimeError("forced render failure")
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
  <path d="M 1 1 L 9 9" style="fill:none;stroke:#111;stroke-width:1;stroke-opacity:1" data-sketcher-pass="1" />
</svg>
""",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        generations,
        "render_candidate_svg",
        fake_render_candidate_svg,
    )

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 201
    generation = response.json()["generation"]
    assert generation["status"] == "ready"
    assert generation["totalCandidateCount"] == 26
    assert generation["readyCount"] == 24
    assert generation["failedCount"] == 2

    rows = fetch_rows(workspace, "candidates")
    failed = [row for row in rows if row["validation_status"] == "failed"]
    ready = [row for row in rows if row["validation_status"] == "ready"]
    assert len(failed) == 2
    assert len(ready) == 24
    assert {row["validation_message"] for row in failed} == {"forced render failure"}


def test_running_first_generation_is_visible_before_request_completes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    second_attempt_started = threading.Event()
    continue_rendering = threading.Event()
    calls = 0
    lock = threading.Lock()

    def fake_render_candidate_svg(
        source_path: Path,
        artifact_path: Path,
        render_parameters: dict,
    ) -> None:
        nonlocal calls
        with lock:
            calls += 1
            attempt = calls
        if attempt == 2:
            second_attempt_started.set()
            if not continue_rendering.wait(timeout=5):
                raise RuntimeError("timed out waiting to continue rendering")
        write_valid_candidate_svg(artifact_path, render_parameters["seed"])

    monkeypatch.setattr(generations, "render_candidate_svg", fake_render_candidate_svg)
    result: dict[str, object] = {}

    def create_generation() -> None:
        try:
            result["summary"] = generations.create_first_generation(workspace, run_id)
        except Exception as error:  # pragma: no cover - re-raised in the test thread.
            result["error"] = error

    thread = threading.Thread(target=create_generation)
    thread.start()
    assert second_attempt_started.wait(timeout=5)

    current_response = client.get(f"/runs/{run_id}/generations/current")
    review_response = client.get(f"/runs/{run_id}/review/current")

    assert current_response.status_code == 200
    running_generation = current_response.json()["generation"]
    assert running_generation["status"] == "running"
    assert running_generation["totalCandidateCount"] == 1
    assert running_generation["readyCount"] == 1
    assert running_generation["failedCount"] == 0
    assert review_response.status_code == 409
    assert "still running" in review_response.json()["detail"]

    candidate_id = running_generation["candidates"][0]["id"]
    decision_response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": candidate_id, "decision": "survived"},
    )
    duplicate_response = client.post(f"/runs/{run_id}/generations")

    assert decision_response.status_code == 409
    assert "still running" in decision_response.json()["detail"]
    assert duplicate_response.status_code == 409

    continue_rendering.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    summary = result["summary"]
    assert isinstance(summary, generations.GenerationSummary)
    assert summary.status == "ready"
    assert summary.ready_count == 24


def test_failed_render_attempt_is_visible_while_generation_is_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    second_attempt_started = threading.Event()
    continue_rendering = threading.Event()
    calls = 0
    lock = threading.Lock()

    def fake_render_candidate_svg(
        source_path: Path,
        artifact_path: Path,
        render_parameters: dict,
    ) -> None:
        nonlocal calls
        with lock:
            calls += 1
            attempt = calls
        if attempt == 1:
            raise RuntimeError("first candidate failed")
        if attempt == 2:
            second_attempt_started.set()
            if not continue_rendering.wait(timeout=5):
                raise RuntimeError("timed out waiting to continue rendering")
        write_valid_candidate_svg(artifact_path, render_parameters["seed"])

    monkeypatch.setattr(generations, "render_candidate_svg", fake_render_candidate_svg)
    result: dict[str, object] = {}

    def create_generation() -> None:
        try:
            result["summary"] = generations.create_first_generation(workspace, run_id)
        except Exception as error:  # pragma: no cover - re-raised in the test thread.
            result["error"] = error

    thread = threading.Thread(target=create_generation)
    thread.start()
    assert second_attempt_started.wait(timeout=5)

    current_response = client.get(f"/runs/{run_id}/generations/current")

    assert current_response.status_code == 200
    running_generation = current_response.json()["generation"]
    assert running_generation["status"] == "running"
    assert running_generation["totalCandidateCount"] == 1
    assert running_generation["readyCount"] == 0
    assert running_generation["failedCount"] == 1
    assert running_generation["candidates"][0]["validationStatus"] == "failed"
    assert running_generation["candidates"][0]["validationMessage"] == "first candidate failed"

    continue_rendering.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    if "error" in result:
        raise result["error"]  # type: ignore[misc]


def test_generation_becomes_partial_failed_when_attempts_are_exhausted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]

    def fake_render_candidate_svg(
        source_path: Path,
        artifact_path: Path,
        render_parameters: dict,
    ) -> None:
        raise RuntimeError("forced render failure")

    monkeypatch.setattr(generations, "MAX_FIRST_GENERATION_ATTEMPTS", 3)
    monkeypatch.setattr(generations, "render_candidate_svg", fake_render_candidate_svg)

    response = client.post(f"/runs/{run_id}/generations")

    assert response.status_code == 201
    generation = response.json()["generation"]
    assert generation["status"] == "partial_failed"
    assert generation["totalCandidateCount"] == 3
    assert generation["readyCount"] == 0
    assert generation["failedCount"] == 3


def test_survivor_stroke_count_mutations_can_reach_thousands() -> None:
    parent_parameters = {"repeats": 28, "shade_strokes": 58}

    repeat_mutation = generations.mutate_render_parameters(
        parent_parameters,
        random.Random(5),
    )
    shade_mutation = generations.mutate_render_parameters(
        parent_parameters,
        random.Random(1),
    )

    assert repeat_mutation["repeats"] > 1000
    assert repeat_mutation["repeats"] <= generations.REPEATS_MAX
    assert shade_mutation["shade_strokes"] > 1000
    assert shade_mutation["shade_strokes"] <= generations.SHADE_STROKES_MAX


def test_split_source_repeat_mutations_stay_low() -> None:
    parent_parameters = {"repeats": 12, "shade_strokes": 58}

    mutation = generations.mutate_render_parameters(
        parent_parameters,
        random.Random(5),
        source_substroke_count=24,
    )

    assert generations.SPLIT_SOURCE_REPEATS_MIN <= mutation["repeats"]
    assert mutation["repeats"] <= generations.SPLIT_SOURCE_REPEATS_MAX


def test_dense_survivor_strokes_stay_visible_and_more_human() -> None:
    parent_parameters = {
        "repeats": 28,
        "shade_strokes": 58,
        "stroke_width": 0.22,
        "opacity": 0.075,
        "shade_width": 1.1,
        "shade_opacity": 0.14,
        "jitter": 0.08,
        "roughness": 0.45,
        "stroke_fragment_min": 0.16,
        "stroke_fragment_max": 0.82,
        "stroke_fragment_probability": 0.7,
        "pressure_variance": 0.48,
        "strength_variance": 0.45,
        "full_retrace_interval": 13,
    }

    repeat_mutation = generations.mutate_render_parameters(
        parent_parameters,
        random.Random(5),
    )
    shade_mutation = generations.mutate_render_parameters(
        parent_parameters,
        random.Random(1),
    )

    assert repeat_mutation["repeats"] > 1000
    assert repeat_mutation["stroke_width"] >= generations.DENSE_STROKE_WIDTH_FLOOR
    assert repeat_mutation["opacity"] >= generations.DENSE_STROKE_OPACITY_FLOOR
    assert repeat_mutation["jitter"] > parent_parameters["jitter"]
    assert repeat_mutation["roughness"] > parent_parameters["roughness"]
    assert repeat_mutation["stroke_fragment_min"] <= repeat_mutation["stroke_fragment_max"]
    assert 0.18 <= repeat_mutation["stroke_fragment_probability"] <= 0.98
    assert 0.12 <= repeat_mutation["pressure_variance"] <= 1.25
    assert 0.12 <= repeat_mutation["strength_variance"] <= 1.0
    assert 0 <= repeat_mutation["full_retrace_interval"] <= 28

    assert shade_mutation["shade_strokes"] > 1000
    assert shade_mutation["shade_width"] >= generations.DENSE_SHADE_WIDTH_FLOOR
    assert shade_mutation["shade_opacity"] >= generations.DENSE_SHADE_OPACITY_FLOOR
    assert shade_mutation["roughness"] > parent_parameters["roughness"]


def test_survivor_mutation_includes_mutable_flick_parameters() -> None:
    parent_parameters = {
        "repeats": 28,
        "shade_strokes": 58,
        "flick_strength": 0.45,
        "flick_bias": "start",
        "flick_curve": "ease_out",
        "flick_probability": 0.65,
        "flick_min_width": 0.08,
        "flick_min_opacity": 0.04,
    }

    mutation = generations.mutate_render_parameters(
        parent_parameters,
        random.Random(9),
    )

    assert mutation["flick_bias"] in {"start", "end", "neutral"}
    assert mutation["flick_curve"] in {"linear", "ease_in", "ease_out"}
    assert 0 <= mutation["flick_strength"] <= 1
    assert 0 <= mutation["flick_probability"] <= 1
    assert mutation["flick_min_width"] > 0
    assert 0 < mutation["flick_min_opacity"] <= 1
    assert any(
        mutation[key] != parent_parameters[key]
        for key in (
            "flick_strength",
            "flick_bias",
            "flick_curve",
            "flick_probability",
            "flick_min_width",
            "flick_min_opacity",
        )
    )


def test_next_generation_requires_completed_review(tmp_path: Path, monkeypatch) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    client.post(f"/runs/{run_id}/generations")

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 409
    assert "review must be complete" in response.json()["detail"]


def test_generation_summary_review_fields_follow_active_decisions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]

    initial = client.get(f"/runs/{run_id}/generations/current").json()["generation"]
    assert initial["reviewedCount"] == 0
    assert initial["survivorCount"] == 0
    assert initial["rejectedCount"] == 0
    assert initial["lowDiversity"] is False
    assert initial["canBreedNextGeneration"] is False
    assert initial["canRerollGeneration"] is False

    response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": generation["candidates"][0]["id"], "decision": "survived"},
    )
    assert response.status_code == 200
    during_review = client.get(f"/runs/{run_id}/generations/current").json()[
        "generation"
    ]
    assert during_review["reviewedCount"] == 1
    assert during_review["survivorCount"] == 1
    assert during_review["rejectedCount"] == 0
    assert during_review["lowDiversity"] is True
    assert during_review["canBreedNextGeneration"] is False
    assert during_review["canRerollGeneration"] is False

    undo_response = client.post(f"/runs/{run_id}/review/undo")
    assert undo_response.status_code == 200
    after_undo = client.get(f"/runs/{run_id}/generations/current").json()[
        "generation"
    ]
    assert after_undo["reviewedCount"] == 0
    assert after_undo["survivorCount"] == 0
    assert after_undo["rejectedCount"] == 0
    assert after_undo["lowDiversity"] is False
    assert after_undo["canBreedNextGeneration"] is False
    assert after_undo["canRerollGeneration"] is False


@pytest.mark.parametrize(
    ("survivor_count", "low_diversity", "can_breed", "can_reroll"),
    [
        (0, False, False, True),
        (2, True, True, False),
        (3, False, True, False),
    ],
)
def test_completed_generation_summary_exposes_breeding_eligibility(
    tmp_path: Path,
    monkeypatch,
    survivor_count: int,
    low_diversity: bool,
    can_breed: bool,
    can_reroll: bool,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=survivor_count)

    summary = client.get(f"/runs/{run_id}/generations/current").json()["generation"]

    assert summary["readyCount"] == 24
    assert summary["failedCount"] == 0
    assert summary["reviewedCount"] == 24
    assert summary["survivorCount"] == survivor_count
    assert summary["rejectedCount"] == 24 - survivor_count
    assert summary["lowDiversity"] is low_diversity
    assert summary["canBreedNextGeneration"] is can_breed
    assert summary["canRerollGeneration"] is can_reroll


def test_zero_survivors_blocks_breed_and_allows_reroll(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=0)

    breed_response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )
    reroll_response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "reroll"},
    )

    assert breed_response.status_code == 409
    assert "Reroll this generation instead" in breed_response.json()["detail"]
    assert reroll_response.status_code == 201
    next_generation = reroll_response.json()["generation"]
    assert next_generation["generationNumber"] == 2
    assert next_generation["readyCount"] == 24
    assert {candidate["originType"] for candidate in next_generation["candidates"]} == {
        "random_immigrant"
    }
    assert all(
        candidate["genome"]["parentCandidateIds"] == []
        for candidate in next_generation["candidates"]
    )
    assert all(
        candidate["parentCandidateIds"] == []
        and candidate["parentGenerationId"] is None
        for candidate in next_generation["candidates"]
    )
    assert fetch_candidate_parent_rows(workspace) == []


@pytest.mark.parametrize("survivor_count", [1, 2])
def test_low_diversity_breed_creates_four_immigrants(
    tmp_path: Path,
    monkeypatch,
    survivor_count: int,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=survivor_count)

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 201
    candidates = response.json()["generation"]["candidates"]
    ready_candidates = [
        candidate for candidate in candidates if candidate["validationStatus"] == "ready"
    ]
    assert len([c for c in ready_candidates if c["originType"] == "random_immigrant"]) == 4
    assert (
        len([c for c in ready_candidates if c["originType"] == "survivor_carryover"])
        == survivor_count
    )
    assert (
        len([c for c in ready_candidates if c["originType"] == "survivor_mutation"])
        == 24 - 4 - survivor_count
    )


def test_normal_breed_creates_two_immigrants(tmp_path: Path, monkeypatch) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=5)

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 201
    ready_candidates = [
        candidate
        for candidate in response.json()["generation"]["candidates"]
        if candidate["validationStatus"] == "ready"
    ]
    assert len([c for c in ready_candidates if c["originType"] == "random_immigrant"]) == 2
    assert len([c for c in ready_candidates if c["originType"] == "survivor_carryover"]) == 5
    assert len([c for c in ready_candidates if c["originType"] == "survivor_mutation"]) == 17


def test_breed_persists_and_exposes_candidate_parent_links(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=3)

    next_generation = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    ).json()["generation"]

    survivors = generation["candidates"][:3]
    parent_rows = fetch_candidate_parent_rows(workspace)
    child_candidates = [
        candidate
        for candidate in next_generation["candidates"]
        if candidate["originType"] in {"survivor_mutation", "survivor_carryover"}
    ]
    immigrants = [
        candidate
        for candidate in next_generation["candidates"]
        if candidate["originType"] == "random_immigrant"
    ]

    assert len(parent_rows) == len(child_candidates)
    assert all(
        candidate["parentCandidateIds"] == []
        and candidate["parentGenerationId"] is None
        for candidate in immigrants
    )
    assert all(
        len(candidate["parentCandidateIds"]) == 1
        and candidate["parentGenerationId"] == generation["id"]
        for candidate in child_candidates
    )

    mutation_parents = [
        candidate["parentCandidateIds"][0]
        for candidate in child_candidates
        if candidate["originType"] == "survivor_mutation"
    ]
    carryover_parents = [
        candidate["parentCandidateIds"][0]
        for candidate in child_candidates
        if candidate["originType"] == "survivor_carryover"
    ]
    assert mutation_parents == [
        survivors[index % len(survivors)]["id"]
        for index in range(len(mutation_parents))
    ]
    assert carryover_parents == [candidate["id"] for candidate in survivors]

    parent_rows_by_candidate = {row["candidate_id"]: row for row in parent_rows}
    for candidate in child_candidates:
        row = parent_rows_by_candidate[candidate["id"]]
        assert row["parent_candidate_id"] == candidate["parentCandidateIds"][0]
        assert row["parent_generation_id"] == candidate["parentGenerationId"]
        assert row["parent_index"] == 0


def test_running_next_generation_is_visible_before_request_completes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=5)

    second_attempt_started = threading.Event()
    continue_rendering = threading.Event()
    calls = 0
    lock = threading.Lock()

    def fake_render_candidate_svg(
        source_path: Path,
        artifact_path: Path,
        render_parameters: dict,
    ) -> None:
        nonlocal calls
        with lock:
            calls += 1
            attempt = calls
        if attempt == 2:
            second_attempt_started.set()
            if not continue_rendering.wait(timeout=5):
                raise RuntimeError("timed out waiting to continue rendering")
        write_valid_candidate_svg(artifact_path, render_parameters["seed"])

    monkeypatch.setattr(generations, "render_candidate_svg", fake_render_candidate_svg)
    result: dict[str, object] = {}

    def create_generation() -> None:
        try:
            result["summary"] = generations.create_next_generation(
                workspace,
                run_id,
                mode="breed",
            )
        except Exception as error:  # pragma: no cover - re-raised in the test thread.
            result["error"] = error

    thread = threading.Thread(target=create_generation)
    thread.start()
    assert second_attempt_started.wait(timeout=5)

    current_response = client.get(f"/runs/{run_id}/generations/current")

    assert current_response.status_code == 200
    running_generation = current_response.json()["generation"]
    assert running_generation["generationNumber"] == 2
    assert running_generation["status"] == "running"
    assert running_generation["totalCandidateCount"] == 1
    assert running_generation["readyCount"] == 1
    assert running_generation["candidates"][0]["originType"] == "survivor_mutation"

    continue_rendering.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    summary = result["summary"]
    assert isinstance(summary, generations.GenerationSummary)
    assert summary.status == "ready"
    assert summary.ready_count == 24


def test_survivor_carryovers_are_appended_last_and_capped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=12)

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 201
    candidates = [
        candidate
        for candidate in response.json()["generation"]["candidates"]
        if candidate["validationStatus"] == "ready"
    ]
    carryovers = [c for c in candidates if c["originType"] == "survivor_carryover"]
    assert len(carryovers) == 8
    assert [candidate["originType"] for candidate in candidates[-8:]] == [
        "survivor_carryover"
    ] * 8
    assert [c["genome"]["parentCandidateIds"][0] for c in carryovers] == [
        candidate["id"] for candidate in generation["candidates"][:8]
    ]


def test_carryovers_must_be_reviewed_again_in_new_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=3)

    next_generation = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    ).json()["generation"]
    review = client.get(f"/runs/{run_id}/review/current").json()["review"]

    assert review["generationId"] == next_generation["id"]
    assert review["reviewedCount"] == 0
    assert review["survivorCount"] == 0
    assert review["totalReadyCount"] == 24
    assert len(fetch_rows(workspace, "candidate_decisions")) == 24


def test_undone_survived_decisions_do_not_count_as_active_survivors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]

    survived_response = client.post(
        f"/runs/{run_id}/review/decisions",
        json={"candidateId": generation["candidates"][0]["id"], "decision": "survived"},
    )
    undo_response = client.post(f"/runs/{run_id}/review/undo")
    assert survived_response.status_code == 200
    assert undo_response.status_code == 200
    review_generation(client, run_id, generation["candidates"], survivor_count=0)

    response = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    )

    assert response.status_code == 409
    assert "Reroll this generation instead" in response.json()["detail"]


def test_next_generation_artifacts_are_workspace_relative_and_hash_checked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    install_fast_renderer(monkeypatch)
    workspace = tmp_path / "workspace"
    client = TestClient(create_app(workspace))
    run_id = upload_source(client)["run"]["id"]
    generation = client.post(f"/runs/{run_id}/generations").json()["generation"]
    review_generation(client, run_id, generation["candidates"], survivor_count=1)

    next_generation = client.post(
        f"/runs/{run_id}/generations/next",
        json={"mode": "breed"},
    ).json()["generation"]
    carryover = next(
        candidate
        for candidate in next_generation["candidates"]
        if candidate["originType"] == "survivor_carryover"
    )
    artifact_path = Path(carryover["artifactPath"])
    artifact_bytes = (workspace / artifact_path).read_bytes()

    assert not artifact_path.is_absolute()
    assert artifact_path.parts[:2] == ("artifacts", "candidates")
    assert carryover["byteSize"] == len(artifact_bytes)
    assert carryover["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert carryover["sha256"] == generation["candidates"][0]["sha256"]
    artifact_response = client.get(f"/candidates/{carryover['id']}/artifact")
    assert artifact_response.status_code == 200
    assert artifact_response.headers["x-content-sha256"] == carryover["sha256"]
