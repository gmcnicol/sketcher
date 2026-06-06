"""Run export jobs for survivor videos and social cuts."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import cairosvg

from .generations import UnknownRunError
from .workspace import connect, ensure_workspace, utc_now


ExportStatusValue = Literal["not_started", "queued", "running", "complete", "failed"]
ExportFileKind = Literal["full", "short"]

FULL_VIDEO_WIDTH = 3840
FULL_VIDEO_HEIGHT = 2160
SHORT_VIDEO_WIDTH = 2160
SHORT_VIDEO_HEIGHT = 3840
EXPORT_FPS = 30
HOLD_MILLISECONDS = 500
TRANSITION_MILLISECONDS = 500
SHORT_SECONDS = 60.0
SHORT_OVERLAP_SECONDS = 5.0
VIDEO_CRF = "18"
VIDEO_PRESET = "veryfast"

_JOBS: dict[Path, dict[str, ExportStatusValue]] = {}
_JOBS_LOCK = threading.Lock()


class SurvivorVideoExportError(ValueError):
    """Raised when a survivor video export cannot be prepared or served."""


class NoExportSurvivorsError(SurvivorVideoExportError):
    """Raised when a run has no active survivor decisions."""


class ExportNotReadyError(SurvivorVideoExportError):
    """Raised when an export file is requested before it exists."""


class ExportToolError(SurvivorVideoExportError):
    """Raised when the local video toolchain is unavailable."""


@dataclass(frozen=True)
class ExportSurvivor:
    index: int
    decision_at: str
    generation_number: int
    position: int
    candidate_id: str
    origin_type: str
    artifact_path: str
    sha256: str


@dataclass(frozen=True)
class ExportShort:
    index: int
    start_seconds: float
    end_seconds: float
    path: str | None
    byte_size: int | None
    sha256: str | None


@dataclass(frozen=True)
class SurvivorVideoExport:
    run_id: str
    status: ExportStatusValue
    survivor_count: int
    hold_milliseconds: int
    transition_milliseconds: int
    fps: int
    full_video_path: str | None
    full_video_byte_size: int | None
    full_video_sha256: str | None
    shorts: list[ExportShort]
    error: str | None
    created_at: str | None
    updated_at: str | None


@dataclass(frozen=True)
class ExportFile:
    path: Path
    sha256: str
    byte_size: int
    filename: str


def get_survivor_video_export(
    workspace: Path,
    run_id: str,
) -> SurvivorVideoExport:
    workspace = ensure_workspace(workspace)
    items = load_export_survivors(workspace, run_id)
    status_data = read_status_file(export_directory(workspace, run_id))
    status_value = status_from_status_data(
        export_directory(workspace, run_id),
        status_data,
    )
    status_value = restart_safe_status(workspace, run_id, status_value)
    return export_to_status(workspace, run_id, items, status_value, status_data)


def start_survivor_video_export(
    workspace: Path,
    run_id: str,
) -> SurvivorVideoExport:
    workspace = ensure_workspace(workspace)
    items = load_export_survivors(workspace, run_id)
    ensure_video_toolchain()
    directory = export_directory(workspace, run_id)
    status_data = read_status_file(directory)
    current_status = status_from_status_data(directory, status_data)
    current_status = restart_safe_status(workspace, run_id, current_status)
    if current_status in {"queued", "running", "complete"}:
        return export_to_status(workspace, run_id, items, current_status, status_data)

    write_status_file(
        directory,
        {
            "status": "queued",
            "runId": run_id,
            "survivorCount": len(items),
            "createdAt": status_data.get("createdAt") or utc_now(),
            "updatedAt": utc_now(),
            "error": None,
        },
    )
    with _JOBS_LOCK:
        _JOBS.setdefault(workspace, {})[run_id] = "queued"

    thread = threading.Thread(
        target=run_export_job,
        args=(workspace, run_id),
        name=f"survivor-video-export-{run_id[:8]}",
        daemon=True,
    )
    thread.start()
    return get_survivor_video_export(workspace, run_id)


def load_survivor_video_export_file(
    workspace: Path,
    run_id: str,
    kind: ExportFileKind,
    *,
    short_index: int | None = None,
) -> ExportFile:
    workspace = ensure_workspace(workspace)
    load_export_survivors(workspace, run_id)
    directory = export_directory(workspace, run_id)
    if status_from_status_data(directory, read_status_file(directory)) != "complete":
        raise ExportNotReadyError("Survivor video export is not complete yet.")

    if kind == "full":
        path = full_video_path(directory)
        filename = "run-survivors-over-time-4k.mp4"
    else:
        if short_index is None or short_index < 1:
            raise ExportNotReadyError("A valid short index is required.")
        path = shorts_directory(directory) / f"run-short-{short_index:02d}-4k-vertical.mp4"
        filename = f"run-survivor-short-{short_index:02d}-4k-vertical.mp4"

    checked_path = checked_export_path(workspace, path)
    if not checked_path.exists() or not checked_path.is_file():
        raise ExportNotReadyError("Requested survivor video export file is missing.")

    data = checked_path.read_bytes()
    return ExportFile(
        path=checked_path,
        sha256=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        filename=filename,
    )


def run_export_job(workspace: Path, run_id: str) -> None:
    directory = export_directory(workspace, run_id)
    status_data = read_status_file(directory)
    try:
        with _JOBS_LOCK:
            _JOBS.setdefault(workspace, {})[run_id] = "running"
        write_status_file(
            directory,
            {
                **status_data,
                "status": "running",
                "updatedAt": utc_now(),
                "error": None,
            },
        )
        build_survivor_video_export(workspace, run_id)
        status_data = read_status_file(directory)
        write_status_file(
            directory,
            {
                **status_data,
                "status": "complete",
                "updatedAt": utc_now(),
                "error": None,
            },
        )
        with _JOBS_LOCK:
            _JOBS.setdefault(workspace, {})[run_id] = "complete"
    except Exception as error:
        write_status_file(
            directory,
            {
                **status_data,
                "status": "failed",
                "updatedAt": utc_now(),
                "error": str(error),
            },
        )
        with _JOBS_LOCK:
            _JOBS.setdefault(workspace, {})[run_id] = "failed"


def build_survivor_video_export(workspace: Path, run_id: str) -> None:
    items = load_export_survivors(workspace, run_id)
    directory = export_directory(workspace, run_id)
    frames = [render_survivor_frame(workspace, directory, item) for item in items]
    segments: list[Path] = []
    for index, frame in enumerate(frames, start=1):
        segments.append(encode_hold_segment(directory, frame, index))
        if index < len(frames):
            segments.append(encode_transition_segment(directory, frame, frames[index], index))
    encode_full_video(directory, segments)
    encode_shorts(directory)
    write_export_manifest(workspace, run_id, items)


def load_export_survivors(workspace: Path, run_id: str) -> list[ExportSurvivor]:
    with connect(workspace) as db:
        run = db.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise UnknownRunError(f"Run {run_id} was not found.")
        rows = db.execute(
            """
            SELECT
                d.created_at AS decision_at,
                c.generation_number,
                c.position,
                c.id AS candidate_id,
                c.origin_type,
                c.artifact_path,
                c.sha256
            FROM candidate_decisions d
            JOIN candidates c ON c.id = d.candidate_id
            WHERE d.run_id = ?
              AND d.decision = 'survived'
              AND d.undone_at IS NULL
              AND c.validation_status = 'ready'
              AND c.artifact_path IS NOT NULL
              AND c.sha256 IS NOT NULL
            ORDER BY d.created_at, c.generation_number, c.position
            """,
            (run_id,),
        ).fetchall()

    if not rows:
        raise NoExportSurvivorsError(
            "Run has no active ready survivors to export."
        )

    return [
        ExportSurvivor(
            index=index,
            decision_at=row["decision_at"],
            generation_number=row["generation_number"],
            position=row["position"],
            candidate_id=row["candidate_id"],
            origin_type=row["origin_type"],
            artifact_path=row["artifact_path"],
            sha256=row["sha256"],
        )
        for index, row in enumerate(rows, start=1)
    ]


def render_survivor_frame(
    workspace: Path,
    directory: Path,
    item: ExportSurvivor,
) -> Path:
    source = checked_candidate_artifact_path(workspace, item)
    frame_path = frames_directory(directory) / f"{item.index:04d}-{item.candidate_id}.png"
    if frame_path.exists():
        return frame_path
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = frame_path.with_suffix(".tmp.png")
    try:
        cairosvg.svg2png(
            url=str(source),
            write_to=str(temporary_path),
            output_width=FULL_VIDEO_WIDTH,
            output_height=FULL_VIDEO_HEIGHT,
            background_color="white",
        )
        temporary_path.replace(frame_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise
    return frame_path


def encode_hold_segment(directory: Path, frame_path: Path, index: int) -> Path:
    segment_path = segments_directory(directory) / f"{index:04d}-hold.mp4"
    if segment_path.exists():
        return segment_path
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-framerate",
            str(EXPORT_FPS),
            "-i",
            str(frame_path),
            "-frames:v",
            str(milliseconds_to_frames(HOLD_MILLISECONDS)),
            "-vf",
            "format=yuv420p",
            "-an",
            *h264_output_args(),
            str(segment_path),
        ]
    )
    return segment_path


def encode_transition_segment(
    directory: Path,
    frame_a: Path,
    frame_b: Path,
    index: int,
) -> Path:
    segment_path = segments_directory(directory) / f"{index:04d}-to-{index + 1:04d}.mp4"
    if segment_path.exists():
        return segment_path
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    transition_seconds = TRANSITION_MILLISECONDS / 1000
    run_ffmpeg(
        [
            "-loop",
            "1",
            "-framerate",
            str(EXPORT_FPS),
            "-i",
            str(frame_a),
            "-loop",
            "1",
            "-framerate",
            str(EXPORT_FPS),
            "-i",
            str(frame_b),
            "-filter_complex",
            "[0:v]format=yuv420p[v0];"
            "[1:v]format=yuv420p[v1];"
            f"[v0][v1]xfade=transition=fade:duration={transition_seconds}:offset=0,"
            "format=yuv420p[v]",
            "-map",
            "[v]",
            "-frames:v",
            str(milliseconds_to_frames(TRANSITION_MILLISECONDS)),
            "-an",
            *h264_output_args(),
            str(segment_path),
        ]
    )
    return segment_path


def encode_full_video(directory: Path, segments: list[Path]) -> Path:
    path = full_video_path(directory)
    if path.exists():
        return path
    concat_path = directory / "segments.ffconcat"
    concat_path.write_text(
        "ffconcat version 1.0\n"
        + "".join(f"file '{segment.resolve()}'\n" for segment in segments),
        encoding="utf-8",
    )
    run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(path),
        ]
    )
    return path


def encode_shorts(directory: Path) -> list[Path]:
    full_path = full_video_path(directory)
    duration = video_duration(full_path)
    starts = short_start_times(duration)
    encoded: list[Path] = []
    shorts_directory(directory).mkdir(parents=True, exist_ok=True)
    for index, start in enumerate(starts, start=1):
        path = shorts_directory(directory) / f"run-short-{index:02d}-4k-vertical.mp4"
        if not path.exists():
            run_ffmpeg(
                [
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    str(full_path),
                    "-t",
                    f"{SHORT_SECONDS:.3f}",
                    "-vf",
                    f"scale={SHORT_VIDEO_WIDTH}:{SHORT_VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
                    f"pad={SHORT_VIDEO_WIDTH}:{SHORT_VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:white,"
                    "setsar=1,format=yuv420p",
                    "-an",
                    *h264_output_args(),
                    str(path),
                ]
            )
        encoded.append(path)
    return encoded


def write_export_manifest(
    workspace: Path,
    run_id: str,
    items: list[ExportSurvivor],
) -> None:
    directory = export_directory(workspace, run_id)
    duration = video_duration(full_video_path(directory))
    manifest_path(directory).write_text(
        json.dumps(
            {
                "runId": run_id,
                "survivorCount": len(items),
                "fps": EXPORT_FPS,
                "holdMilliseconds": HOLD_MILLISECONDS,
                "transitionMilliseconds": TRANSITION_MILLISECONDS,
                "fullVideo": path_to_workspace_relative(
                    workspace,
                    full_video_path(directory),
                ),
                "fullVideoDurationSeconds": round(duration, 3),
                "shortSeconds": SHORT_SECONDS,
                "shortOverlapSeconds": SHORT_OVERLAP_SECONDS,
                "shorts": [
                    short.__dict__
                    for short in export_shorts(workspace, directory, duration)
                ],
                "survivors": [item.__dict__ for item in items],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def export_to_status(
    workspace: Path,
    run_id: str,
    items: list[ExportSurvivor],
    status_value: ExportStatusValue,
    status_data: dict[str, object],
) -> SurvivorVideoExport:
    directory = export_directory(workspace, run_id)
    full_path = full_video_path(directory)
    full_size, full_sha = file_metadata(full_path) if full_path.exists() else (None, None)
    duration = video_duration(full_path) if full_path.exists() else None
    return SurvivorVideoExport(
        run_id=run_id,
        status=status_value,
        survivor_count=len(items),
        hold_milliseconds=HOLD_MILLISECONDS,
        transition_milliseconds=TRANSITION_MILLISECONDS,
        fps=EXPORT_FPS,
        full_video_path=path_to_workspace_relative(workspace, full_path)
        if full_path.exists()
        else None,
        full_video_byte_size=full_size,
        full_video_sha256=full_sha,
        shorts=export_shorts(workspace, directory, duration) if full_path.exists() else [],
        error=string_or_none(status_data.get("error")),
        created_at=string_or_none(status_data.get("createdAt")),
        updated_at=string_or_none(status_data.get("updatedAt")),
    )


def export_shorts(
    workspace: Path,
    directory: Path,
    duration: float | None,
) -> list[ExportShort]:
    starts = short_start_times(duration) if duration is not None else []
    shorts: list[ExportShort] = []
    for index, start in enumerate(starts, start=1):
        path = shorts_directory(directory) / f"run-short-{index:02d}-4k-vertical.mp4"
        size, sha = file_metadata(path) if path.exists() else (None, None)
        shorts.append(
            ExportShort(
                index=index,
                start_seconds=round(start, 3),
                end_seconds=round(min(start + SHORT_SECONDS, duration), 3)
                if duration is not None
                else round(start + SHORT_SECONDS, 3),
                path=path_to_workspace_relative(workspace, path) if path.exists() else None,
                byte_size=size,
                sha256=sha,
            )
        )
    return shorts


def status_from_status_data(
    directory: Path,
    status_data: dict[str, object],
) -> ExportStatusValue:
    status = status_data.get("status")
    if status in {"queued", "running", "complete", "failed"}:
        if status == "complete" and not full_video_path(directory).exists():
            return "failed"
        return status  # type: ignore[return-value]
    if full_video_path(directory).exists():
        return "complete"
    return "not_started"


def restart_safe_status(
    workspace: Path,
    run_id: str,
    status_value: ExportStatusValue,
) -> ExportStatusValue:
    if status_value not in {"queued", "running"}:
        return status_value
    with _JOBS_LOCK:
        job_status = _JOBS.get(workspace, {}).get(run_id)
    if job_status in {"queued", "running"}:
        return status_value
    if full_video_path(export_directory(workspace, run_id)).exists():
        return "complete"
    return "failed"


def read_status_file(directory: Path) -> dict[str, object]:
    path = status_path(directory)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_status_file(directory: Path, status_data: dict[str, object]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = status_path(directory)
    temporary_path = path.with_suffix(".tmp.json")
    temporary_path.write_text(json.dumps(status_data, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def checked_candidate_artifact_path(workspace: Path, item: ExportSurvivor) -> Path:
    relative_path = Path(item.artifact_path)
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or relative_path.parts[:2] != ("artifacts", "candidates")
    ):
        raise SurvivorVideoExportError(
            f"Survivor {item.candidate_id} artifact path is not workspace-relative."
        )
    candidate_root = (workspace / "artifacts" / "candidates").resolve()
    artifact_path = (workspace / relative_path).resolve()
    try:
        artifact_path.relative_to(candidate_root)
    except ValueError as error:
        raise SurvivorVideoExportError(
            f"Survivor {item.candidate_id} artifact path points outside candidate artifacts."
        ) from error
    if not artifact_path.exists() or not artifact_path.is_file():
        raise SurvivorVideoExportError(
            f"Survivor {item.candidate_id} artifact file is missing."
        )
    data = artifact_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != item.sha256:
        raise SurvivorVideoExportError(
            f"Survivor {item.candidate_id} artifact hash does not match metadata."
        )
    return artifact_path


def checked_export_path(workspace: Path, path: Path) -> Path:
    export_root = (workspace / "artifacts" / "exports").resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(export_root)
    except ValueError as error:
        raise ExportNotReadyError("Export path points outside the export directory.") from error
    return resolved_path


def ensure_video_toolchain() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise ExportToolError("ffmpeg and ffprobe are required to export survivor videos.")


def run_ffmpeg(args: list[str]) -> None:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or str(error)
        raise SurvivorVideoExportError(f"ffmpeg failed: {detail}") from error


def video_duration(video_path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-hide_banner",
                "-loglevel",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or str(error)
        raise SurvivorVideoExportError(f"ffprobe failed: {detail}") from error
    return float(result.stdout.strip())


def h264_output_args() -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        VIDEO_PRESET,
        "-crf",
        VIDEO_CRF,
        "-r",
        str(EXPORT_FPS),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]


def milliseconds_to_frames(milliseconds: int) -> int:
    return max(1, round(EXPORT_FPS * milliseconds / 1000))


def short_start_times(duration: float) -> list[float]:
    if duration <= SHORT_SECONDS:
        return [0.0]
    step = SHORT_SECONDS - SHORT_OVERLAP_SECONDS
    count = math.ceil((duration - SHORT_SECONDS) / step) + 1
    starts = [min(index * step, duration - SHORT_SECONDS) for index in range(count)]
    deduped: list[float] = []
    for start in starts:
        if not deduped or abs(start - deduped[-1]) > 0.01:
            deduped.append(start)
    return deduped


def file_metadata(path: Path) -> tuple[int, str]:
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def path_to_workspace_relative(workspace: Path, path: Path) -> str:
    return path.resolve().relative_to(workspace.resolve()).as_posix()


def export_directory(workspace: Path, run_id: str) -> Path:
    return workspace / "artifacts" / "exports" / "survivor-videos" / run_id


def frames_directory(directory: Path) -> Path:
    return directory / "frames-4k-landscape"


def segments_directory(directory: Path) -> Path:
    return directory / "segments-4k-landscape"


def shorts_directory(directory: Path) -> Path:
    return directory / "youtube-shorts-4k"


def full_video_path(directory: Path) -> Path:
    return directory / "run-survivors-over-time-4k.mp4"


def manifest_path(directory: Path) -> Path:
    return directory / "manifest.json"


def status_path(directory: Path) -> Path:
    return directory / "status.json"


def string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
