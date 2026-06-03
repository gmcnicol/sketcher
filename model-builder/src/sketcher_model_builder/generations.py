"""Candidate generation creation and persistence."""

from __future__ import annotations

import hashlib
import json
import math
import random
import shutil
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .generator import (
    absolute_path_bounds,
    basic_shape_to_path_d,
    local_name,
    render_sketch_svg,
    style_to_dict,
)
from .workspace import connect, ensure_workspace, new_uuid7, utc_now


FIRST_GENERATION_SIZE = 24
MAX_FIRST_GENERATION_ATTEMPTS = 72
NEXT_GENERATION_SIZE = 24
MAX_NEXT_GENERATION_ATTEMPTS = 96
MAX_SURVIVOR_CARRYOVERS = 8
REPEATS_MIN = 100
REPEATS_MAX = 3000
REPEATS_MUTATION_DELTA = 3000
SHADE_STROKES_MIN = 0
SHADE_STROKES_MAX = 3000
SHADE_STROKES_MUTATION_DELTA = 3000
DENSE_REPEATS_BASELINE = 60
DENSE_SHADE_STROKES_BASELINE = 90
DENSE_STROKE_WIDTH_FLOOR = 0.095
DENSE_STROKE_OPACITY_FLOOR = 0.058
DENSE_SHADE_WIDTH_FLOOR = 0.22
DENSE_SHADE_OPACITY_FLOOR = 0.07
SKETCH_PATH_MARKERS = {
    "data-sketcher-pass",
    "data-sketcher-flow-pass",
    "data-sketcher-pressure-pass",
    "data-sketcher-vertical-flow-pass",
}


class GenerationError(ValueError):
    """Raised when a generation cannot be created or loaded."""


class UnknownRunError(GenerationError):
    """Raised when a run ID is not present in the workspace."""


class DuplicateGenerationError(GenerationError):
    """Raised when generation creation is requested more than once for a run."""


class MissingGenerationError(GenerationError):
    """Raised when a run has no generation to report."""


class SourceArtifactError(GenerationError):
    """Raised when a run source cannot be used for generation."""


class ReviewNotCompleteError(GenerationError):
    """Raised when next generation creation is requested before review completion."""


class NoSurvivorsError(GenerationError):
    """Raised when breeding is requested without active survivors."""


class RerollNotAllowedError(GenerationError):
    """Raised when rerolling is requested with active survivors."""


class CandidateArtifactLineageError(GenerationError):
    """Raised when a parent candidate artifact cannot be safely copied."""


@dataclass(frozen=True)
class CandidateSummary:
    id: str
    run_id: str
    generation_id: str
    generation_number: int
    position: int
    origin_type: str
    genome: dict[str, Any]
    artifact_path: str | None
    byte_size: int | None
    sha256: str | None
    validation_status: str
    validation_message: str | None
    created_at: str


@dataclass(frozen=True)
class GenerationSummary:
    id: str
    run_id: str
    generation_number: int
    status: str
    total_candidate_count: int
    ready_count: int
    failed_count: int
    candidates: list[CandidateSummary]
    created_at: str


@dataclass(frozen=True)
class SourceContext:
    run_id: str
    source_id: str
    source_path: Path
    source_bounds: tuple[float, float, float, float]


@dataclass(frozen=True)
class ParentCandidate:
    id: str
    generation_id: str
    generation_number: int
    position: int
    origin_type: str
    genome: dict[str, Any]
    artifact_path: str
    byte_size: int
    sha256: str


def create_first_generation(workspace: Path, run_id: str) -> GenerationSummary:
    workspace = ensure_workspace(workspace)

    with connect(workspace) as db:
        source_context = load_source_context(db, workspace, run_id)
        if generation_exists(db, run_id):
            raise DuplicateGenerationError(f"Run {run_id} already has a generation.")

    generation_id = new_uuid7()
    created_at = utc_now()

    with connect(workspace) as db:
        db.execute(
            """
            INSERT INTO generations (id, run_id, generation_number, status, created_at)
            VALUES (?, ?, 1, 'running', ?)
            """,
            (generation_id, run_id, created_at),
        )

        ready_count = 0
        for attempt in range(1, MAX_FIRST_GENERATION_ATTEMPTS + 1):
            if ready_count >= FIRST_GENERATION_SIZE:
                break

            candidate_id = new_uuid7()
            genome = build_first_generation_genome(run_id, attempt)
            artifact_relative_path = candidate_artifact_path(
                run_id=run_id,
                generation_id=generation_id,
                position=attempt,
                candidate_id=candidate_id,
            )
            artifact_path = workspace / artifact_relative_path
            validation_status = "ready"
            validation_message: str | None = None
            byte_size: int | None = None
            sha256: str | None = None

            try:
                render_candidate_svg(
                    source_context.source_path,
                    artifact_path,
                    genome["renderParameters"],
                )
                validation_message = validate_candidate_svg(
                    artifact_path,
                    source_context.source_bounds,
                )
                data = artifact_path.read_bytes()
                byte_size = len(data)
                sha256 = hashlib.sha256(data).hexdigest()
                ready_count += 1
            except Exception as error:
                validation_status = "failed"
                validation_message = str(error) or error.__class__.__name__
                if artifact_path.exists():
                    data = artifact_path.read_bytes()
                    byte_size = len(data)
                    sha256 = hashlib.sha256(data).hexdigest()

            insert_candidate(
                db,
                candidate_id=candidate_id,
                run_id=run_id,
                generation_id=generation_id,
                generation_number=1,
                position=attempt,
                origin_type="preset_mutation",
                genome=genome,
                artifact_path=artifact_relative_path.as_posix()
                if artifact_path.exists()
                else None,
                byte_size=byte_size,
                sha256=sha256,
                validation_status=validation_status,
                validation_message=validation_message,
            )

        status = "ready" if ready_count == FIRST_GENERATION_SIZE else "partial_failed"
        db.execute(
            "UPDATE generations SET status = ? WHERE id = ?",
            (status, generation_id),
        )

    return get_current_generation(workspace, run_id)


