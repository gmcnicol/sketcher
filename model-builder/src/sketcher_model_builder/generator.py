#!/usr/bin/env python3
"""Create a pencil-sketch style SVG by retracing path elements with jitter."""

from __future__ import annotations

import argparse
import math
import random
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path


SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
SODIPODI_NS = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
XLINK_NS = "http://www.w3.org/1999/xlink"
EDITOR_METADATA_NAMESPACES = {INKSCAPE_NS, SODIPODI_NS}
EXPORT_BOUNDING_TAGS = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
NON_RENDERED_CONTAINER_TAGS = {"clipPath", "defs", "mask", "metadata", "pattern", "symbol"}

PATH_TOKEN_RE = re.compile(
    r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"
)
TRANSFORM_RE = re.compile(r"([A-Za-z]+)\(([^)]*)\)")


def register_namespaces() -> None:
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("inkscape", INKSCAPE_NS)
    ET.register_namespace("sodipodi", SODIPODI_NS)
    ET.register_namespace("xlink", XLINK_NS)


def is_number(token: str) -> bool:
    return not token[:1].isalpha()


def fmt_number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    if text == "-0":
        return "0"
    return text


def jitter_path_d(d: str, rng: random.Random, amount: float) -> str:
    """Jitter path coordinate tokens while preserving command letters.

    Relative and absolute path data both tolerate small numeric perturbations.
    Arc flags are intentionally uncommon in logo paths; when present, this
    keeps 0/1 flag-like values stable if they follow an arc command.
    """
    tokens = PATH_TOKEN_RE.findall(d)
    output: list[str] = []
    current_command = ""
    arc_param_index = 0

    for token in tokens:
        if not is_number(token):
            current_command = token
            arc_param_index = 0
            output.append(token)
            continue

        value = float(token)
        should_jitter = True

        if current_command in {"A", "a"}:
            # Arc parameter order: rx ry x-axis-rotation large-arc-flag
            # sweep-flag x y. Flags must remain binary.
            arc_param_index = (arc_param_index % 7) + 1
            should_jitter = arc_param_index not in {4, 5}

        if should_jitter:
            value += rng.gauss(0, amount)

        output.append(fmt_number(value))

    return " ".join(output)


