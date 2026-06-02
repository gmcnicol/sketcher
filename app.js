const SVG_NS = "http://www.w3.org/2000/svg";

const state = {
  paths: [],
  activePath: null,
  drawing: false,
  image: null,
  sketchSvg: "",
};

const els = {
  stage: document.getElementById("stage"),
  backgroundImage: document.getElementById("backgroundImage"),
  routeLayer: document.getElementById("routeLayer"),
  previewLayer: document.getElementById("previewLayer"),
  imageInput: document.getElementById("imageInput"),
  imageOpacity: document.getElementById("imageOpacity"),
  passes: document.getElementById("passes"),
  jitter: document.getElementById("jitter"),
  roughness: document.getElementById("roughness"),
  strokeWidth: document.getElementById("strokeWidth"),
  opacity: document.getElementById("opacity"),
  seed: document.getElementById("seed"),
  newPathButton: document.getElementById("newPathButton"),
  undoPointButton: document.getElementById("undoPointButton"),
  undoPathButton: document.getElementById("undoPathButton"),
  clearButton: document.getElementById("clearButton"),
  generateButton: document.getElementById("generateButton"),
  downloadRoutesButton: document.getElementById("downloadRoutesButton"),
  downloadSketchButton: document.getElementById("downloadSketchButton"),
  downloadProjectButton: document.getElementById("downloadProjectButton"),
  pathCount: document.getElementById("pathCount"),
  pointCount: document.getElementById("pointCount"),
};

