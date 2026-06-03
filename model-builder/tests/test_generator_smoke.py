import subprocess
import sys
from pathlib import Path


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