def create_next_generation(
    workspace: Path,
    run_id: str,
    *,
    mode: str,
) -> GenerationSummary:
    workspace = ensure_workspace(workspace)
    if mode not in {"breed", "reroll"}:
        raise GenerationError("Next generation mode must be breed or reroll.")

    with connect(workspace) as db:
        source_context = load_source_context(db, workspace, run_id)
        previous_generation = load_current_generation_row(db, run_id)
        ready_count, reviewed_count = review_completion_counts(db, previous_generation["id"])
        if not ready_count or reviewed_count < ready_count:
            raise ReviewNotCompleteError(
                "Current generation review must be complete before creating the next generation."
            )

        survivors = load_active_survivors(db, previous_generation["id"])
        if mode == "breed" and not survivors:
            raise NoSurvivorsError(
                "Breed next generation requires at least one survivor. Reroll this generation instead."
            )
        if mode == "reroll" and survivors:
            raise RerollNotAllowedError(
                "Reroll is only available when the completed generation has zero survivors."
            )

    generation_id = new_uuid7()
    generation_number = previous_generation["generation_number"] + 1
    created_at = utc_now()
    carryovers = survivors[:MAX_SURVIVOR_CARRYOVERS] if mode == "breed" else []
    immigrant_target = next_generation_immigrant_target(len(survivors), mode)
    mutation_target = NEXT_GENERATION_SIZE - immigrant_target - len(carryovers)

    with connect(workspace) as db:
        db.execute(
            """
            INSERT INTO generations (id, run_id, generation_number, status, created_at)
            VALUES (?, ?, ?, 'running', ?)
            """,
            (generation_id, run_id, generation_number, created_at),
        )

        position = 1
        ready_count = 0
        mutation_ready_count, position = render_next_generation_group(
            db,
            workspace,
            source_context=source_context,
            run_id=run_id,
            generation_id=generation_id,
            generation_number=generation_number,
            starting_position=position,
            target_ready_count=mutation_target,
            origin_type="survivor_mutation",
            genome_builder=lambda slot: build_survivor_mutation_genome(
                run_id=run_id,
                generation_number=generation_number,
                slot=slot,
                parent=survivors[(slot - 1) % len(survivors)],
            ),
        )
        ready_count += mutation_ready_count

        immigrant_ready_count, position = render_next_generation_group(
            db,
            workspace,
            source_context=source_context,
            run_id=run_id,
            generation_id=generation_id,
            generation_number=generation_number,
            starting_position=position,
            target_ready_count=immigrant_target,
            origin_type="random_immigrant",
            genome_builder=lambda slot: build_random_immigrant_genome(
                run_id=run_id,
                generation_number=generation_number,
                slot=slot,
            ),
        )
        ready_count += immigrant_ready_count

        for parent in carryovers:
            copy_survivor_carryover(
                db,
                workspace,
                run_id=run_id,
                generation_id=generation_id,
                generation_number=generation_number,
                position=position,
                parent=parent,
            )
            position += 1
            ready_count += 1

        status = "ready" if ready_count == NEXT_GENERATION_SIZE else "partial_failed"
        db.execute(
            "UPDATE generations SET status = ? WHERE id = ?",
            (status, generation_id),
        )

    return get_current_generation(workspace, run_id)