function mulberry32(seed) {
  let value = seed >>> 0;
  return function random() {
    value += 0x6d2b79f5;
    let t = value;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussian(random) {
  let u = 0;
  let v = 0;
  while (u === 0) u = random();
  while (v === 0) v = random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function fmt(value) {
  return Number(value.toFixed(3)).toString();
}

function pointFromEvent(event) {
  const point = els.stage.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const transformed = point.matrixTransform(els.stage.getScreenCTM().inverse());
  return { x: transformed.x, y: transformed.y, t: performance.now() };
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function pathData(points) {
  if (!points.length) return "";
  const parts = ["M", fmt(points[0].x), fmt(points[0].y)];
  for (const point of points.slice(1)) {
    parts.push("L", fmt(point.x), fmt(point.y));
  }
  return parts.join(" ");
}

function smoothSketchData(points, random, jitter, roughness) {
  if (!points.length) return "";
  if (points.length === 1) return `M ${fmt(points[0].x)} ${fmt(points[0].y)}`;

  const passDx = gaussian(random) * roughness * 0.35;
  const passDy = gaussian(random) * roughness * 0.35;
  const shifted = points.map((point, index) => {
    const phase = index / Math.max(points.length - 1, 1);
    const shake = 0.55 + Math.sin(phase * Math.PI) * 0.75;
    return {
      x: point.x + passDx + gaussian(random) * jitter * shake,
      y: point.y + passDy + gaussian(random) * jitter * shake,
    };
  });

  const parts = ["M", fmt(shifted[0].x), fmt(shifted[0].y)];
  for (let index = 1; index < shifted.length; index += 1) {
    const p0 = shifted[index - 1];
    const p1 = shifted[index];
    const prev = shifted[Math.max(index - 2, 0)];
    const next = shifted[Math.min(index + 1, shifted.length - 1)];
    const tension = roughness > 0 ? 10.5 : 6;
    let c1x = p0.x + (p1.x - prev.x) / tension;
    let c1y = p0.y + (p1.y - prev.y) / tension;
    let c2x = p1.x - (next.x - p0.x) / tension;
    let c2y = p1.y - (next.y - p0.y) / tension;

    if (roughness > 0) {
      const dx = p1.x - p0.x;
      const dy = p1.y - p0.y;
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len;
      const ny = dx / len;
      const bow = gaussian(random) * roughness * 2.4;
      c1x += nx * bow * (0.35 + random() * 0.55);
      c1y += ny * bow * (0.35 + random() * 0.55);
      c2x += nx * bow * (0.35 + random() * 0.55);
      c2y += ny * bow * (0.35 + random() * 0.55);
    }

    parts.push("C", fmt(c1x), fmt(c1y), fmt(c2x), fmt(c2y), fmt(p1.x), fmt(p1.y));
  }
  return parts.join(" ");
}

function createSvgElement(tag, attributes = {}) {
  const element = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function renderRoutes() {
  els.routeLayer.replaceChildren();
  state.paths.forEach((path, pathIndex) => {
    if (path.points.length < 1) return;
    els.routeLayer.appendChild(createSvgElement("path", {
      class: "route-path",
      d: pathData(path.points),
      "data-path-index": pathIndex,
    }));
    const last = path.points[path.points.length - 1];
    els.routeLayer.appendChild(createSvgElement("circle", {
      class: "route-point",
      cx: fmt(last.x),
      cy: fmt(last.y),
      r: "3.2",
    }));
  });

  els.pathCount.textContent = String(state.paths.length);
  els.pointCount.textContent = String(state.paths.reduce((sum, path) => sum + path.points.length, 0));
}

function settings() {
  return {
    passes: Math.max(1, Number(els.passes.value) || 1),
    jitter: Math.max(0, Number(els.jitter.value) || 0),
    roughness: Math.max(0, Number(els.roughness.value) || 0),
    strokeWidth: Math.max(0.01, Number(els.strokeWidth.value) || 0.13),
    opacity: Math.min(1, Math.max(0.01, Number(els.opacity.value) || 0.13)),
    seed: Number(els.seed.value) || 1,
  };
}

function routeSvgString() {
  const paths = state.paths
    .filter((path) => path.points.length > 1)
    .map((path, index) => `  <path id="route-${index + 1}" d="${pathData(path.points)}" fill="none" stroke="#000000" stroke-width="0.2" stroke-linecap="round" stroke-linejoin="round" />`)
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="700" viewBox="0 0 1000 700">\n${paths}\n</svg>\n`;
}

function generateSketchSvg() {
  const config = settings();
  const random = mulberry32(config.seed);
  const sketchPaths = [];

  state.paths.filter((path) => path.points.length > 1).forEach((path, pathIndex) => {
    for (let pass = 0; pass < config.passes; pass += 1) {
      const width = config.strokeWidth * (0.45 + random() * 1.5) * Math.exp(gaussian(random) * 0.28);
      const opacity = Math.min(1, config.opacity * (0.82 + random() * 0.73));
      const d = smoothSketchData(path.points, random, config.jitter, config.roughness);
      sketchPaths.push(`  <path id="route-${pathIndex + 1}-pass-${pass + 1}" d="${d}" fill="none" stroke="#111111" stroke-width="${fmt(width)}" stroke-linecap="round" stroke-linejoin="round" stroke-opacity="${fmt(opacity)}" />`);
    }
  });

  state.sketchSvg = `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="700" viewBox="0 0 1000 700">\n${sketchPaths.join("\n")}\n</svg>\n`;
  renderPreview();
}

function renderPreview() {
  els.previewLayer.replaceChildren();
  if (!state.sketchSvg) return;
  const doc = new DOMParser().parseFromString(state.sketchSvg, "image/svg+xml");
  [...doc.querySelectorAll("path")].forEach((path) => {
    const element = createSvgElement("path", {
      class: "preview-path",
      d: path.getAttribute("d"),
      "stroke-width": path.getAttribute("stroke-width"),
      "stroke-opacity": path.getAttribute("stroke-opacity"),
    });
    els.previewLayer.appendChild(element);
  });
}

function download(name, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function id() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `path-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function beginPath(point) {
  const path = { id: id(), points: [point] };
  state.paths.push(path);
  state.activePath = path;
  state.drawing = true;
}

function addPoint(point) {
  if (!state.activePath) {
    beginPath(point);
    return;
  }
  const last = state.activePath.points[state.activePath.points.length - 1];
  if (!last || distance(last, point) >= 2.2) {
    state.activePath.points.push(point);
  }
}

els.stage.addEventListener("pointerdown", (event) => {
  els.stage.setPointerCapture(event.pointerId);
  beginPath(pointFromEvent(event));
  renderRoutes();
});

els.stage.addEventListener("pointermove", (event) => {
  if (!state.drawing) return;
  addPoint(pointFromEvent(event));
  renderRoutes();
});

els.stage.addEventListener("pointerup", () => {
  state.drawing = false;
  state.activePath = null;
  renderRoutes();
});

els.stage.addEventListener("pointercancel", () => {
  state.drawing = false;
  state.activePath = null;
});

els.imageInput.addEventListener("change", () => {
  const file = els.imageInput.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  state.image = { name: file.name, url };
  els.backgroundImage.setAttribute("href", url);
});

els.imageOpacity.addEventListener("input", () => {
  els.backgroundImage.style.opacity = els.imageOpacity.value;
});

els.newPathButton.addEventListener("click", () => {
  state.activePath = null;
  state.drawing = false;
});

els.undoPointButton.addEventListener("click", () => {
  const path = state.paths[state.paths.length - 1];
  if (!path) return;
  path.points.pop();
  if (path.points.length === 0) state.paths.pop();
  renderRoutes();
});

els.undoPathButton.addEventListener("click", () => {
  state.paths.pop();
  state.activePath = null;
  state.drawing = false;
  renderRoutes();
});

els.clearButton.addEventListener("click", () => {
  state.paths = [];
  state.activePath = null;
  state.sketchSvg = "";
  els.previewLayer.replaceChildren();
  renderRoutes();
});

els.generateButton.addEventListener("click", generateSketchSvg);

els.downloadRoutesButton.addEventListener("click", () => {
  download("routes.svg", routeSvgString(), "image/svg+xml");
});

els.downloadSketchButton.addEventListener("click", () => {
  if (!state.sketchSvg) generateSketchSvg();
  download("sketch.svg", state.sketchSvg, "image/svg+xml");
});

els.downloadProjectButton.addEventListener("click", () => {
  download("sketch-project.json", JSON.stringify({
    paths: state.paths,
    settings: settings(),
    imageName: state.image?.name || null,
  }, null, 2), "application/json");
});

renderRoutes();