def style_to_dict(style: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if not style:
        return values

    for part in style.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        values[key.strip()] = value.strip()
    return values


def style_value(element: ET.Element, key: str, default: str | None = None) -> str | None:
    if key in element.attrib:
        return element.get(key)
    return style_to_dict(element.get("style")).get(key, default)


def dict_to_style(values: dict[str, str]) -> str:
    return ";".join(f"{key}:{value}" for key, value in values.items())


def hide_element(element: ET.Element) -> None:
    style = style_to_dict(element.get("style"))
    style["display"] = "none"
    element.set("style", dict_to_style(style))


def append_after(parent: ET.Element, existing: ET.Element, new: ET.Element) -> None:
    children = list(parent)
    parent.insert(children.index(existing) + 1, new)


def local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def namespace_name(name: str) -> str | None:
    if name.startswith("{"):
        return name[1:].split("}", 1)[0]
    return None


def element_namespace(element: ET.Element) -> str | None:
    return namespace_name(element.tag)


def points_attr_to_pairs(points: str) -> list[tuple[float, float]]:
    numbers = [float(value) for value in re.findall(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?", points)]
    return [(numbers[index], numbers[index + 1]) for index in range(0, len(numbers) - 1, 2)]


def basic_shape_to_path_d(element: ET.Element) -> str | None:
    tag = local_name(element)
    if tag == "rect":
        x = float(element.get("x", "0"))
        y = float(element.get("y", "0"))
        width = float(element.get("width", "0"))
        height = float(element.get("height", "0"))
        if width <= 0 or height <= 0:
            return None
        return f"M {fmt_number(x)} {fmt_number(y)} H {fmt_number(x + width)} V {fmt_number(y + height)} H {fmt_number(x)} Z"
    if tag == "circle":
        cx = float(element.get("cx", "0"))
        cy = float(element.get("cy", "0"))
        r = float(element.get("r", "0"))
        if r <= 0:
            return None
        return f"M {fmt_number(cx - r)} {fmt_number(cy)} A {fmt_number(r)} {fmt_number(r)} 0 1 0 {fmt_number(cx + r)} {fmt_number(cy)} A {fmt_number(r)} {fmt_number(r)} 0 1 0 {fmt_number(cx - r)} {fmt_number(cy)} Z"
    if tag == "ellipse":
        cx = float(element.get("cx", "0"))
        cy = float(element.get("cy", "0"))
        rx = float(element.get("rx", "0"))
        ry = float(element.get("ry", "0"))
        if rx <= 0 or ry <= 0:
            return None
        return f"M {fmt_number(cx - rx)} {fmt_number(cy)} A {fmt_number(rx)} {fmt_number(ry)} 0 1 0 {fmt_number(cx + rx)} {fmt_number(cy)} A {fmt_number(rx)} {fmt_number(ry)} 0 1 0 {fmt_number(cx - rx)} {fmt_number(cy)} Z"
    if tag == "line":
        x1 = float(element.get("x1", "0"))
        y1 = float(element.get("y1", "0"))
        x2 = float(element.get("x2", "0"))
        y2 = float(element.get("y2", "0"))
        return f"M {fmt_number(x1)} {fmt_number(y1)} L {fmt_number(x2)} {fmt_number(y2)}"
    if tag in {"polyline", "polygon"}:
        pairs = points_attr_to_pairs(element.get("points", ""))
        if not pairs:
            return None
        parts = ["M", fmt_number(pairs[0][0]), fmt_number(pairs[0][1])]
        for x, y in pairs[1:]:
            parts.extend(["L", fmt_number(x), fmt_number(y)])
        if tag == "polygon":
            parts.append("Z")
        return " ".join(parts)
    return None


def normalize_basic_shapes(root: ET.Element) -> None:
    parent_map = {child: parent for parent in root.iter() for child in parent}
    for element in list(parent_map):
        if local_name(element) not in {"rect", "circle", "ellipse", "line", "polyline", "polygon"}:
            continue
        d = basic_shape_to_path_d(element)
        parent = parent_map.get(element)
        if d is None or parent is None:
            continue
        path = ET.Element(f"{{{SVG_NS}}}path")
        for key, value in element.attrib.items():
            if key not in {"x", "y", "width", "height", "cx", "cy", "r", "rx", "ry", "x1", "y1", "x2", "y2", "points"}:
                path.set(key, value)
        path.set("d", d)
        path.set("id", f"{element.get('id', local_name(element))}-as-path")
        append_after(parent, element, path)
        hide_element(element)


Matrix = tuple[float, float, float, float, float, float]


def matrix_multiply(left: Matrix, right: Matrix) -> Matrix:
    la, lb, lc, ld, le, lf = left
    ra, rb, rc, rd, re, rf = right
    return (
        la * ra + lc * rb,
        lb * ra + ld * rb,
        la * rc + lc * rd,
        lb * rc + ld * rd,
        la * re + lc * rf + le,
        lb * re + ld * rf + lf,
    )


def apply_matrix(matrix: Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def parse_transform_numbers(value: str) -> list[float]:
    return [
        float(number)
        for number in re.findall(
            r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?",
            value,
        )
    ]


def transform_to_matrix(transform: str | None) -> Matrix:
    matrix: Matrix = (1, 0, 0, 1, 0, 0)
    if not transform:
        return matrix

    for function, raw_values in TRANSFORM_RE.findall(transform):
        values = parse_transform_numbers(raw_values)
        name = function.lower()
        next_matrix: Matrix | None = None

        if name == "matrix" and len(values) >= 6:
            next_matrix = (
                values[0],
                values[1],
                values[2],
                values[3],
                values[4],
                values[5],
            )
        elif name == "translate" and values:
            next_matrix = (1, 0, 0, 1, values[0], values[1] if len(values) > 1 else 0)
        elif name == "scale" and values:
            sx = values[0]
            sy = values[1] if len(values) > 1 else sx
            next_matrix = (sx, 0, 0, sy, 0, 0)
        elif name == "rotate" and values:
            angle = math.radians(values[0])
            cos_value = math.cos(angle)
            sin_value = math.sin(angle)
            rotation: Matrix = (cos_value, sin_value, -sin_value, cos_value, 0, 0)
            if len(values) >= 3:
                cx, cy = values[1], values[2]
                next_matrix = matrix_multiply(
                    matrix_multiply((1, 0, 0, 1, cx, cy), rotation),
                    (1, 0, 0, 1, -cx, -cy),
                )
            else:
                next_matrix = rotation

        if next_matrix is not None:
            matrix = matrix_multiply(matrix, next_matrix)

    return matrix


def transformed_bounds(
    bounds: tuple[float, float, float, float],
    matrix: Matrix,
) -> tuple[float, float, float, float]:
    min_x, min_y, max_x, max_y = bounds
    points = [
        apply_matrix(matrix, min_x, min_y),
        apply_matrix(matrix, min_x, max_y),
        apply_matrix(matrix, max_x, min_y),
        apply_matrix(matrix, max_x, max_y),
    ]
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def numeric_style_value(element: ET.Element, key: str, default: float = 0) -> float:
    value = style_value(element, key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def element_export_bounds(
    element: ET.Element,
    matrix: Matrix,
) -> tuple[float, float, float, float] | None:
    tag = local_name(element)
    if tag == "path":
        d = element.get("d")
    elif tag in EXPORT_BOUNDING_TAGS:
        d = basic_shape_to_path_d(element)
    else:
        d = None

    if not d:
        return None

    min_x, min_y, max_x, max_y = transformed_bounds(absolute_path_bounds(d), matrix)
    padding = numeric_style_value(element, "stroke-width") / 2
    return min_x - padding, min_y - padding, max_x + padding, max_y + padding


def combine_bounds(
    bounds: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    if not bounds:
        raise ValueError("no bounds to combine")
    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def visible_content_bounds(root: ET.Element) -> tuple[float, float, float, float] | None:
    bounds: list[tuple[float, float, float, float]] = []

    def visit(element: ET.Element, inherited_matrix: Matrix) -> None:
        if local_name(element) in NON_RENDERED_CONTAINER_TAGS:
            return
        if (
            style_value(element, "display") == "none"
            or style_value(element, "visibility") == "hidden"
        ):
            return

        matrix = matrix_multiply(
            inherited_matrix,
            transform_to_matrix(element.get("transform")),
        )
        element_bounds = element_export_bounds(element, matrix)
        if element_bounds is not None:
            bounds.append(element_bounds)

        for child in element:
            visit(child, matrix)

    visit(root, (1, 0, 0, 1, 0, 0))
    if not bounds:
        return None
    return combine_bounds(bounds)


def strip_editor_metadata(root: ET.Element) -> None:
    for key in list(root.attrib):
        if key in {"width", "height", "x", "y"} or namespace_name(key) in EDITOR_METADATA_NAMESPACES:
            del root.attrib[key]

    for element in root.iter():
        for key in list(element.attrib):
            if namespace_name(key) in EDITOR_METADATA_NAMESPACES:
                del element.attrib[key]

    def prune(element: ET.Element) -> None:
        for child in list(element):
            if (
                local_name(child).lower() in {"metadata", "namedview"}
                or element_namespace(child) in EDITOR_METADATA_NAMESPACES
            ):
                element.remove(child)
            else:
                prune(child)

    prune(root)


def fit_viewbox_to_visible_content(root: ET.Element) -> None:
    bounds = visible_content_bounds(root)
    if bounds is None:
        return

    min_x, min_y, max_x, max_y = bounds
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        return

    root.set(
        "viewBox",
        " ".join(fmt_number(value) for value in (min_x, min_y, width, height)),
    )


def clean_svg_tree_for_export(root: ET.Element, *, fit_viewbox: bool = True) -> None:
    strip_editor_metadata(root)
    if fit_viewbox:
        fit_viewbox_to_visible_content(root)


def clean_svg_bytes_for_export(data: bytes, *, fit_viewbox: bool = True) -> bytes:
    register_namespaces()
    root = ET.fromstring(data)
    clean_svg_tree_for_export(root, fit_viewbox=fit_viewbox)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def get_or_create_defs(root: ET.Element) -> ET.Element:
    defs = root.find(f"{{{SVG_NS}}}defs")
    if defs is None:
        defs = ET.Element(f"{{{SVG_NS}}}defs")
        root.insert(0, defs)
    return defs


def command_numbers(tokens: list[str], start: int) -> tuple[list[float], int]:
    values: list[float] = []
    index = start
    while index < len(tokens) and is_number(tokens[index]):
        values.append(float(tokens[index]))
        index += 1
    return values, index


def absolute_path_bounds(d: str) -> tuple[float, float, float, float]:
    """Approximate bounds from endpoint and control-point coordinates."""
    tokens = PATH_TOKEN_RE.findall(d)
    x = y = start_x = start_y = 0.0
    points: list[tuple[float, float]] = []
    index = 0
    command = ""

    def point(px: float, py: float) -> None:
        points.append((px, py))

    while index < len(tokens):
        if not is_number(tokens[index]):
            command = tokens[index]
            index += 1

        values, index = command_numbers(tokens, index)
        relative = command.islower()
        upper = command.upper()

        if upper == "M":
            for offset in range(0, len(values), 2):
                if offset + 1 >= len(values):
                    break
                px, py = values[offset], values[offset + 1]
                x = x + px if relative else px
                y = y + py if relative else py
                if offset == 0:
                    start_x, start_y = x, y
                point(x, y)
        elif upper == "L":
            for offset in range(0, len(values), 2):
                if offset + 1 >= len(values):
                    break
                px, py = values[offset], values[offset + 1]
                x = x + px if relative else px
                y = y + py if relative else py
                point(x, y)
        elif upper == "H":
            for px in values:
                x = x + px if relative else px
                point(x, y)
        elif upper == "V":
            for py in values:
                y = y + py if relative else py
                point(x, y)
        elif upper == "C":
            for offset in range(0, len(values), 6):
                if offset + 5 >= len(values):
                    break
                coords = values[offset : offset + 6]
                curve_points = []
                for coord_offset in range(0, 6, 2):
                    px, py = coords[coord_offset], coords[coord_offset + 1]
                    curve_points.append((x + px if relative else px, y + py if relative else py))
                points.extend(curve_points)
                x, y = curve_points[-1]
        elif upper in {"S", "Q"}:
            step = 4
            for offset in range(0, len(values), step):
                if offset + step - 1 >= len(values):
                    break
                coords = values[offset : offset + step]
                curve_points = []
                for coord_offset in range(0, step, 2):
                    px, py = coords[coord_offset], coords[coord_offset + 1]
                    curve_points.append((x + px if relative else px, y + py if relative else py))
                points.extend(curve_points)
                x, y = curve_points[-1]
        elif upper == "T":
            for offset in range(0, len(values), 2):
                if offset + 1 >= len(values):
                    break
                px, py = values[offset], values[offset + 1]
                x = x + px if relative else px
                y = y + py if relative else py
                point(x, y)
        elif upper == "A":
            for offset in range(0, len(values), 7):
                if offset + 6 >= len(values):
                    break
                px, py = values[offset + 5], values[offset + 6]
                x = x + px if relative else px
                y = y + py if relative else py
                point(x, y)
        elif upper == "Z":
            x, y = start_x, start_y
            point(x, y)

    if not points:
        return 0, 0, 100, 100

    xs = [px for px, _ in points]
    ys = [py for _, py in points]
    return min(xs), min(ys), max(xs), max(ys)


def cubic_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    mt = 1 - t
    x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
    y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
    return x, y


def sample_path_outline(d: str, curve_steps: int = 18) -> list[tuple[float, float]]:
    return [
        point
        for subpath in sample_path_subpaths(d, curve_steps=curve_steps)
        for point in subpath
    ]


def sample_path_subpaths(
    d: str,
    curve_steps: int = 18,
) -> list[list[tuple[float, float]]]:
    tokens = PATH_TOKEN_RE.findall(d)
    x = y = start_x = start_y = 0.0
    subpaths: list[list[tuple[float, float]]] = []
    points: list[tuple[float, float]] = []
    index = 0
    command = ""

    def add(px: float, py: float) -> None:
        points.append((px, py))

    def start_subpath(px: float, py: float) -> None:
        nonlocal points
        if points:
            subpaths.append(points)
        points = [(px, py)]

    while index < len(tokens):
        if not is_number(tokens[index]):
            command = tokens[index]
            index += 1

        values, index = command_numbers(tokens, index)
        relative = command.islower()
        upper = command.upper()

        if upper == "M":
            for offset in range(0, len(values), 2):
                if offset + 1 >= len(values):
                    break
                px, py = values[offset], values[offset + 1]
                x = x + px if relative else px
                y = y + py if relative else py
                if offset == 0:
                    start_x, start_y = x, y
                    start_subpath(x, y)
                else:
                    add(x, y)
        elif upper == "L":
            for offset in range(0, len(values), 2):
                if offset + 1 >= len(values):
                    break
                px, py = values[offset], values[offset + 1]
                x = x + px if relative else px
                y = y + py if relative else py
                add(x, y)
        elif upper == "H":
            for px in values:
                x = x + px if relative else px
                add(x, y)
        elif upper == "V":
            for py in values:
                y = y + py if relative else py
                add(x, y)
        elif upper == "C":
            for offset in range(0, len(values), 6):
                if offset + 5 >= len(values):
                    break
                p0 = (x, y)
                coords = values[offset : offset + 6]
                p1 = (x + coords[0], y + coords[1]) if relative else (coords[0], coords[1])
                p2 = (x + coords[2], y + coords[3]) if relative else (coords[2], coords[3])
                p3 = (x + coords[4], y + coords[5]) if relative else (coords[4], coords[5])
                for step in range(1, curve_steps + 1):
                    add(*cubic_point(p0, p1, p2, p3, step / curve_steps))
                x, y = p3
        elif upper == "Z":
            x, y = start_x, start_y
            add(x, y)

    if points:
        subpaths.append(points)
    return subpaths


def centreline_from_outline(
    outline: list[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    rows: int = 90,
) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = bounds
    if len(outline) < 3:
        return [((min_x + max_x) / 2, max_y), ((min_x + max_x) / 2, min_y)]

    centreline: list[tuple[float, float]] = []
    for row in range(rows):
        y = max_y - (max_y - min_y) * row / max(rows - 1, 1)
        intersections: list[float] = []

        for index, (x1, y1) in enumerate(outline):
            x2, y2 = outline[(index + 1) % len(outline)]
            if (y1 <= y < y2) or (y2 <= y < y1):
                t = (y - y1) / (y2 - y1)
                intersections.append(x1 + t * (x2 - x1))

        intersections.sort()
        intervals = [
            (intersections[i], intersections[i + 1])
            for i in range(0, len(intersections) - 1, 2)
            if intersections[i + 1] > intersections[i]
        ]
        if not intervals:
            continue

        left, right = max(intervals, key=lambda pair: pair[1] - pair[0])
        centreline.append(((left + right) / 2, y))

    if len(centreline) < 2:
        return [((min_x + max_x) / 2, max_y), ((min_x + max_x) / 2, min_y)]
    return centreline


def flow_path_from_centreline(
    centreline: list[tuple[float, float]],
    offset: float,
    rng: random.Random,
    wobble: float,
    roughness: float,
) -> str:
    shifted: list[tuple[float, float]] = []
    for index, (x, y) in enumerate(centreline):
        prev_x, prev_y = centreline[max(index - 1, 0)]
        next_x, next_y = centreline[min(index + 1, len(centreline) - 1)]
        tx = next_x - prev_x
        ty = next_y - prev_y
        length = math.hypot(tx, ty) or 1
        nx = -ty / length
        ny = tx / length
        pressure = math.sin(index / max(len(centreline) - 1, 1) * math.pi)
        shifted.append(
            (
                x + nx * offset + rng.gauss(0, wobble * (0.35 + pressure)),
                y + ny * offset + rng.gauss(0, wobble * (0.35 + pressure)),
            )
        )

    parts = ["M", fmt_number(shifted[0][0]), fmt_number(shifted[0][1])]
    for index in range(1, len(shifted)):
        x0, y0 = shifted[index - 1]
        x1, y1 = shifted[index]
        prev_x, prev_y = shifted[max(index - 2, 0)]
        next_x, next_y = shifted[min(index + 1, len(shifted) - 1)]
        tension = 10.5 if roughness > 0 else 6
        c1x = x0 + (x1 - prev_x) / tension
        c1y = y0 + (y1 - prev_y) / tension
        c2x = x1 - (next_x - x0) / tension
        c2y = y1 - (next_y - y0) / tension
        if roughness > 0:
            dx = x1 - x0
            dy = y1 - y0
            length = math.hypot(dx, dy) or 1
            nx = -dy / length
            ny = dx / length
            bow = rng.gauss(0, roughness * 2.4)
            c1x += nx * bow * rng.uniform(0.35, 0.9)
            c1y += ny * bow * rng.uniform(0.35, 0.9)
            c2x += nx * bow * rng.uniform(0.35, 0.9)
            c2y += ny * bow * rng.uniform(0.35, 0.9)
        parts.extend(["C", fmt_number(c1x), fmt_number(c1y), fmt_number(c2x), fmt_number(c2y), fmt_number(x1), fmt_number(y1)])
    return " ".join(parts)


def smooth_path_from_points(
    points: list[tuple[float, float]],
    rng: random.Random,
    jitter: float,
    roughness: float = 0,
) -> str:
    if not points:
        return ""
    if len(points) == 1:
        return f"M {fmt_number(points[0][0])} {fmt_number(points[0][1])}"

    working = list(points)
    pass_dx = rng.gauss(0, roughness * 0.35)
    pass_dy = rng.gauss(0, roughness * 0.35)
    shifted: list[tuple[float, float]] = []
    for index, (x, y) in enumerate(working):
        phase = index / max(len(working) - 1, 1)
        shake = 0.55 + math.sin(phase * math.pi) * 0.75
        shifted.append(
            (
                x + pass_dx + rng.gauss(0, jitter * shake),
                y + pass_dy + rng.gauss(0, jitter * shake),
            )
        )

    parts = ["M", fmt_number(shifted[0][0]), fmt_number(shifted[0][1])]

    for index in range(1, len(shifted)):
        x0, y0 = shifted[index - 1]
        x1, y1 = shifted[index]
        prev_x, prev_y = shifted[max(index - 2, 0)]
        next_x, next_y = shifted[min(index + 1, len(shifted) - 1)]
        c1x = x0 + (x1 - prev_x) / 6
        c1y = y0 + (y1 - prev_y) / 6
        c2x = x1 - (next_x - x0) / 6
        c2y = y1 - (next_y - y0) / 6
        parts.extend(["C", fmt_number(c1x), fmt_number(c1y), fmt_number(c2x), fmt_number(c2y), fmt_number(x1), fmt_number(y1)])

    return " ".join(parts)


def stroke_route_from_path_d(d: str) -> list[tuple[float, float]]:
    return sample_path_outline(re.sub(r"[Zz]", "", d), curve_steps=12)


def stroke_routes_from_path_d(d: str) -> list[list[tuple[float, float]]]:
    return sample_path_subpaths(re.sub(r"[Zz]", "", d), curve_steps=12)


def bounds_gesture_points(bounds: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    min_x, min_y, max_x, max_y = bounds
    width = max_x - min_x
    height = max_y - min_y
    return [
        (min_x + width * 0.18, min_y + height * 0.18),
        (min_x + width * 0.12, min_y + height * 0.04),
        (min_x + width * 0.08, min_y + height * 0.2),
        (min_x + width * 0.18, min_y + height * 0.3),
        (min_x + width * 0.36, min_y + height * 0.34),
        (max_x - width * 0.06, min_y + height * 0.36),
        (max_x - width * 0.05, min_y + height * 0.58),
        (max_x - width * 0.12, min_y + height * 0.76),
        (min_x + width * 0.58, min_y + height * 0.76),
        (min_x + width * 0.48, max_y - height * 0.05),
        (min_x + width * 0.55, min_y + height * 0.77),
        (max_x - width * 0.06, min_y + height * 0.58),
        (max_x - width * 0.05, min_y + height * 0.36),
        (min_x + width * 0.34, min_y + height * 0.34),
        (min_x + width * 0.18, min_y + height * 0.3),
        (min_x + width * 0.08, min_y + height * 0.2),
        (min_x + width * 0.14, min_y + height * 0.05),
        (min_x + width * 0.3, min_y + height * 0.22),
        (min_x + width * 0.28, min_y + height * 0.48),
        (min_x + width * 0.1, min_y + height * 0.52),
        (min_x + width * 0.22, min_y + height * 0.6),
        (min_x + width * 0.58, min_y + height * 0.43),
        (max_x - width * 0.07, min_y + height * 0.36),
    ]


def gesture_path(
    bounds: tuple[float, float, float, float],
    rng: random.Random,
    offset: float,
    wobble: float,
) -> str:
    points = bounds_gesture_points(bounds)
    shifted: list[tuple[float, float]] = []

    for index, (x, y) in enumerate(points):
        prev_x, prev_y = points[max(index - 1, 0)]
        next_x, next_y = points[min(index + 1, len(points) - 1)]
        tx = next_x - prev_x
        ty = next_y - prev_y
        length = math.hypot(tx, ty) or 1
        nx = -ty / length
        ny = tx / length
        taper = math.sin(index / max(len(points) - 1, 1) * math.pi)
        shifted.append(
            (
                x + nx * offset + rng.gauss(0, wobble * (0.45 + taper)),
                y + ny * offset + rng.gauss(0, wobble * (0.45 + taper)),
            )
        )

    parts = ["M", fmt_number(shifted[0][0]), fmt_number(shifted[0][1])]
    for index in range(1, len(shifted)):
        x0, y0 = shifted[index - 1]
        x1, y1 = shifted[index]
        prev_x, prev_y = shifted[max(index - 2, 0)]
        next_x, next_y = shifted[min(index + 1, len(shifted) - 1)]
        c1x = x0 + (x1 - prev_x) / 5.2
        c1y = y0 + (y1 - prev_y) / 5.2
        c2x = x1 - (next_x - x0) / 5.2
        c2y = y1 - (next_y - y0) / 5.2
        parts.extend(["C", fmt_number(c1x), fmt_number(c1y), fmt_number(c2x), fmt_number(c2y), fmt_number(x1), fmt_number(y1)])

    return " ".join(parts)


def vertical_flow_path(
    column: tuple[float, float, float, float],
    x: float,
    rng: random.Random,
    wobble: float,
) -> str:
    _left, _right, top, bottom = column
    y1 = bottom + rng.uniform(0, (bottom - top) * 0.04)
    y2 = top - rng.uniform(0, (bottom - top) * 0.04)
    mid_y = (y1 + y2) / 2
    c1x = x + rng.gauss(0, wobble)
    c1y = y1 * 0.68 + y2 * 0.32 + rng.gauss(0, wobble)
    c2x = x + rng.gauss(0, wobble)
    c2y = y1 * 0.32 + y2 * 0.68 + rng.gauss(0, wobble)
    return " ".join(
        [
            "M",
            fmt_number(x + rng.gauss(0, wobble * 0.35)),
            fmt_number(y1),
            "C",
            fmt_number(c1x),
            fmt_number(c1y),
            fmt_number(c2x),
            fmt_number(c2y),
            fmt_number(x + rng.gauss(0, wobble * 0.35)),
            fmt_number(y2),
            "C",
            fmt_number(x + rng.gauss(0, wobble)),
            fmt_number((y2 + mid_y) / 2),
            fmt_number(x + rng.gauss(0, wobble)),
            fmt_number((y1 + mid_y) / 2),
            fmt_number(x + rng.gauss(0, wobble * 0.35)),
            fmt_number(y1),
        ]
    )


def vertical_spans_from_outline(
    outline: list[tuple[float, float]],
    bounds: tuple[float, float, float, float],
    columns: int = 70,
) -> list[tuple[float, float, float]]:
    min_x, min_y, max_x, max_y = bounds
    spans: list[tuple[float, float, float]] = []
    for column in range(columns):
        x = min_x + (max_x - min_x) * column / max(columns - 1, 1)
        intersections: list[float] = []

        for index, (x1, y1) in enumerate(outline):
            x2, y2 = outline[(index + 1) % len(outline)]
            if (x1 <= x < x2) or (x2 <= x < x1):
                t = (x - x1) / (x2 - x1)
                intersections.append(y1 + t * (y2 - y1))

        intersections.sort()
        intervals = [
            (intersections[i], intersections[i + 1])
            for i in range(0, len(intersections) - 1, 2)
            if intersections[i + 1] > intersections[i]
        ]
        if not intervals:
            continue

        top, bottom = max(intervals, key=lambda pair: pair[1] - pair[0])
        if bottom - top > (max_y - min_y) * 0.18:
            spans.append((x, top, bottom))

    return spans


def vertical_column_groups(spans: list[tuple[float, float, float]], min_run: int = 3) -> list[tuple[float, float, float, float]]:
    if not spans:
        return []

    typical_gap = min(
        (abs(spans[index][0] - spans[index - 1][0]) for index in range(1, len(spans)) if spans[index][0] != spans[index - 1][0]),
        default=1.0,
    )
    groups: list[list[tuple[float, float, float]]] = []
    current: list[tuple[float, float, float]] = []

    for span in spans:
        if not current:
            current = [span]
            continue
        previous_x = current[-1][0]
        if abs(span[0] - previous_x) <= typical_gap * 1.8:
            current.append(span)
        else:
            if len(current) >= min_run:
                groups.append(current)
            current = [span]

    if len(current) >= min_run:
        groups.append(current)

    columns: list[tuple[float, float, float, float]] = []
    for group in groups:
        xs = [item[0] for item in group]
        tops = [item[1] for item in group]
        bottoms = [item[2] for item in group]
        left = min(xs)
        right = max(xs)
        top = min(tops)
        bottom = max(bottoms)
        if bottom - top > max(right - left, typical_gap) * 1.35:
            columns.append((left, right, top, bottom))
    return columns


def make_wobbly_line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    rng: random.Random,
    wobble: float,
) -> str:
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy) or 1
    nx = -dy / length
    ny = dx / length

    c1x = x1 + dx * 0.33 + nx * rng.gauss(0, wobble)
    c1y = y1 + dy * 0.33 + ny * rng.gauss(0, wobble)
    c2x = x1 + dx * 0.66 + nx * rng.gauss(0, wobble)
    c2y = y1 + dy * 0.66 + ny * rng.gauss(0, wobble)

    return " ".join(
        [
            "M",
            fmt_number(x1),
            fmt_number(y1),
            "C",
            fmt_number(c1x),
            fmt_number(c1y),
            fmt_number(c2x),
            fmt_number(c2y),
            fmt_number(x2),
            fmt_number(y2),
        ]
    )


def continuous_scribble_path(
    bounds: tuple[float, float, float, float],
    rng: random.Random,
    loops: int,
    wobble: float,
) -> str:
    min_x, min_y, max_x, max_y = bounds
    width = max_x - min_x
    height = max_y - min_y
    center_x = (min_x + max_x) / 2 + rng.gauss(0, width * 0.025)
    center_y = (min_y + max_y) / 2 + rng.gauss(0, height * 0.025)
    orbit_count = rng.randint(3, 5)
    samples_per_orbit = 28
    total_samples = max(loops * orbit_count * samples_per_orbit, samples_per_orbit)
    drift_x = rng.gauss(0, width * 0.018)
    drift_y = rng.gauss(0, height * 0.018)
    start_phase = math.pi / 2 + rng.gauss(0, 0.08)
    orbits: list[tuple[float, float, float, float]] = []

    for orbit_index in range(orbit_count):
        scale = 1 - orbit_index * rng.uniform(0.08, 0.14)
        orbits.append(
            (
                width * rng.uniform(0.42, 0.64) * scale,
                height * rng.uniform(0.28, 0.5) * scale,
                math.radians(rng.uniform(-34, 34)),
                rng.uniform(-0.18, 0.18),
            )
        )

    points: list[tuple[float, float]] = []
    for sample in range(total_samples + 1):
        orbit_index = (sample // samples_per_orbit) % orbit_count
        rx_base, ry_base, tilt, phase_offset = orbits[orbit_index]
        lap = sample / (orbit_count * samples_per_orbit)
        theta = start_phase + phase_offset + sample / samples_per_orbit * math.tau
        lap_ratio = lap / max(loops, 1)

        pressure_wander = math.sin(theta * 2.0 + rng.uniform(-0.2, 0.2)) * 0.06
        rx = rx_base * (1 + pressure_wander + rng.gauss(0, 0.018))
        ry = ry_base * (1 - pressure_wander + rng.gauss(0, 0.018))

        local_x = math.cos(theta) * rx + math.sin(theta * 2.0) * width * 0.035
        local_y = math.sin(theta) * ry + math.cos(theta * 3.0) * height * 0.022

        x = center_x + local_x * math.cos(tilt) - local_y * math.sin(tilt)
        y = center_y + local_x * math.sin(tilt) + local_y * math.cos(tilt)
        x += drift_x * lap_ratio + rng.gauss(0, wobble)
        y += drift_y * lap_ratio + rng.gauss(0, wobble)
        points.append((x, y))

    parts = ["M", fmt_number(points[0][0]), fmt_number(points[0][1])]
    for index in range(1, len(points)):
        x0, y0 = points[index - 1]
        x1, y1 = points[index]
        prev_x, prev_y = points[max(index - 2, 0)]
        next_x, next_y = points[min(index + 1, len(points) - 1)]
        c1x = x0 + (x1 - prev_x) / 6
        c1y = y0 + (y1 - prev_y) / 6
        c2x = x1 - (next_x - x0) / 6
        c2y = y1 - (next_y - y0) / 6
        parts.extend(["C", fmt_number(c1x), fmt_number(c1y), fmt_number(c2x), fmt_number(c2y), fmt_number(x1), fmt_number(y1)])

    return " ".join(parts)


def add_shading_strokes(
    group: ET.Element,
    clip_id: str,
    source_d: str,
    bounds: tuple[float, float, float, float],
    count: int,
    stroke: str,
    stroke_width: float,
    opacity: float,
    roughness: float,
    rng: random.Random,
) -> None:
    min_x, min_y, max_x, max_y = bounds
    width = max_x - min_x
    height = max_y - min_y
    outline = sample_path_outline(source_d)
    centreline = centreline_from_outline(outline, bounds)
    vertical_columns = vertical_column_groups(vertical_spans_from_outline(outline, bounds, columns=110))
    offset_span = max(width, height) * 0.11

    shade_group = ET.Element(f"{{{SVG_NS}}}g")
    shade_group.set("id", f"{clip_id}-directional-pencil-fill")
    shade_group.set("clip-path", f"url(#{clip_id})")

    for index in range(1, count + 1):
        band_pos = -1 + 2 * (index - 1) / max(count - 1, 1)
        offset = band_pos * offset_span + rng.gauss(0, offset_span * 0.12)
        d = flow_path_from_centreline(
            centreline=centreline,
            offset=offset,
            rng=rng,
            wobble=max(width, height) * rng.uniform(0.004, 0.012),
            roughness=roughness,
        )
        pressure = rng.lognormvariate(0, 0.24)

        line = ET.Element(f"{{{SVG_NS}}}path")
        line.set("id", f"{clip_id}-flow-{index}")
        line.set("d", d)
        line.set(
            "style",
            dict_to_style(
                {
                    "fill": "none",
                    "stroke": stroke,
                    "stroke-width": fmt_number(stroke_width * rng.uniform(0.55, 1.65) * pressure),
                    "stroke-linecap": "round",
                    "stroke-linejoin": "round",
                    "stroke-opacity": fmt_number(opacity * rng.uniform(0.55, 1.3)),
                }
            ),
        )
        line.set("data-sketcher-flow-pass", str(index))
        shade_group.append(line)

        if rng.random() < 0.65:
            pressure_line = ET.Element(f"{{{SVG_NS}}}path")
            pressure_line.set("id", f"{clip_id}-flow-pressure-{index}")
            pressure_line.set("d", d)
            pressure_line.set(
                "style",
                dict_to_style(
                    {
                        "fill": "none",
                        "stroke": stroke,
                        "stroke-width": fmt_number(stroke_width * rng.uniform(0.18, 0.55)),
                        "stroke-linecap": "round",
                        "stroke-linejoin": "round",
                        "stroke-opacity": fmt_number(opacity * rng.uniform(0.25, 0.75)),
                    }
                ),
            )
            pressure_line.set("data-sketcher-pressure-pass", str(index))
            shade_group.append(pressure_line)

    vertical_count = max(24, count // 2)
    if vertical_columns:
        for index in range(1, vertical_count + 1):
            column = vertical_columns[(index - 1) % len(vertical_columns)]
            left, right, top, bottom = column
            x = rng.uniform(left, right)
            d = vertical_flow_path(column, x + rng.gauss(0, max(right - left, width * 0.01) * 0.08), rng, max(right - left, width * 0.01) * 0.22)
            pressure = rng.lognormvariate(0, 0.24)
            line = ET.Element(f"{{{SVG_NS}}}path")
            line.set("id", f"{clip_id}-vertical-flow-{index}")
            line.set("d", d)
            line.set(
                "style",
                dict_to_style(
                    {
                        "fill": "none",
                        "stroke": stroke,
                        "stroke-width": fmt_number(stroke_width * rng.uniform(0.55, 1.65) * pressure),
                        "stroke-linecap": "round",
                        "stroke-linejoin": "round",
                        "stroke-opacity": fmt_number(opacity * rng.uniform(0.55, 1.3)),
                    }
                ),
            )
            line.set("data-sketcher-vertical-flow-pass", str(index))
            shade_group.append(line)

    group.append(shade_group)


def sketch_paths(
    root: ET.Element,
    repeats: int,
    jitter: float,
    stroke_width: float,
    stroke: str,
    opacity: float,
    shade_strokes: int,
    shade_width: float,
    shade_opacity: float,
    mode: str,
    roughness: float,
    keep_original: bool,
    rng: random.Random,
) -> int:
    defs = get_or_create_defs(root)
    parent_map = {child: parent for parent in root.iter() for child in parent}
    paths = [element for element in root.iter(f"{{{SVG_NS}}}path") if element.get("d")]
    selected_mode = mode
    if mode == "auto":
        stroke_like = [
            path
            for path in paths
            if style_value(path, "fill", "none") == "none"
            and style_value(path, "stroke", "none") != "none"
        ]
        selected_mode = "stroke" if paths and len(stroke_like) == len(paths) else "fill"

    for path_index, path in enumerate(paths, start=1):
        parent = parent_map.get(path)
        if parent is None:
            continue

        if not keep_original:
            hide_element(path)

        group = ET.Element(f"{{{SVG_NS}}}g")
        group.set("id", f"sketch-retrace-{path_index}")
        group.set("data-sketcher-source", path.get("id", f"path-{path_index}"))

        if selected_mode == "fill":
            clip_id = f"sketch-clip-{path_index}"
            clip_path = ET.Element(f"{{{SVG_NS}}}clipPath")
            clip_path.set("id", clip_id)
            clip_shape = ET.Element(f"{{{SVG_NS}}}path")
            clip_shape.set("d", path.get("d", ""))
            clip_path.append(clip_shape)
            defs.append(clip_path)

            add_shading_strokes(
                group=group,
                clip_id=clip_id,
                source_d=path.get("d", ""),
                bounds=absolute_path_bounds(path.get("d", "")),
                count=shade_strokes,
                stroke=stroke,
                stroke_width=shade_width,
                opacity=shade_opacity,
                roughness=roughness,
                rng=rng,
            )

        for repeat_index in range(repeats):
            copy = deepcopy(path)
            copy.set("id", f"{path.get('id', 'path')}-sketch-{repeat_index + 1}")
            if selected_mode == "stroke":
                try:
                    sampled_routes = stroke_routes_from_path_d(path.get("d", ""))
                except ValueError as error:
                    raise SystemExit(
                        f"{path.get('id', 'path')} is a closed outline, not an open stroke route. "
                        "Use open centreline paths for rough sketch mode, or run --mode outline to roughen outlines."
                    ) from error
                copy.set(
                    "d",
                    " ".join(
                        smooth_path_from_points(route, rng, jitter, roughness)
                        for route in sampled_routes
                    ),
                )
            else:
                sampled = sample_path_outline(path.get("d", ""), curve_steps=10)
                copy.set("d", smooth_path_from_points(sampled, rng, jitter, roughness))

            style = style_to_dict(copy.get("style"))
            style["fill"] = "none"
            style.pop("fill-opacity", None)
            style["stroke"] = stroke
            style["stroke-width"] = fmt_number(stroke_width * rng.uniform(0.45, 1.95) * rng.lognormvariate(0, 0.28))
            style["stroke-linecap"] = "round"
            style["stroke-linejoin"] = "round"
            style["stroke-opacity"] = fmt_number(min(1, opacity * rng.uniform(0.82, 1.55)))
            style.pop("display", None)
            copy.set("style", dict_to_style(style))
            copy.set("data-sketcher-pass", str(repeat_index + 1))

            group.append(copy)

        append_after(parent, path, group)

    return len(paths)


def render_sketch_svg(
    input_path: Path,
    output_path: Path,
    *,
    repeats: int = 54,
    jitter: float = 0.08,
    roughness: float = 0.65,
    stroke_width: float = 0.24,
    stroke: str = "#111111",
    opacity: float = 0.075,
    shade_strokes: int = 70,
    shade_width: float = 1.25,
    shade_opacity: float = 0.15,
    mode: str = "auto",
    seed: int = 7,
    keep_original: bool = False,
) -> int:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    if jitter < 0:
        raise ValueError("jitter must be 0 or greater")
    if roughness < 0:
        raise ValueError("roughness must be 0 or greater")
    if shade_strokes < 0:
        raise ValueError("shade_strokes must be 0 or greater")
    if stroke_width <= 0:
        raise ValueError("stroke_width must be greater than 0")
    if shade_width <= 0:
        raise ValueError("shade_width must be greater than 0")
    if not 0 < opacity <= 1:
        raise ValueError("opacity must be greater than 0 and no more than 1")
    if not 0 < shade_opacity <= 1:
        raise ValueError("shade_opacity must be greater than 0 and no more than 1")
    if mode not in {"auto", "fill", "stroke", "outline"}:
        raise ValueError("mode must be one of auto, fill, stroke, or outline")

    register_namespaces()
    tree = ET.parse(input_path)
    root = tree.getroot()
    normalize_basic_shapes(root)

    path_count = sketch_paths(
        root=root,
        repeats=repeats,
        jitter=jitter,
        stroke_width=stroke_width,
        stroke=stroke,
        opacity=opacity,
        shade_strokes=shade_strokes,
        shade_width=shade_width,
        shade_opacity=shade_opacity,
        mode=mode,
        roughness=roughness,
        keep_original=keep_original,
        rng=random.Random(seed),
    )

    if path_count == 0:
        raise ValueError(f"No path elements with d attributes found in {input_path}")

    clean_svg_tree_for_export(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="UTF-8", xml_declaration=True)
    return path_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrace SVG paths many times with small variations for a sketched look."
    )
    parser.add_argument("input", type=Path, help="Source SVG file")
    parser.add_argument("output", type=Path, help="New SVG file to write")
    parser.add_argument("--repeats", type=int, default=54, help="Number of rough retraces per source path")
    parser.add_argument("--shade-strokes", type=int, default=70, help="Number of generic flow passes used for filled areas")
    parser.add_argument("--jitter", type=float, default=0.08, help="Point-level hand jitter in SVG units")
    parser.add_argument("--roughness", type=float, default=0.65, help="Non-subtle human scrawl error added to retraced strokes")
    parser.add_argument("--stroke-width", type=float, default=0.24, help="Base pencil stroke width")
    parser.add_argument("--shade-width", type=float, default=1.25, help="Base width for interior pencil strokes")
    parser.add_argument("--stroke", default="#111111", help="Stroke color")
    parser.add_argument("--opacity", type=float, default=0.075, help="Base stroke opacity per retrace")
    parser.add_argument("--shade-opacity", type=float, default=0.15, help="Base opacity for interior pencil strokes")
    parser.add_argument(
        "--mode",
        choices=["auto", "fill", "stroke", "outline"],
        default="auto",
        help="auto uses open strokes when available; fill sketches filled areas; stroke requires open centreline paths; outline roughens closed outlines",
    )
    parser.add_argument("--seed", type=int, default=7, help="Random seed for repeatable output")
    parser.add_argument(
        "--keep-original",
        action="store_true",
        help="Keep original paths visible underneath the sketched retraces",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        path_count = render_sketch_svg(
            args.input,
            args.output,
            repeats=args.repeats,
            jitter=args.jitter,
            roughness=args.roughness,
            stroke_width=args.stroke_width,
            stroke=args.stroke,
            opacity=args.opacity,
            shade_strokes=args.shade_strokes,
            shade_width=args.shade_width,
            shade_opacity=args.shade_opacity,
            mode=args.mode,
            seed=args.seed,
            keep_original=args.keep_original,
        )
    except (ET.ParseError, ValueError) as error:
        raise SystemExit(str(error)) from error

    print(f"Wrote {args.output} from {path_count} path(s)")


if __name__ == "__main__":
    main()
