import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from sketcher_model_builder.generator import render_sketch_svg, stroke_routes_from_path_d


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
