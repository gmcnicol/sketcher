import random
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from sketcher_model_builder.generator import (
    human_stroke_routes_for_pass,
    order_routes_by_nearest_endpoint,
    render_sketch_svg,
    route_axis,
    stroke_routes_from_path_d,
)


def style_to_dict(style: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if not style:
        return values
    for part in style.split(";"):
        if ":" in part:
            key, value = part.split(":", 1)
            values[key] = value
    return values


def test_generator_writes_svg(tmp_path: Path) -> None:
    input_svg = Path(__file__).parent / "fixtures" / "tom2.svg"
    output_svg = tmp_path / "tom2.sketch.svg"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sketcher_model_builder",
            str(input_svg),
            str(output_svg),
            "--repeats",
            "1",
            "--shade-strokes",
            "1",
            "--seed",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert output_svg.exists()
    output = output_svg.read_text()
    assert "<svg" in output
    assert "inkscape" not in output
    assert "sodipodi" not in output
    assert "<metadata" not in output
    assert "width=" not in output
    assert "height=" not in output
    assert "viewBox=" in output


def test_stroke_mode_keeps_scanned_subpaths_separate(tmp_path: Path) -> None:
    input_svg = tmp_path / "source.svg"
    output_svg = tmp_path / "out.svg"
    input_svg.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
  <path id="scan" d="M 0 0 L 10 0 M 100 100 L 110 100" style="fill:none;stroke:#000;stroke-width:1" />
</svg>
""",
        encoding="utf-8",
    )

    routes = stroke_routes_from_path_d("M 0 0 L 10 0 M 100 100 L 110 100")
    assert routes == [[(0.0, 0.0), (10.0, 0.0)], [(100.0, 100.0), (110.0, 100.0)]]

    render_sketch_svg(
        input_svg,
        output_svg,
        mode="stroke",
        repeats=1,
        shade_strokes=0,
        jitter=0,
        roughness=0,
        seed=1,
    )

    root = ET.parse(output_svg).getroot()
    sketch_paths = [
        element
        for element in root.iter()
        if element.get("data-sketcher-pass") == "1"
    ]
    assert len(sketch_paths) == 1
    rendered_d = sketch_paths[0].get("d", "")
    assert rendered_d.count("M") == 2
    assert "100 100" in rendered_d


def test_fill_mode_preserves_scanned_stroke_subpaths(tmp_path: Path) -> None:
    input_svg = tmp_path / "source.svg"
    output_svg = tmp_path / "out.svg"
    input_svg.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
  <path id="scan" d="M 0 0 L 10 0 M 100 100 L 110 100" style="fill:none;stroke:#000;stroke-width:1" />
</svg>
""",
        encoding="utf-8",
    )

    render_sketch_svg(
        input_svg,
        output_svg,
        mode="fill",
        repeats=1,
        shade_strokes=8,
        jitter=0,
        roughness=0,
        seed=1,
    )

    root = ET.parse(output_svg).getroot()
    sketch_paths = [
        element
        for element in root.iter()
        if element.get("data-sketcher-pass") == "1"
    ]
    fill_paths = [
        element
        for element in root.iter()
        if element.get("data-sketcher-flow-pass")
        or element.get("data-sketcher-pressure-pass")
        or element.get("data-sketcher-vertical-flow-pass")
    ]

    assert len(sketch_paths) == 1
    assert sketch_paths[0].get("d", "").count("M") == 2
    assert fill_paths == []


def test_flick_taper_emits_three_randomized_paths_per_segment(tmp_path: Path) -> None:
    input_svg = tmp_path / "source.svg"
    output_svg = tmp_path / "out.svg"
    input_svg.write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 10">
  <path id="line" d="M 0 5 L 10 5 L 20 5 L 30 5" style="fill:none;stroke:#000;stroke-width:1" />
</svg>
""",
        encoding="utf-8",
    )

    render_sketch_svg(
        input_svg,
        output_svg,
        mode="stroke",
        repeats=1,
        shade_strokes=0,
        jitter=0.05,
        roughness=0.05,
        stroke_width=1.0,
        opacity=0.5,
        seed=3,
        flick_strength=1,
        flick_bias="start",
        flick_curve="linear",
        flick_probability=1,
        flick_min_width=0.08,
        flick_min_opacity=0.04,
    )

    root = ET.parse(output_svg).getroot()
    segment_paths = [
        element
        for element in root.iter()
        if element.get("data-sketcher-pass") == "1"
    ]

    assert len(segment_paths) == 9
    assert all("-segment-" in (element.get("id") or "") for element in segment_paths)

    segment_widths: dict[str, list[float]] = {"1": [], "2": [], "3": []}
    segment_opacities: dict[str, list[float]] = {"1": [], "2": [], "3": []}
    segment_ds: dict[str, set[str]] = {"1": set(), "2": set(), "3": set()}
    for element in segment_paths:
        segment_index = (element.get("id") or "").split("-segment-", 1)[1].split("-", 1)[0]
        style = style_to_dict(element.get("style"))
        segment_widths[segment_index].append(float(style["stroke-width"]))
        segment_opacities[segment_index].append(float(style["stroke-opacity"]))
        segment_ds[segment_index].add(element.get("d", ""))

    assert all(len(values) == 3 for values in segment_widths.values())
    assert all(len(values) == 3 for values in segment_opacities.values())
    assert all(len(values) == 3 for values in segment_ds.values())
    assert min(segment_widths["1"]) > max(segment_widths["2"])
    assert min(segment_widths["2"]) > max(segment_widths["3"])
    assert min(segment_opacities["1"]) > max(segment_opacities["2"])
    assert min(segment_opacities["2"]) > max(segment_opacities["3"])
    assert min(min(values) for values in segment_widths.values()) >= 0.08
    assert min(min(values) for values in segment_opacities.values()) >= 0.04


def test_stroke_routes_can_be_ordered_by_nearest_endpoint() -> None:
    first = [(0.0, 0.0), (10.0, 0.0)]
    far = [(100.0, 100.0), (110.0, 100.0)]
    near_reversed = [(22.0, 0.0), (12.0, 0.0)]

    ordered = order_routes_by_nearest_endpoint(
        [first, far, near_reversed],
        distance_weight=1.0,
    )

    assert ordered == [first, near_reversed, far]

    source_ordered = order_routes_by_nearest_endpoint(
        [first, far, near_reversed],
        distance_weight=0.0,
    )
    assert source_ordered == [first, far, near_reversed]


def test_stroke_route_axis_uses_larger_span() -> None:
    assert route_axis([(0.0, 0.0), (0.0, 20.0), (4.0, 22.0)]) == "vertical"
    assert route_axis([(0.0, 0.0), (20.0, 0.0), (22.0, 4.0)]) == "horizontal"


def test_stroke_passes_can_draw_mutable_fragments_in_source_direction() -> None:
    route = [(0.0, 0.0), (20.0, 0.0), (40.0, 0.0), (60.0, 0.0)]

    fragments = human_stroke_routes_for_pass(
        [route],
        random.Random(3),
        1,
        fragment_min=0.2,
        fragment_max=0.45,
        fragment_probability=1.0,
        full_retrace_interval=0,
    )

    assert len(fragments) == 1
    fragment = fragments[0]
    assert route[0] != fragment[0]
    assert route[-1] != fragment[-1]
    assert fragment[0][0] < fragment[-1][0]
