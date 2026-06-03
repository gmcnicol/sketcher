from __future__ import annotations

import argparse
import math
import random
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

from sketcher_model_builder.generator import (
    SVG_NS,
    clean_svg_tree_for_export,
    dict_to_style,
    local_name,
    register_namespaces,
    route_length,
    sample_path_subpaths,
    smooth_path_from_points,
    style_to_dict,
    style_value,
)


def is_stroke_path(element: ET.Element) -> bool:
    return (
        local_name(element) == "path"
        and bool(element.get("d"))
        and style_value(element, "fill", "none") == "none"
        and style_value(element, "stroke", "none") != "none"
    )


def turn_angle_degrees(
    previous: tuple[float, float],
    current: tuple[float, float],
    next_point: tuple[float, float],
) -> float:
    left = (current[0] - previous[0], current[1] - previous[1])
    right = (next_point[0] - current[0], next_point[1] - current[1])
    left_length = math.hypot(*left)
    right_length = math.hypot(*right)
    if left_length == 0 or right_length == 0:
        return 0

    dot = left[0] * right[0] + left[1] * right[1]
    cosine = max(-1.0, min(1.0, dot / (left_length * right_length)))
    return math.degrees(math.acos(cosine))


def split_route(
    route: list[tuple[float, float]],
    *,
    min_length: float,
    max_length: float,
    angle_threshold: float,
    min_points: int,
) -> list[list[tuple[float, float]]]:
    if len(route) < max(2, min_points):
        return [route] if len(route) >= 2 else []

    strokes: list[list[tuple[float, float]]] = []
    current = [route[0]]
    current_length = 0.0

    for index in range(1, len(route)):
        previous = route[index - 1]
        point = route[index]
        current.append(point)
        current_length += math.hypot(point[0] - previous[0], point[1] - previous[1])

        if len(current) < min_points or current_length < min_length:
            continue

        sharp_turn = False
        if index + 1 < len(route):
            sharp_turn = (
                turn_angle_degrees(route[index - 1], route[index], route[index + 1])
                >= angle_threshold
            )

        if current_length >= max_length or sharp_turn:
            strokes.append(current)
            current = [point]
            current_length = 0.0

    if len(current) >= 2:
        if strokes and route_length(current) < min_length * 0.45:
            strokes[-1].extend(current[1:])
        else:
            strokes.append(current)

    return strokes


def split_path_routes(
    d: str,
    *,
    curve_steps: int,
    min_length: float,
    max_length: float,
    angle_threshold: float,
    min_points: int,
) -> list[list[tuple[float, float]]]:
    strokes: list[list[tuple[float, float]]] = []
    for route in sample_path_subpaths(d, curve_steps=curve_steps):
        strokes.extend(
            split_route(
                route,
                min_length=min_length,
                max_length=max_length,
                angle_threshold=angle_threshold,
                min_points=min_points,
            )
        )
    return strokes


def stroke_style_for_split(source: ET.Element) -> str:
    style = style_to_dict(source.get("style"))
    style["fill"] = "none"
    style.setdefault("stroke", source.get("stroke", "#000000"))
    style.setdefault("stroke-linecap", "round")
    style.setdefault("stroke-linejoin", "round")
    return dict_to_style(style)


def split_trace_svg(
    input_path: Path,
    output_path: Path,
    *,
    curve_steps: int = 10,
    min_length: float = 28,
    max_length: float = 120,
    angle_threshold: float = 128,
    min_points: int = 6,
) -> tuple[int, int]:
    register_namespaces()
    tree = ET.parse(input_path)
    root = tree.getroot()
    replaced_count, split_count = split_trace_tree(
        root,
        curve_steps=curve_steps,
        min_length=min_length,
        max_length=max_length,
        angle_threshold=angle_threshold,
        min_points=min_points,
    )
    clean_svg_tree_for_export(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="UTF-8", xml_declaration=True)
    return replaced_count, split_count


def split_trace_svg_bytes(
    data: bytes,
    *,
    curve_steps: int = 10,
    min_length: float = 28,
    max_length: float = 120,
    angle_threshold: float = 128,
    min_points: int = 6,
) -> tuple[bytes, int, int]:
    register_namespaces()
    root = ET.fromstring(data)
    replaced_count, split_count = split_trace_tree(
        root,
        curve_steps=curve_steps,
        min_length=min_length,
        max_length=max_length,
        angle_threshold=angle_threshold,
        min_points=min_points,
    )
    clean_svg_tree_for_export(root)
    return (
        ET.tostring(root, encoding="utf-8", xml_declaration=True),
        replaced_count,
        split_count,
    )


def split_trace_tree(
    root: ET.Element,
    *,
    curve_steps: int,
    min_length: float,
    max_length: float,
    angle_threshold: float,
    min_points: int,
) -> tuple[int, int]:
    parent_map = {child: parent for parent in root.iter() for child in parent}
    paths = [element for element in root.iter() if is_stroke_path(element)]

    if not paths:
        return 0, 0

    replaced_count = 0
    split_count = 0
    rng = random.Random(0)

    for path in paths:
        parent = parent_map.get(path)
        if parent is None:
            continue

        source_routes = sample_path_subpaths(path.get("d", ""), curve_steps=curve_steps)
        strokes: list[list[tuple[float, float]]] = []
        for route in source_routes:
            strokes.extend(
                split_route(
                    route,
                    min_length=min_length,
                    max_length=max_length,
                    angle_threshold=angle_threshold,
                    min_points=min_points,
                )
            )
        if len(strokes) <= len(source_routes):
            continue

        index = list(parent).index(path)
        base_id = path.get("id", f"stroke-{replaced_count + 1}")
        style = stroke_style_for_split(path)
        template_attrib = {
            key: value
            for key, value in path.attrib.items()
            if key not in {"d", "id", "style"}
        }

        parent.remove(path)
        for stroke_index, points in enumerate(strokes, start=1):
            split_path = ET.Element(f"{{{SVG_NS}}}path")
            split_path.attrib.update(deepcopy(template_attrib))
            split_path.set("id", f"{base_id}-substroke-{stroke_index:04d}")
            split_path.set("style", style)
            split_path.set("d", smooth_path_from_points(points, rng, jitter=0, roughness=0))
            split_path.set("data-sketcher-substroke", str(stroke_index))
            parent.insert(index + stroke_index - 1, split_path)

        replaced_count += 1
        split_count += len(strokes)

    return replaced_count, split_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split long Inkscape traced stroke paths into shorter open sub-strokes."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--curve-steps", type=int, default=10)
    parser.add_argument("--min-length", type=float, default=28)
    parser.add_argument("--max-length", type=float, default=120)
    parser.add_argument("--angle-threshold", type=float, default=128)
    parser.add_argument("--min-points", type=int, default=6)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        replaced_count, split_count = split_trace_svg(
            args.input,
            args.output,
            curve_steps=args.curve_steps,
            min_length=args.min_length,
            max_length=args.max_length,
            angle_threshold=args.angle_threshold,
            min_points=args.min_points,
        )
    except (ET.ParseError, ValueError) as error:
        raise SystemExit(str(error)) from error

    print(
        f"Wrote {args.output} by splitting {replaced_count} path(s) "
        f"into {split_count} sub-stroke path(s)"
    )


if __name__ == "__main__":
    main()