def get_current_generation(workspace: Path, run_id: str) -> GenerationSummary:
    workspace = ensure_workspace(workspace)
    with connect(workspace) as db:
        run = db.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise UnknownRunError(f"Run {run_id} was not found.")

        generation = db.execute(
            """
            SELECT * FROM generations
            WHERE run_id = ?
            ORDER BY generation_number DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if generation is None:
            raise MissingGenerationError(f"Run {run_id} does not have a generation yet.")

        return generation_summary_from_row(db, generation)


def load_current_generation_row(
    db: sqlite3.Connection,
    run_id: str,
) -> sqlite3.Row:
    run = db.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise UnknownRunError(f"Run {run_id} was not found.")

    generation = db.execute(
        """
        SELECT * FROM generations
        WHERE run_id = ?
        ORDER BY generation_number DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if generation is None:
        raise MissingGenerationError(f"Run {run_id} does not have a generation yet.")

    return generation


def load_source_context(
    db: sqlite3.Connection,
    workspace: Path,
    run_id: str,
) -> SourceContext:
    row = db.execute(
        """
        SELECT
            runs.id AS run_id,
            runs.status AS run_status,
            sources.id AS source_id,
            sources.artifact_path AS source_artifact_path
        FROM runs
        JOIN sources ON sources.id = runs.source_id
        WHERE runs.id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise UnknownRunError(f"Run {run_id} was not found.")
    if row["run_status"] != "active":
        raise SourceArtifactError(f"Run {run_id} is not active.")

    relative_path = Path(row["source_artifact_path"])
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise SourceArtifactError("Run source artifact path is not workspace-relative.")

    source_path = workspace / relative_path
    if not source_path.exists():
        raise SourceArtifactError("Run source artifact is missing.")
    if not source_path.is_file():
        raise SourceArtifactError("Run source artifact is not a file.")

    try:
        source_bounds = source_svg_bounds(source_path)
    except (ET.ParseError, ValueError) as error:
        raise SourceArtifactError(
            f"Run source artifact is not a renderable SVG: {error}"
        ) from error

    return SourceContext(
        run_id=row["run_id"],
        source_id=row["source_id"],
        source_path=source_path,
        source_bounds=source_bounds,
    )


def generation_exists(db: sqlite3.Connection, run_id: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM generations WHERE run_id = ? LIMIT 1",
            (run_id,),
        ).fetchone()
        is not None
    )


def review_completion_counts(
    db: sqlite3.Connection,
    generation_id: str,
) -> tuple[int, int]:
    ready_count = db.execute(
        """
        SELECT COUNT(*) AS count FROM candidates
        WHERE generation_id = ?
          AND validation_status = 'ready'
        """,
        (generation_id,),
    ).fetchone()["count"]
    reviewed_count = db.execute(
        """
        SELECT COUNT(*) AS count FROM candidate_decisions
        WHERE generation_id = ?
          AND undone_at IS NULL
        """,
        (generation_id,),
    ).fetchone()["count"]
    return ready_count, reviewed_count


def load_active_survivors(
    db: sqlite3.Connection,
    generation_id: str,
) -> list[ParentCandidate]:
    rows = db.execute(
        """
        SELECT candidates.* FROM candidates
        JOIN candidate_decisions
          ON candidate_decisions.candidate_id = candidates.id
         AND candidate_decisions.generation_id = candidates.generation_id
         AND candidate_decisions.undone_at IS NULL
         AND candidate_decisions.decision = 'survived'
        WHERE candidates.generation_id = ?
          AND candidates.validation_status = 'ready'
        ORDER BY candidates.position
        """,
        (generation_id,),
    ).fetchall()
    return [
        ParentCandidate(
            id=row["id"],
            generation_id=row["generation_id"],
            generation_number=row["generation_number"],
            position=row["position"],
            origin_type=row["origin_type"],
            genome=json.loads(row["genome_json"]),
            artifact_path=row["artifact_path"],
            byte_size=row["byte_size"],
            sha256=row["sha256"],
        )
        for row in rows
    ]


def source_svg_bounds(source_path: Path) -> tuple[float, float, float, float]:
    root = ET.parse(source_path).getroot()
    if local_name(root).lower() != "svg":
        raise ValueError("missing <svg> root")

    path_bounds: list[tuple[float, float, float, float]] = []
    for element in root.iter():
        tag = local_name(element)
        d = element.get("d") if tag == "path" else None
        if d is None and tag in {"rect", "circle", "ellipse", "line", "polyline", "polygon"}:
            d = basic_shape_to_path_d(element)
        if not d:
            continue
        path_bounds.append(absolute_path_bounds(d))

    if not path_bounds:
        raise ValueError("no renderable paths or shapes")

    return combine_bounds(path_bounds)


def build_first_generation_genome(run_id: str, attempt: int) -> dict[str, Any]:
    presets = [
        (
            "outline_retrace",
            {
                "mode": "outline",
                "repeats": 28,
                "shade_strokes": 0,
                "jitter": 0.08,
                "roughness": 0.38,
                "stroke_width": 0.32,
                "opacity": 0.13,
                "shade_width": 0.8,
                "shade_opacity": 0.08,
                "stroke_fragment_min": 0.22,
                "stroke_fragment_max": 0.95,
                "stroke_fragment_probability": 0.82,
                "pressure_variance": 0.5,
                "strength_variance": 0.42,
                "full_retrace_interval": 11,
            },
        ),
        (
            "directional_fill",
            {
                "mode": "fill",
                "repeats": 10,
                "shade_strokes": 58,
                "jitter": 0.07,
                "roughness": 0.42,
                "stroke_width": 0.3,
                "opacity": 0.11,
                "shade_width": 1.15,
                "shade_opacity": 0.18,
                "stroke_fragment_min": 0.12,
                "stroke_fragment_max": 0.76,
                "stroke_fragment_probability": 0.62,
                "pressure_variance": 0.68,
                "strength_variance": 0.58,
                "full_retrace_interval": 17,
            },
        ),
        (
            "vertical_flow",
            {
                "mode": "fill",
                "repeats": 6,
                "shade_strokes": 18,
                "jitter": 0.05,
                "roughness": 0.62,
                "stroke_width": 0.28,
                "opacity": 0.1,
                "shade_width": 1.45,
                "shade_opacity": 0.22,
                "stroke_fragment_min": 0.08,
                "stroke_fragment_max": 0.58,
                "stroke_fragment_probability": 0.48,
                "pressure_variance": 0.82,
                "strength_variance": 0.72,
                "full_retrace_interval": 0,
            },
        ),
    ]
    family, base_parameters = presets[(attempt - 1) % len(presets)]
    seed = candidate_seed(run_id, attempt)
    rng = random.Random(seed)

    render_parameters = dict(base_parameters)
    render_parameters.update(
        {
            "seed": seed,
            "keep_original": False,
            "stroke": "#111111",
            "stroke_order_distance_weight": 1.0,
            "stroke_fragment_min": clamp_float(
                render_parameters["stroke_fragment_min"] * rng.uniform(0.65, 1.4),
                0.04,
                0.45,
            ),
            "stroke_fragment_max": clamp_float(
                render_parameters["stroke_fragment_max"] * rng.uniform(0.75, 1.25),
                0.35,
                1.0,
            ),
            "stroke_fragment_probability": clamp_float(
                render_parameters["stroke_fragment_probability"] * rng.uniform(0.72, 1.28),
                0.18,
                0.98,
            ),
            "pressure_variance": clamp_float(
                render_parameters["pressure_variance"] * rng.uniform(0.7, 1.35),
                0.12,
                1.25,
            ),
            "strength_variance": clamp_float(
                render_parameters["strength_variance"] * rng.uniform(0.7, 1.35),
                0.12,
                1.0,
            ),
            "full_retrace_interval": clamp_int(
                render_parameters["full_retrace_interval"] + rng.randint(-3, 4),
                0,
                28,
            ),
            "repeats": clamp_int(
                render_parameters["repeats"] + rng.randint(-4, 5),
                REPEATS_MIN,
                REPEATS_MAX,
            ),
            "shade_strokes": clamp_int(
                render_parameters["shade_strokes"] + rng.randint(-10, 14),
                SHADE_STROKES_MIN,
                SHADE_STROKES_MAX,
            ),
            "jitter": clamp_float(
                render_parameters["jitter"] * rng.uniform(0.65, 1.65),
                0.02,
                0.22,
            ),
            "roughness": clamp_float(
                render_parameters["roughness"] * rng.uniform(0.55, 1.55),
                0.04,
                0.95,
            ),
            "stroke_width": clamp_float(
                render_parameters["stroke_width"] * rng.uniform(0.65, 1.6),
                0.12,
                0.52,
            ),
            "opacity": clamp_float(
                render_parameters["opacity"] * rng.uniform(0.75, 1.4),
                0.07,
                0.22,
            ),
            "shade_width": clamp_float(
                render_parameters["shade_width"] * rng.uniform(0.7, 1.55),
                0.45,
                2.4,
            ),
            "shade_opacity": clamp_float(
                render_parameters["shade_opacity"] * rng.uniform(0.65, 1.45),
                0.07,
                0.32,
            ),
        }
    )
    if render_parameters["stroke_fragment_min"] > render_parameters["stroke_fragment_max"]:
        render_parameters["stroke_fragment_min"] = render_parameters["stroke_fragment_max"]
    render_parameters = humanize_dense_strokes(render_parameters)

    return {
        "schemaVersion": 1,
        "strategyFamily": family,
        "seed": seed,
        "parentCandidateIds": [],
        "parentGenerationId": None,
        "generationNumber": 1,
        "lineageKind": "preset_mutation",
        "presetIndex": (attempt - 1) % len(presets),
        "mutationAttempt": attempt,
        "renderParameters": render_parameters,
    }


def build_random_immigrant_genome(
    *,
    run_id: str,
    generation_number: int,
    slot: int,
) -> dict[str, Any]:
    genome = build_first_generation_genome(
        f"{run_id}:generation:{generation_number}:random_immigrant",
        slot,
    )
    seed = lineage_seed(
        run_id=run_id,
        generation_number=generation_number,
        slot=slot,
        origin_type="random_immigrant",
    )
    render_parameters = dict(genome["renderParameters"])
    render_parameters["seed"] = seed
    genome.update(
        {
            "seed": seed,
            "parentCandidateIds": [],
            "parentGenerationId": None,
            "generationNumber": generation_number,
            "lineageKind": "random_immigrant",
            "immigrantSlot": slot,
            "renderParameters": render_parameters,
        }
    )
    return genome


def build_survivor_mutation_genome(
    *,
    run_id: str,
    generation_number: int,
    slot: int,
    parent: ParentCandidate,
) -> dict[str, Any]:
    seed = lineage_seed(
        run_id=run_id,
        generation_number=generation_number,
        slot=slot,
        origin_type="survivor_mutation",
        parent_id=parent.id,
    )
    rng = random.Random(seed)
    parent_parameters = dict(parent.genome.get("renderParameters", {}))
    render_parameters = mutate_render_parameters(parent_parameters, rng)
    render_parameters.update(
        {
            "seed": seed,
            "mode": parent_parameters.get("mode", "auto"),
            "stroke": parent_parameters.get("stroke", "#111111"),
            "keep_original": bool(parent_parameters.get("keep_original", False)),
        }
    )

    return {
        "schemaVersion": 1,
        "strategyFamily": parent.genome.get("strategyFamily", "survivor_mutation"),
        "seed": seed,
        "parentCandidateIds": [parent.id],
        "parentGenerationId": parent.generation_id,
        "generationNumber": generation_number,
        "lineageKind": "survivor_mutation",
        "parentPosition": parent.position,
        "mutationSlot": slot,
        "renderParameters": render_parameters,
    }


def build_survivor_carryover_genome(
    *,
    run_id: str,
    generation_number: int,
    position: int,
    parent: ParentCandidate,
) -> dict[str, Any]:
    seed = lineage_seed(
        run_id=run_id,
        generation_number=generation_number,
        slot=position,
        origin_type="survivor_carryover",
        parent_id=parent.id,
    )
    render_parameters = dict(parent.genome.get("renderParameters", {}))
    render_parameters["seed"] = seed
    return {
        "schemaVersion": 1,
        "strategyFamily": parent.genome.get("strategyFamily", "survivor_carryover"),
        "seed": seed,
        "parentCandidateIds": [parent.id],
        "parentGenerationId": parent.generation_id,
        "generationNumber": generation_number,
        "lineageKind": "survivor_carryover",
        "parentPosition": parent.position,
        "renderParameters": render_parameters,
    }


def mutate_render_parameters(
    parent_parameters: dict[str, Any],
    rng: random.Random,
) -> dict[str, Any]:
    render_parameters = {
        "mode": parent_parameters.get("mode", "auto"),
        "repeats": mutate_int_parameter(
            parent_parameters.get("repeats", 24),
            rng,
            minimum=REPEATS_MIN,
            maximum=REPEATS_MAX,
            delta=REPEATS_MUTATION_DELTA,
        ),
        "shade_strokes": mutate_int_parameter(
            parent_parameters.get("shade_strokes", 24),
            rng,
            minimum=SHADE_STROKES_MIN,
            maximum=SHADE_STROKES_MAX,
            delta=SHADE_STROKES_MUTATION_DELTA,
        ),
        "jitter": mutate_float_parameter(
            parent_parameters.get("jitter", 0.08),
            rng,
            minimum=0.02,
            maximum=0.22,
            variance=0.18,
        ),
        "roughness": mutate_float_parameter(
            parent_parameters.get("roughness", 0.45),
            rng,
            minimum=0.04,
            maximum=0.95,
            variance=0.16,
        ),
        "stroke_width": mutate_float_parameter(
            parent_parameters.get("stroke_width", 0.22),
            rng,
            minimum=0.12,
            maximum=0.52,
            variance=0.16,
        ),
        "opacity": mutate_float_parameter(
            parent_parameters.get("opacity", 0.075),
            rng,
            minimum=0.07,
            maximum=0.22,
            variance=0.14,
        ),
        "shade_width": mutate_float_parameter(
            parent_parameters.get("shade_width", 1.1),
            rng,
            minimum=0.45,
            maximum=2.4,
            variance=0.16,
        ),
        "shade_opacity": mutate_float_parameter(
            parent_parameters.get("shade_opacity", 0.14),
            rng,
            minimum=0.07,
            maximum=0.32,
            variance=0.14,
        ),
        "stroke": parent_parameters.get("stroke", "#111111"),
        "stroke_order_distance_weight": mutate_float_parameter(
            parent_parameters.get("stroke_order_distance_weight", 1.0),
            rng,
            minimum=0.0,
            maximum=2.0,
            variance=0.35,
        ),
        "stroke_fragment_min": mutate_float_parameter(
            parent_parameters.get("stroke_fragment_min", 0.16),
            rng,
            minimum=0.04,
            maximum=0.45,
            variance=0.32,
        ),
        "stroke_fragment_max": mutate_float_parameter(
            parent_parameters.get("stroke_fragment_max", 0.82),
            rng,
            minimum=0.35,
            maximum=1.0,
            variance=0.24,
        ),
        "stroke_fragment_probability": mutate_float_parameter(
            parent_parameters.get("stroke_fragment_probability", 0.7),
            rng,
            minimum=0.18,
            maximum=0.98,
            variance=0.3,
        ),
        "pressure_variance": mutate_float_parameter(
            parent_parameters.get("pressure_variance", 0.48),
            rng,
            minimum=0.12,
            maximum=1.25,
            variance=0.32,
        ),
        "strength_variance": mutate_float_parameter(
            parent_parameters.get("strength_variance", 0.45),
            rng,
            minimum=0.12,
            maximum=1.0,
            variance=0.32,
        ),
        "full_retrace_interval": mutate_int_parameter(
            parent_parameters.get("full_retrace_interval", 13),
            rng,
            minimum=0,
            maximum=28,
            delta=6,
        ),
        "keep_original": bool(parent_parameters.get("keep_original", False)),
    }
    if render_parameters["stroke_fragment_min"] > render_parameters["stroke_fragment_max"]:
        render_parameters["stroke_fragment_min"] = render_parameters["stroke_fragment_max"]
    return humanize_dense_strokes(render_parameters)


def humanize_dense_strokes(render_parameters: dict[str, Any]) -> dict[str, Any]:
    repeats = int(render_parameters["repeats"])
    shade_strokes = int(render_parameters["shade_strokes"])

    retrace_scale = density_scale(repeats, DENSE_REPEATS_BASELINE)
    shade_scale = density_scale(shade_strokes, DENSE_SHADE_STROKES_BASELINE)

    render_parameters = dict(render_parameters)
    render_parameters.update(
        {
            "stroke_width": clamp_float(
                float(render_parameters["stroke_width"]) * retrace_scale,
                DENSE_STROKE_WIDTH_FLOOR,
                0.52,
            ),
            "opacity": clamp_float(
                scale_density_opacity(float(render_parameters["opacity"]), retrace_scale),
                DENSE_STROKE_OPACITY_FLOOR,
                0.22,
            ),
            "shade_width": clamp_float(
                float(render_parameters["shade_width"]) * shade_scale,
                DENSE_SHADE_WIDTH_FLOOR,
                2.4,
            ),
            "shade_opacity": clamp_float(
                scale_density_opacity(float(render_parameters["shade_opacity"]), shade_scale),
                DENSE_SHADE_OPACITY_FLOOR,
                0.32,
            ),
            "jitter": clamp_float(
                float(render_parameters["jitter"]) * (1 + (1 - retrace_scale) * 0.35),
                0.02,
                0.3,
            ),
            "roughness": clamp_float(
                float(render_parameters["roughness"])
                * (1 + (1 - min(retrace_scale, shade_scale)) * 0.3),
                0.04,
                1.15,
            ),
        }
    )
    return render_parameters


def density_scale(count: int, baseline: int) -> float:
    if count <= baseline:
        return 1.0
    return math.sqrt(baseline / count)


def scale_density_opacity(opacity: float, density_scale_value: float) -> float:
    return opacity * math.sqrt(density_scale_value)


def mutate_int_parameter(
    value: Any,
    rng: random.Random,
    *,
    minimum: int,
    maximum: int,
    delta: int,
) -> int:
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        numeric_value = minimum
    return clamp_int(numeric_value + rng.randint(-delta, delta), minimum, maximum)


def mutate_float_parameter(
    value: Any,
    rng: random.Random,
    *,
    minimum: float,
    maximum: float,
    variance: float,
) -> float:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        numeric_value = minimum
    factor = 1 + rng.uniform(-variance, variance)
    return clamp_float(numeric_value * factor, minimum, maximum)


def candidate_seed(run_id: str, attempt: int) -> int:
    digest = hashlib.sha256(f"{run_id}:{attempt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def lineage_seed(
    *,
    run_id: str,
    generation_number: int,
    slot: int,
    origin_type: str,
    parent_id: str | None = None,
) -> int:
    parts = [run_id, str(generation_number), str(slot), origin_type]
    if parent_id is not None:
        parts.append(parent_id)
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def next_generation_immigrant_target(survivor_count: int, mode: str) -> int:
    if mode == "reroll":
        return NEXT_GENERATION_SIZE
    if survivor_count <= 2:
        return 4
    return 2


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def clamp_float(value: float, minimum: float, maximum: float) -> float:
    return round(max(minimum, min(maximum, value)), 4)


def candidate_artifact_path(
    *,
    run_id: str,
    generation_id: str,
    position: int,
    candidate_id: str,
) -> Path:
    return (
        Path("artifacts")
        / "candidates"
        / run_id
        / generation_id
        / f"{position:03d}-{candidate_id}.svg"
    )


def render_candidate_svg(
    source_path: Path,
    artifact_path: Path,
    render_parameters: dict[str, Any],
) -> None:
    render_sketch_svg(source_path, artifact_path, **render_parameters)


def validate_candidate_svg(
    artifact_path: Path,
    source_bounds: tuple[float, float, float, float],
) -> str:
    if not artifact_path.exists():
        raise ValueError("Rendered candidate artifact is missing.")
    data = artifact_path.read_bytes()
    if not data.strip():
        raise ValueError("Rendered candidate artifact is empty.")

    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError("Rendered candidate artifact is malformed SVG XML.") from error
    if local_name(root).lower() != "svg":
        raise ValueError("Rendered candidate artifact is missing an <svg> root.")

    sketch_paths = [
        element
        for element in root.iter()
        if element.get("d") and any(marker in element.attrib for marker in SKETCH_PATH_MARKERS)
        and local_name(element).lower() == "path"
    ]
    if not sketch_paths:
        raise ValueError("Rendered candidate is missing sketch paths.")

    visible_paths = [element for element in sketch_paths if is_visible_sketch_path(element)]
    if not visible_paths:
        raise ValueError("Rendered candidate has no visible sketch paths.")

    candidate_bounds = combine_bounds(
        [absolute_path_bounds(element.get("d", "")) for element in visible_paths]
    )
    if bounds_are_obviously_out_of_bounds(candidate_bounds, source_bounds):
        raise ValueError("Rendered candidate is obviously out of bounds.")

    return "Candidate SVG passed validation."


def is_visible_sketch_path(element: ET.Element) -> bool:
    style = style_to_dict(element.get("style"))
    display = style.get("display", element.get("display", "")).lower()
    visibility = style.get("visibility", element.get("visibility", "")).lower()
    stroke = style.get("stroke", element.get("stroke", ""))
    opacity = numeric_style_value(style, element, "opacity", 1.0)
    stroke_opacity = numeric_style_value(style, element, "stroke-opacity", 1.0)
    stroke_width = numeric_style_value(style, element, "stroke-width", 1.0)

    return (
        display != "none"
        and visibility != "hidden"
        and stroke.lower() != "none"
        and opacity > 0
        and stroke_opacity > 0
        and stroke_width > 0
    )


def numeric_style_value(
    style: dict[str, str],
    element: ET.Element,
    key: str,
    default: float,
) -> float:
    value = style.get(key, element.get(key))
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def combine_bounds(
    bounds: list[tuple[float, float, float, float]]
) -> tuple[float, float, float, float]:
    if not bounds:
        raise ValueError("no bounds to combine")
    min_x = min(item[0] for item in bounds)
    min_y = min(item[1] for item in bounds)
    max_x = max(item[2] for item in bounds)
    max_y = max(item[3] for item in bounds)
    if not all(math.isfinite(value) for value in (min_x, min_y, max_x, max_y)):
        raise ValueError("non-finite path bounds")
    return min_x, min_y, max_x, max_y


def bounds_are_obviously_out_of_bounds(
    candidate: tuple[float, float, float, float],
    source: tuple[float, float, float, float],
) -> bool:
    source_width = max(source[2] - source[0], 1.0)
    source_height = max(source[3] - source[1], 1.0)
    source_center_x = (source[0] + source[2]) / 2
    source_center_y = (source[1] + source[3]) / 2
    candidate_width = candidate[2] - candidate[0]
    candidate_height = candidate[3] - candidate[1]
    candidate_center_x = (candidate[0] + candidate[2]) / 2
    candidate_center_y = (candidate[1] + candidate[3]) / 2

    return (
        candidate_width > source_width * 50
        or candidate_height > source_height * 50
        or abs(candidate_center_x - source_center_x) > source_width * 25
        or abs(candidate_center_y - source_center_y) > source_height * 25
    )


def render_next_generation_group(
    db: sqlite3.Connection,
    workspace: Path,
    *,
    source_context: SourceContext,
    run_id: str,
    generation_id: str,
    generation_number: int,
    starting_position: int,
    target_ready_count: int,
    origin_type: str,
    genome_builder: Any,
) -> tuple[int, int]:
    ready_count = 0
    position = starting_position
    attempts = 0
    while ready_count < target_ready_count and attempts < MAX_NEXT_GENERATION_ATTEMPTS:
        attempts += 1
        slot = attempts
        candidate_id = new_uuid7()
        genome = genome_builder(slot)
        artifact_relative_path = candidate_artifact_path(
            run_id=run_id,
            generation_id=generation_id,
            position=position,
            candidate_id=candidate_id,
        )
        artifact_path = workspace / artifact_relative_path
        validation_status = "ready"
        validation_message: str | None = None
        byte_size: int | None = None
        sha256: str | None = None

        try:
            render_candidate_svg(
                source_context.source_path,
                artifact_path,
                genome["renderParameters"],
            )
            validation_message = validate_candidate_svg(
                artifact_path,
                source_context.source_bounds,
            )
            data = artifact_path.read_bytes()
            byte_size = len(data)
            sha256 = hashlib.sha256(data).hexdigest()
            ready_count += 1
        except Exception as error:
            validation_status = "failed"
            validation_message = str(error) or error.__class__.__name__
            if artifact_path.exists():
                data = artifact_path.read_bytes()
                byte_size = len(data)
                sha256 = hashlib.sha256(data).hexdigest()

        insert_candidate(
            db,
            candidate_id=candidate_id,
            run_id=run_id,
            generation_id=generation_id,
            generation_number=generation_number,
            position=position,
            origin_type=origin_type,
            genome=genome,
            artifact_path=artifact_relative_path.as_posix()
            if artifact_path.exists()
            else None,
            byte_size=byte_size,
            sha256=sha256,
            validation_status=validation_status,
            validation_message=validation_message,
        )
        position += 1

    return ready_count, position


def copy_survivor_carryover(
    db: sqlite3.Connection,
    workspace: Path,
    *,
    run_id: str,
    generation_id: str,
    generation_number: int,
    position: int,
    parent: ParentCandidate,
) -> None:
    source_path = checked_candidate_artifact_path(workspace, parent)
    candidate_id = new_uuid7()
    artifact_relative_path = candidate_artifact_path(
        run_id=run_id,
        generation_id=generation_id,
        position=position,
        candidate_id=candidate_id,
    )
    artifact_path = workspace / artifact_relative_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, artifact_path)
    data = artifact_path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if sha256 != parent.sha256:
        raise CandidateArtifactLineageError(
            f"Copied survivor artifact hash does not match parent candidate {parent.id}."
        )

    insert_candidate(
        db,
        candidate_id=candidate_id,
        run_id=run_id,
        generation_id=generation_id,
        generation_number=generation_number,
        position=position,
        origin_type="survivor_carryover",
        genome=build_survivor_carryover_genome(
            run_id=run_id,
            generation_number=generation_number,
            position=position,
            parent=parent,
        ),
        artifact_path=artifact_relative_path.as_posix(),
        byte_size=len(data),
        sha256=sha256,
        validation_status="ready",
        validation_message="Survivor artifact carried over from previous generation.",
    )


def checked_candidate_artifact_path(
    workspace: Path,
    parent: ParentCandidate,
) -> Path:
    relative_path = Path(parent.artifact_path)
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or relative_path.parts[:2] != ("artifacts", "candidates")
    ):
        raise CandidateArtifactLineageError(
            f"Parent candidate {parent.id} artifact path is not workspace-relative."
        )

    candidate_root = (workspace / "artifacts" / "candidates").resolve()
    artifact_path = (workspace / relative_path).resolve()
    try:
        artifact_path.relative_to(candidate_root)
    except ValueError as error:
        raise CandidateArtifactLineageError(
            f"Parent candidate {parent.id} artifact path points outside candidate artifacts."
        ) from error

    if not artifact_path.exists() or not artifact_path.is_file():
        raise CandidateArtifactLineageError(
            f"Parent candidate {parent.id} artifact is missing."
        )
    data = artifact_path.read_bytes()
    if len(data) != parent.byte_size:
        raise CandidateArtifactLineageError(
            f"Parent candidate {parent.id} artifact byte size does not match metadata."
        )
    if hashlib.sha256(data).hexdigest() != parent.sha256:
        raise CandidateArtifactLineageError(
            f"Parent candidate {parent.id} artifact hash does not match metadata."
        )
    return artifact_path


def insert_candidate(
    db: sqlite3.Connection,
    *,
    candidate_id: str,
    run_id: str,
    generation_id: str,
    generation_number: int,
    position: int,
    origin_type: str,
    genome: dict[str, Any],
    artifact_path: str | None,
    byte_size: int | None,
    sha256: str | None,
    validation_status: str,
    validation_message: str | None,
) -> None:
    db.execute(
        """
        INSERT INTO candidates (
            id,
            run_id,
            generation_id,
            generation_number,
            position,
            origin_type,
            genome_json,
            artifact_path,
            byte_size,
            sha256,
            validation_status,
            validation_message,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_id,
            run_id,
            generation_id,
            generation_number,
            position,
            origin_type,
            json.dumps(genome, sort_keys=True),
            artifact_path,
            byte_size,
            sha256,
            validation_status,
            validation_message,
            utc_now(),
        ),
    )


def generation_summary_from_row(
    db: sqlite3.Connection,
    generation: sqlite3.Row,
) -> GenerationSummary:
    rows = db.execute(
        """
        SELECT * FROM candidates
        WHERE generation_id = ?
        ORDER BY position
        """,
        (generation["id"],),
    ).fetchall()
    candidates = [candidate_summary_from_row(row) for row in rows]
    ready_count = sum(
        1 for candidate in candidates if candidate.validation_status == "ready"
    )
    failed_count = sum(
        1 for candidate in candidates if candidate.validation_status == "failed"
    )
    return GenerationSummary(
        id=generation["id"],
        run_id=generation["run_id"],
        generation_number=generation["generation_number"],
        status=generation["status"],
        total_candidate_count=len(candidates),
        ready_count=ready_count,
        failed_count=failed_count,
        candidates=candidates,
        created_at=generation["created_at"],
    )


def candidate_summary_from_row(row: sqlite3.Row) -> CandidateSummary:
    return CandidateSummary(
        id=row["id"],
        run_id=row["run_id"],
        generation_id=row["generation_id"],
        generation_number=row["generation_number"],
        position=row["position"],
        origin_type=row["origin_type"],
        genome=json.loads(row["genome_json"]),
        artifact_path=row["artifact_path"],
        byte_size=row["byte_size"],
        sha256=row["sha256"],
        validation_status=row["validation_status"],
        validation_message=row["validation_message"],
        created_at=row["created_at"],
    )


def generation_summary_to_api(summary: GenerationSummary) -> dict[str, Any]:
    return {
        "id": summary.id,
        "runId": summary.run_id,
        "generationNumber": summary.generation_number,
        "status": summary.status,
        "totalCandidateCount": summary.total_candidate_count,
        "readyCount": summary.ready_count,
        "failedCount": summary.failed_count,
        "createdAt": summary.created_at,
        "candidates": [
            {
                "id": candidate.id,
                "runId": candidate.run_id,
                "generationId": candidate.generation_id,
                "generationNumber": candidate.generation_number,
                "position": candidate.position,
                "originType": candidate.origin_type,
                "genome": candidate.genome,
                "artifactPath": candidate.artifact_path,
                "byteSize": candidate.byte_size,
                "sha256": candidate.sha256,
                "validationStatus": candidate.validation_status,
                "validationMessage": candidate.validation_message,
                "createdAt": candidate.created_at,
            }
            for candidate in summary.candidates
        ],
    }
