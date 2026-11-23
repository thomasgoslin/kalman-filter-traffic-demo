import {initializeSettings} from "./settings.js";
import {cloudflareApi} from "./experiment-api.js";

const $ = id => document.getElementById(id);
const video = $("video"), stage = video.parentElement, overlay = $("overlay");
const tokens = getComputedStyle(document.documentElement);
const theme = name => tokens.getPropertyValue("--" + name).trim();
const colors = {forecast:theme("cyan"), detection:theme("gold"), corrected:theme("green"), initialized:theme("gold")};
const plotColors = {forecast:theme("plot-forecast"), detection:theme("plot-detector"), corrected:theme("plot-update")};
const mono = theme("mono");
const statusText = {updated:"Analysis", initialized:"Initialized from observation", predicted:"Forecast only", expired:"Track expired"};
const layers = {forecast:"forecast", detection:"measurement", corrected:"update"};
const boxes = ["forecast", "detection", "corrected"];
const overlayInputs = [...Object.values(layers), "ellipse", "ids"].map($);
const timeline = $("timeline"), frameNumber = $("frame-number");
const estimateDialog = $("estimate-dialog"), estimatePanel = $("estimate-panel");
const simulation = $("simulation"), modelView = $("model"), settingsView = $("settings");
let draggingTimeline = false, editingFrame = false;
let manifest, run, frames = [], index = [], history = null, selected = null, hovered = null;
let frame = 0, tab = "position", runVersion = 0, trackVersion = 0;
let pendingSeek = null, queuedSeek = null;
let currentSource, mediaUrl;
let track = () => {}, viewChanged = () => {}, currentView = "simulation";

const standardCoverage = {percent:95, normal:1.96, chi2:5.991464547};
const coverage = () => run?.coverage ?? standardCoverage;
const sourcePath = id => id === "default" ? "/" : "/videos/" + id + "/";
const runPath = item => item.settings ? "/experiments/" + item.id : sourcePath(currentSource) + item.id;

async function get(path) {
  if (cloudflareApi && path.startsWith("/experiments/")) return (await cloudflareApi).request(path);
  const response = await fetch(path);
  if (!response.ok) throw new Error("Could not load " + path + " (" + response.status + ").");
  return response.json();
}

function showError(error) {
  track("client_error", 1, /video|MP4|dimension/i.test(error.message) ? "media" : "other");
  $("error").textContent = error.message;
  $("error").hidden = false;
}

function setupCanvas(canvas) {
  const {width, height} = canvas.getBoundingClientRect();
  if (!width || !height) return null;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return {ctx, width, height};
}

function viewport(width, height) {
  const scale = Math.min(width / manifest.width, height / manifest.height);
  return {scale, x:(width - manifest.width * scale) / 2, y:(height - manifest.height * scale) / 2};
}

function activeRecord() {
  if (!history) return null;
  const first = history[0].frame;
  return history[frame - first] ?? null;
}

function ellipse(ctx, state, cov, color, dashed, scale = 1) {
  const a = cov[0][0], b = cov[0][1], d = cov[1][1];
  const delta = Math.hypot(a - d, 2 * b);
  const rx = Math.sqrt(coverage().chi2 * Math.max(0, (a + d + delta) / 2));
  const ry = Math.sqrt(coverage().chi2 * Math.max(0, (a + d - delta) / 2));
  ctx.beginPath();
  ctx.setLineDash(dashed ? [5 / scale, 4 / scale] : []);
  ctx.lineWidth = 1.5 / scale;
  ctx.strokeStyle = color;
  ctx.ellipse(state[0], state[1], rx, ry, Math.atan2(2 * b, a - d) / 2, 0, 2 * Math.PI);
  ctx.stroke();
  ctx.setLineDash([]);
}

function preferredBox(track) {
  return ["corrected", "forecast", "detection"]
    .map(key => $(layers[key]).checked ? track[key] : null).find(Boolean);
}

function drawOverlay() {
  const canvas = setupCanvas(overlay);
  if (!canvas || !manifest) return;
  const {ctx, width, height} = canvas;
  const {x, y, scale} = viewport(width, height);
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(scale, scale);
  ctx.beginPath();
  ctx.rect(0, 0, manifest.width, manifest.height);
  ctx.clip();
  for (const track of frames[frame]?.tracks ?? []) {
    const highlighted = track.id === selected || track.id === hovered;
    ctx.lineWidth = (highlighted ? 2.4 : 1.05) / scale;
    for (const key of boxes) {
      const box = track[key];
      if (!box || !$(layers[key]).checked) continue;
      const [l, t, r, b] = box;
      ctx.strokeStyle = colors[key];
      ctx.setLineDash(key === "forecast" ? [5 / scale, 3 / scale] : []);
      if (key === "detection") {
        const arm = Math.min((r-l)/3, (b-t)/3, 9/scale);
        ctx.beginPath();
        for (const [x0,y0,sx,sy] of [[l,t,1,1],[r,t,-1,1],[l,b,1,-1],[r,b,-1,-1]]) {
          ctx.moveTo(x0 + sx*arm, y0);
          ctx.lineTo(x0, y0);
          ctx.lineTo(x0, y0 + sy*arm);
        }
        ctx.stroke();
      } else {
        ctx.strokeRect(l, t, r-l, b-t);
      }
    }
    const box = preferredBox(track);
    if ($("ids").checked && box) {
      ctx.font = (highlighted ? 13 : 10) / scale + "px " + mono;
      const text = "ID " + track.id, labelX = Math.max(3 / scale, box[0]);
      const labelY = Math.max(12 / scale, box[1] - 3 / scale);
      ctx.fillStyle = theme("deck-dark");
      ctx.fillRect(labelX - 2 / scale, labelY - 11 / scale, ctx.measureText(text).width + 4 / scale, 13 / scale);
      ctx.fillStyle = theme("light");
      ctx.fillText(text, labelX, labelY);
    }
  }
  const record = activeRecord();
  if ($("ellipse").checked && record && record.status !== "expired") {
    if (record.x_pred) ellipse(ctx, record.x_pred, record.P_pred, colors.forecast, true, scale);
    if (record.status !== "predicted") {
      ellipse(ctx, record.x_current, record.P_current, colors[record.status === "updated" ? "corrected" : "initialized"], false, scale);
    }
  }
  ctx.restore();
}

function hit(event) {
  if (!manifest || !frames.length) return null;
  const rect = stage.getBoundingClientRect(), view = viewport(rect.width, rect.height);
  const x = (event.clientX - rect.left - view.x) / view.scale;
  const y = (event.clientY - rect.top - view.y) / view.scale;
  if (x < 0 || x > manifest.width || y < 0 || y > manifest.height) return null;
  const pad = 5 / view.scale;
  const hits = (frames[frame]?.tracks ?? []).flatMap(track => {
    const box = preferredBox(track);
    if (!box) return [];
    const [l,t,r,b] = box;
    return x >= l-pad && x <= r+pad && y >= t-pad && y <= b+pad ? [{track, area:(r-l)*(b-t)}] : [];
  });
  hits.sort((a,b) => Number(b.track.id === selected) - Number(a.track.id === selected) || a.area - b.area);
  return hits[0]?.track ?? null;
}

function refresh() {
  if (!manifest) return;
  const data = frames[frame];
  if (!draggingTimeline) timeline.value = frame;
  if (!editingFrame) frameNumber.value = frame;
  $("detector").textContent = data ? (data.withheld ? "DETECTOR OFFLINE" : "DETECTOR ONLINE") : "Loading";
  $("detector").classList.toggle("offline", !!data?.withheld);
  drawOverlay();
  refreshInspector();
}

function refreshInspector() {
  const record = activeRecord(), meta = index.find(item => item.id === selected);
  $("track-title").textContent = selected === null ? "Track inspector" : "Track " + selected;
  $("clear").disabled = selected === null;
  $("analytics").hidden = selected === null;
  $("analytics").style.visibility = history ? "" : "hidden";
  $("expand-estimate").disabled = !history;
  $("metrics").hidden = selected === null;
  $("metrics").style.visibility = history ? "" : "hidden";
  const current = record?.status === "expired" ? null : record;
  const missing = "—";
  $("box-size").textContent = current
    ? current.x_current[2].toFixed(1) + " × " + current.x_current[3].toFixed(1) + " px" : missing;
  $("center-sigma").textContent = current
    ? Math.sqrt(current.P_current[0][0]).toFixed(2) + " / " + Math.sqrt(current.P_current[1][1]).toFixed(2) + " px"
    : missing;
  $("miss-count").textContent = current?.misses ?? missing;
  $("detector-score").textContent = current?.detection_confidence == null
    ? missing : (100 * current.detection_confidence).toFixed(1) + "%";
  if (selected === null) {
    $("status").textContent = "Click a car or choose a track by ID.";
  } else if (!history) {
    $("status").textContent = "Loading track history…";
  } else if (frame > meta.last) {
    const end = history.at(-1);
    $("status").textContent = end.expiry_reason === "unmatched_on_recovery"
      ? "Retired at frame " + end.frame + ": no matching box when detections returned."
      : "Track ended at frame " + meta.last + ".";
  } else if (frame < meta.first) {
    $("status").textContent = "Track starts at frame " + meta.first + ".";
  } else {
    const context = record.status === "predicted" ? (record.withheld ? " (global outage)" : " (no assigned measurement)") : "";
    $("status").textContent = statusText[record.status] + context;
  }
  if (history) renderCharts();
}

function series(ctx, rows, x, y, values, color, {dashed = false, points = false} = {}) {
  ctx.strokeStyle = ctx.fillStyle = color;
  ctx.lineWidth = 1.5;
  ctx.setLineDash(dashed ? [5, 3] : []);
  ctx.beginPath();
  let connected = false;
  for (const row of rows) {
    const value = values(row);
    if (value === null) { connected = false; continue; }
    const px = x(row, value), py = y(row, value);
    if (points) {
      ctx.moveTo(px + 2.4, py);
      ctx.arc(px, py, 2.4, 0, 2 * Math.PI);
    } else if (connected) ctx.lineTo(px, py);
    else ctx.moveTo(px, py);
    connected = true;
  }
  if (points) ctx.fill(); else ctx.stroke();
  ctx.setLineDash([]);
}

function timeChart(canvas, coordinate, title, unit) {
  canvas.classList.remove("spatial");
  canvas.setAttribute("aria-label", title + " versus source frame");
  const surface = setupCanvas(canvas);
  if (!surface) return;
  const {ctx, width, height} = surface;
  const textScale = estimateDialog.open ? 1.25 : 1;
  const plot = {left:51*textScale, top:30*textScale, right:width - 10, bottom:height - 22*textScale};
  const rows = history.filter(row => row.status !== "expired");
  const first = rows[0].frame, last = Math.max(first + 1, rows.at(-1).frame);
  const px = value => plot.left + (value - first) / (last - first) * (plot.right - plot.left);
  canvas.frameAt = x => Math.round(first + (x - plot.left) / (plot.right - plot.left) * (last - first));
  const estimate = row => row.x_current[coordinate];
  const normal = coverage().normal;
  const sd = row => normal * Math.sqrt(Math.max(0,row.P_current[coordinate][coordinate]));
  const values = rows.flatMap(row => [estimate(row) - sd(row), estimate(row) + sd(row), row.x_pred?.[coordinate], row.z?.[coordinate]].filter(value => value != null));
  const min = Math.min(...values), max = Math.max(...values), pad = Math.max((max - min) * 0.07, 0.01);
  const py = value => plot.bottom - (value - min + pad) / (max - min + 2*pad) * (plot.bottom - plot.top);
  ctx.font = 12*textScale + "px " + mono;
  ctx.fillStyle = theme("ink");
  ctx.fillText(title + " (" + unit + ")", 4, 15*textScale);
  ctx.font = 10*textScale + "px " + mono;
  for (let i = 0; i <= 3; i++) {
    const value = min - pad + (max - min + 2*pad) * i/3, y = py(value);
    ctx.fillStyle = theme("muted-ink"); ctx.fillText(value.toFixed(coordinate >= 4 ? 1 : 0), 4, y+3);
    ctx.strokeStyle = theme("plot-grid"); ctx.beginPath(); ctx.moveTo(plot.left,y); ctx.lineTo(plot.right,y); ctx.stroke();
  }
  const tickCount = estimateDialog.open ? Math.min(7, last - first + 1) : 2;
  for (let i = 0; i < tickCount; i++) {
    const tick = Math.round(first + (last - first) * i / (tickCount - 1));
    ctx.textAlign = i === 0 ? "left" : i === tickCount - 1 ? "right" : "center";
    ctx.fillText(tick, px(tick), height - 5);
  }
  ctx.textAlign = "left";
  ctx.save(); ctx.beginPath(); ctx.rect(plot.left,plot.top,plot.right-plot.left,plot.bottom-plot.top); ctx.clip();
  ctx.fillStyle = theme("plot-outage");
  for (const [start, end] of run.outages) ctx.fillRect(px(start),plot.top,px(end)-px(start),plot.bottom-plot.top);
  ctx.fillStyle = theme("plot-band"); ctx.beginPath();
  rows.forEach((row,i) => ctx[i ? "lineTo" : "moveTo"](px(row.frame),py(estimate(row)+sd(row))));
  for (const row of [...rows].reverse()) ctx.lineTo(px(row.frame),py(estimate(row)-sd(row)));
  ctx.closePath(); ctx.fill();
  const x = row => px(row.frame), y = (_, value) => py(value);
  series(ctx, rows, x, y, row => row.x_pred?.[coordinate] ?? null, plotColors.forecast, {dashed:true});
  series(ctx, rows, x, y, row => row.status === "updated" ? estimate(row) : null, plotColors.corrected);
  series(ctx, rows, x, y, row => row.status === "updated" ? estimate(row) : null, plotColors.corrected, {points:true});
  if (coordinate < 4) series(ctx, rows, x, y, row => row.z?.[coordinate] ?? null, plotColors.detection, {points:true});
  else series(ctx, rows, x, y, row => row.status === "initialized" ? estimate(row) : null, plotColors.detection, {points:true});
  ctx.strokeStyle = theme("ink"); ctx.beginPath(); ctx.moveTo(px(frame),plot.top); ctx.lineTo(px(frame),plot.bottom); ctx.stroke();
  ctx.restore();
}

function trajectory(canvas) {
  canvas.classList.add("spatial");
  canvas.setAttribute("aria-label", "Bounding-box center path in c_x and c_y");
  const surface = setupCanvas(canvas);
  if (!surface) return;
  const {ctx,width,height} = surface;
  const rows = history.filter(row => row.status !== "expired");
  const states = rows.flatMap(row => [row.x_current,row.x_pred,row.z].filter(Boolean));
  const xs = states.map(state => state[0]), ys = states.map(state => state[1]);
  const minX = Math.min(...xs) - 25, minY = Math.min(...ys) - 25;
  const w = Math.max(...xs) - minX + 25, h = Math.max(...ys) - minY + 25;
  const scale = Math.min((width-25)/w,(height-48)/h);
  const offsetX = (width - w*scale)/2, offsetY = 29 + (height-48 - h*scale)/2;
  ctx.fillStyle = theme("ink"); ctx.font = (estimateDialog.open ? 15 : 12) + "px " + mono;
  ctx.fillText("Box center path (c_x, c_y)", 5, estimateDialog.open ? 19 : 15);
  ctx.save(); ctx.translate(offsetX-minX*scale,offsetY-minY*scale); ctx.scale(scale,scale);
  const x = (_,state) => state[0], y = (_,state) => state[1];
  series(ctx, rows, x, y, row => row.x_pred, plotColors.forecast,{dashed:true});
  series(ctx, rows, x, y, row => row.status === "updated" ? row.x_current : null, plotColors.corrected);
  series(ctx, rows, x, y, row => row.z, plotColors.detection,{points:true});
  const record = activeRecord();
  if (record && record.status !== "expired") {
    if (record.x_pred) ellipse(ctx,record.x_pred,record.P_pred,plotColors.forecast,true,scale);
    if (record.status !== "predicted") ellipse(ctx,record.x_current,record.P_current,plotColors[record.status === "updated" ? "corrected" : "detection"],false,scale);
    ctx.fillStyle = theme("ink"); ctx.beginPath(); ctx.arc(...record.x_current.slice(0,2),3/scale,0,2*Math.PI); ctx.fill();
  }
  ctx.restore();
  canvas.frameAt = null;
}

const densityCoordinates = [
  ["Center x", "px"], ["Center y", "px"],
  ["Width", "px"], ["Height", "px"],
  ["Velocity x", "px/frame"], ["Velocity y", "px/frame"],
];
const densityGrid = $("density-charts");
const densityCanvases = densityCoordinates.map(() => {
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  densityGrid.append(canvas);
  return canvas;
});

function densityChart(canvas, coordinate, record) {
  const surface = setupCanvas(canvas);
  if (!surface) return;
  const {ctx, width, height} = surface;
  const [title, unit] = densityCoordinates[coordinate];
  const descriptions = [
    {label:"Forecast", mean:record.x_pred, covariance:record.P_pred, color:plotColors.forecast, dashed:true},
    ...(record.status === "updated" || record.status === "initialized" ? [{
      label:record.status === "initialized" ? "Initial estimate" : "Analysis",
      mean:record.x_current, covariance:record.P_current, color:plotColors.corrected, dashed:false,
    }] : []),
  ];
  const curves = descriptions.flatMap(({label, mean, covariance, color, dashed}) => {
    const mu = mean?.[coordinate], variance = covariance?.[coordinate]?.[coordinate];
    return Number.isFinite(mu) && Number.isFinite(variance)
      ? [{label, mu, sigma:Math.sqrt(Math.max(0, variance)), color, dashed}]
      : [];
  });
  canvas.setAttribute("aria-label", title + " (" + unit + "). " + curves.map(curve =>
    curve.label + " mean " + curve.mu.toFixed(2) + ", standard deviation " + curve.sigma.toFixed(2)
  ).join(". "));
  if (!curves.length) return;

  const left = 8, right = width - 8, top = 49, bottom = height - 19;
  const low = Math.min(...curves.map(({mu, sigma}) => mu - 4 * sigma));
  const high = Math.max(...curves.map(({mu, sigma}) => mu + 4 * sigma));
  const pad = Math.max((high - low) * 0.04, 0.0001);
  const x = value => left + (value - low + pad) / (high - low + 2 * pad) * (right - left);
  const positiveSpreads = curves.map(curve => curve.sigma).filter(sigma => sigma > 0);
  const smallestSpread = Math.min(...positiveSpreads);
  const short = value => Number(value.toPrecision(5)).toString();

  ctx.fillStyle = theme("ink");
  ctx.font = "11px " + mono;
  ctx.fillText(title + " (" + unit + ")", left, 14, right - left);
  ctx.font = "10px " + mono;
  curves.forEach(({mu, sigma, color}, index) => {
    ctx.fillStyle = color;
    ctx.fillText("μ " + short(mu) + "   σ " + short(sigma), left, 28 + index * 13, right - left);
  });
  ctx.strokeStyle = theme("plot-grid");
  ctx.beginPath(); ctx.moveTo(left, bottom); ctx.lineTo(right, bottom); ctx.stroke();
  ctx.fillStyle = theme("muted-ink");
  ctx.fillText(short(low - pad), left, height - 5, (right - left) * 0.45);
  ctx.textAlign = "right";
  ctx.fillText(short(high + pad), right, height - 5, (right - left) * 0.45);
  ctx.textAlign = "left";

  for (const {mu, sigma, color, dashed} of curves) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.8;
    ctx.setLineDash(dashed ? [4, 2] : []);
    ctx.beginPath();
    if (sigma === 0) {
      ctx.moveTo(x(mu), bottom);
      ctx.lineTo(x(mu), top);
    } else {
      for (let i = 0; i <= 60; i++) {
        const z = i / 60 * 8 - 4;
        const relativeDensity = smallestSpread / sigma * Math.exp(-0.5 * z * z);
        ctx[i ? "lineTo" : "moveTo"](x(mu + z * sigma), bottom - relativeDensity * (bottom - top));
      }
    }
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

function renderDensities(record) {
  const available = record && record.status !== "expired";
  densityGrid.hidden = !available;
  if (!available) {
    $("chart-note").textContent = "Choose a frame within this track to see its distributions.";
    return;
  }
  densityCanvases.forEach((canvas, coordinate) => densityChart(canvas, coordinate, record));
  const explanation = record.status === "predicted" ? "Forecast only on this frame. "
    : record.status === "initialized" ? "The first detection initializes the state. " : "";
  $("chart-note").textContent = explanation
    + "Each curve is a Gaussian marginal at frame " + frame
    + ". μ is its mean and σ its standard deviation.";
}

function renderCharts() {
  const a = $("chart-a"), b = $("chart-b");
  const densities = tab === "density";
  const record = densities ? activeRecord() : null;
  a.hidden = densities;
  b.hidden = densities || tab === "trajectory";
  $("graph-observation").hidden = densities || tab === "velocity";
  $("graph-initial").hidden = tab !== "velocity";
  $("graph-forecast").hidden = densities && !record?.x_pred;
  $("graph-analysis").hidden = densities && record?.status !== "updated";
  $("graph-density-initial").hidden = !densities || record?.status !== "initialized";
  densityGrid.hidden = true;
  if (densities) {
    renderDensities(record);
    return;
  }
  const level = coverage();
  const band = "The " + level.percent + "% band shows z̄ᵢ ± " + Number(level.normal.toFixed(3)) + "√Pᵢᵢ.";
  if (tab === "trajectory") {
    trajectory(a);
    $("chart-note").textContent = "The horizontal axis is c_x and the vertical axis is c_y. Both are measured in pixels from the video’s top-left corner, with c_y increasing downward. The ellipse shows the " + level.percent + "% model region at the current frame.";
  } else if (tab === "position") {
    timeChart(a,0,"Horizontal center c_x","px"); timeChart(b,1,"Vertical center c_y","px");
    $("chart-note").textContent = band + " Coral shading marks a detector outage. Click a chart to seek.";
  } else {
    timeChart(a,4,"Horizontal velocity v_x","px/frame"); timeChart(b,5,"Vertical velocity v_y","px/frame");
    $("chart-note").textContent = "The filter infers v_x and v_y from successive boxes. " + band;
  }
}

async function selectTrack(id) {
  const version = ++trackVersion;
  selected = id; history = null;
  if (id !== null) track("track_select");
  $("track").value = id ?? "";
  refresh();
  if (id === null) return;
  try {
    const data = await get(runPath(run) + "/tracks/" + id + ".json");
    if (version !== trackVersion) return;
    if (data.run !== run.id || data.id !== id || !data.frames?.length) throw new Error("Track history does not match this selection.");
    history = data.frames;
    refresh();
  } catch (error) {
    if (version === trackVersion) showError(error);
  }
}

async function loadRun(id) {
  const version = ++runVersion;
  const next = manifest.runs.find(item => item.id === id);
  if (!next) return;
  $("error").hidden = true;
  video.pause();
  frames = []; index = []; hovered = null; $("hover").hidden = true;
  selectTrack(null);
  $("track").disabled = true;
  try {
    const [geometry, tracks] = await Promise.all([get(runPath(next) + "/frames.json"), get(runPath(next) + "/tracks-index.json")]);
    if (version !== runVersion) return;
    if (geometry.length !== manifest.frame_count) throw new Error("Simulation frame count does not match the source video.");
    run = next; frames = geometry; index = tracks;
    $("ellipse-label").textContent = coverage().percent + "% center ellipse";
    const initialVelocity = run.settings?.model.initial_velocity ?? [0, 0];
    $("initial-velocity-value").textContent = "(" + initialVelocity.join(", ") + ")";
    $("track").replaceChildren(new Option("Choose a track",""),...index.map(item => new Option("ID " + item.id + " (frames " + item.first + "–" + item.last + ")",item.id)));
    $("track").disabled = !index.length;
    $("outages").replaceChildren(...run.outages.map(([start,end]) => {
      const item = document.createElement("i");
      item.style.left = 100*start/manifest.frame_count + "%";
      item.style.width = 100*(end-start)/manifest.frame_count + "%";
      return item;
    }));
    refresh();
  } catch (error) {
    if (version === runVersion) showError(error);
  }
}

function frameAtTime(time) {
  const ts = manifest.timestamps;
  let lo = 0, hi = ts.length - 1;
  while (lo < hi) {
    const mid = Math.floor((lo + hi + 1)/2);
    if (ts[mid] <= time + 0.00002) lo = mid; else hi = mid - 1;
  }
  return lo;
}

function presentFrame(time) {
  if (!manifest) return;
  const next = frameAtTime(time);
  if (pendingSeek !== null && (next < pendingSeek || (video.paused && next !== pendingSeek))) return;
  if (next !== frame) { frame = next; refresh(); }
  pendingSeek = null;
  if (queuedSeek !== null) {
    const requested = queuedSeek;
    queuedSeek = null;
    seek(requested, {pause:false});
  }
}

function seek(value, {pause = true} = {}) {
  if (!manifest || video.readyState < 1 || !Number.isFinite(value)) return;
  if (pause) video.pause();
  const requested = Math.min(manifest.frame_count-1,Math.max(0,Math.round(value)));
  // Let the current seek reach the screen before sending the latest queued target.
  if (pendingSeek !== null) {
    queuedSeek = requested;
    return;
  }
  const start = manifest.timestamps[requested], end = manifest.timestamps[requested+1] ?? video.duration;
  const target = start + Math.max(0,(end-start)/2);
  if (requested === frame && Math.abs(video.currentTime - target) < 0.00001) return;
  pendingSeek = requested;
  video.currentTime = target;
}

function togglePlay() {
  if (video.paused) {
    if (video.ended) seek(0);
    video.play().catch(showError);
  } else video.pause();
}

$("play").onclick = togglePlay;
$("restart").onclick = () => { track("seek", 1, "restart"); seek(0, {pause:false}); };
$("speed").onchange = event => {video.playbackRate = Number(event.target.value); track("speed_change", 1, String(Number(event.target.value)));};
timeline.oninput = () => seek(timeline.valueAsNumber, {pause:false});
timeline.onchange = () => track("seek", 1, "timeline");
timeline.onpointerdown = () => { draggingTimeline = true; };
function finishTimelineDrag() {
  if (!draggingTimeline) return;
  draggingTimeline = false;
  timeline.value = frame;
}
window.addEventListener("pointerup", finishTimelineDrag);
window.addEventListener("pointercancel", finishTimelineDrag);
frameNumber.onfocus = frameNumber.onclick = () => frameNumber.select();
frameNumber.oninput = () => { editingFrame = true; };
frameNumber.onchange = () => { track("seek", 1, "frame_input"); seek(frameNumber.valueAsNumber); };
frameNumber.onblur = () => {
  editingFrame = false;
  frameNumber.value = queuedSeek ?? pendingSeek ?? frame;
};
frameNumber.onkeydown = event => {
  if (event.key === "Enter") frameNumber.blur();
};
$("run").onchange = event => { track("scenario_change", 1, /^[a-f0-9]{32}$/.test(event.target.value) ? "custom" : "preset"); loadRun(event.target.value); };
$("track").onchange = event => selectTrack(event.target.value ? Number(event.target.value) : null);
$("clear").onclick = () => selectTrack(null);
const fullscreenButton = $("simulation-fullscreen");
const presentationActive = () => document.fullscreenElement === simulation || simulation.classList.contains("simulation-expanded");
const player = stage.closest(".player");
const simulationToolbar = fullscreenButton.parentElement;
const simulationSplitter = $("simulation-splitter");
let playerShare = 0.9;
let minPlayerWidth = 0, maxPlayerWidth = 0;
let splitterDrag = null;

function applyPresentationSplit() {
  const width = Math.floor(minPlayerWidth + playerShare * (maxPlayerWidth - minPlayerWidth));
  simulation.style.setProperty("--presentation-player-width", width + "px");
  simulationSplitter.setAttribute("aria-valuenow", Math.round(playerShare * 100));
}

function sizePresentationPlayer() {
  if (!presentationActive() || !manifest || !simulationSplitter.offsetWidth) return;
  const layout = getComputedStyle(simulation);
  const playerHeight = simulation.clientHeight - simulationToolbar.offsetHeight
    - parseFloat(layout.paddingTop) - parseFloat(layout.paddingBottom) - parseFloat(layout.rowGap);
  const controlsHeight = player.offsetHeight - stage.offsetHeight;
  const videoWidth = (playerHeight - controlsHeight) * manifest.width / manifest.height;
  const availableWidth = simulation.clientWidth - parseFloat(layout.paddingLeft) - parseFloat(layout.paddingRight)
    - simulationSplitter.offsetWidth - parseFloat(layout.getPropertyValue("--inspector-min-width"));
  maxPlayerWidth = Math.max(0, Math.min(videoWidth + player.offsetWidth - stage.offsetWidth, availableWidth));
  minPlayerWidth = Math.min(maxPlayerWidth, Math.max(420, maxPlayerWidth * 0.65));
  applyPresentationSplit();
}

function setPlayerShare(share) {
  playerShare = Math.min(1, Math.max(0, share));
  applyPresentationSplit();
}

simulationSplitter.onpointerdown = event => {
  if (event.button !== 0 || !event.isPrimary || maxPlayerWidth <= minPlayerWidth) return;
  simulationSplitter.focus({preventScroll:true});
  splitterDrag = {id:event.pointerId, x:event.clientX, width:player.offsetWidth};
  simulationSplitter.setPointerCapture(event.pointerId);
  simulationSplitter.classList.add("is-dragging");
};
simulationSplitter.onpointermove = event => {
  const range = maxPlayerWidth - minPlayerWidth;
  if (splitterDrag?.id !== event.pointerId || range <= 0) return;
  const width = splitterDrag.width + event.clientX - splitterDrag.x;
  setPlayerShare((width - minPlayerWidth) / range);
};
simulationSplitter.onlostpointercapture = () => {
  splitterDrag = null;
  simulationSplitter.classList.remove("is-dragging");
};
simulationSplitter.onkeydown = event => {
  if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
  event.preventDefault();
  setPlayerShare(playerShare + (event.key === "ArrowUp" ? 0.05 : -0.05));
};
const presentationResize = new ResizeObserver(sizePresentationPlayer);
for (const element of [simulation, simulationToolbar, player]) presentationResize.observe(element);
function syncFullscreenButton() {
  sizePresentationPlayer();
  fullscreenButton.textContent = presentationActive() ? "Exit fullscreen" : "Fullscreen";
}
function exitPresentation() {
  if (document.fullscreenElement === simulation) {
    document.exitFullscreen().catch(showError);
  } else {
    simulation.classList.remove("simulation-expanded");
    syncFullscreenButton();
  }
}
fullscreenButton.hidden = false;
fullscreenButton.onclick = async () => {
  if (presentationActive()) {
    exitPresentation();
    return;
  }
  if (document.fullscreenEnabled) {
    try {
      await simulation.requestFullscreen();
      return;
    } catch {
      // Embedded browsers can deny native fullscreen.
    }
  }
  simulation.classList.add("simulation-expanded");
  syncFullscreenButton();
};
document.addEventListener("fullscreenchange", syncFullscreenButton);
const estimateHome = estimatePanel.parentElement;
$("expand-estimate").onclick = () => {
  track("chart_expand");
  estimateDialog.append(estimatePanel);
  estimateDialog.showModal();
  $("close-estimate").focus({preventScroll:true});
  renderCharts();
};
$("close-estimate").onclick = () => estimateDialog.close();
estimateDialog.addEventListener("close", () => {
  estimateHome.append(estimatePanel);
  $("expand-estimate").focus({preventScroll:true});
  if (history) renderCharts();
});
function updateOverlayControls() {
  $("toggle-overlays").textContent = overlayInputs.some(input => input.checked) ? "Hide all" : "Show all";
  drawOverlay();
}
for (const input of overlayInputs) input.onchange = updateOverlayControls;
$("toggle-overlays").onclick = () => {
  const show = overlayInputs.every(input => !input.checked);
  for (const input of overlayInputs) input.checked = show;
  updateOverlayControls();
};
stage.onpointermove = event => {
  const track = hit(event);
  hovered = track?.id ?? null;
  $("hover").hidden = !track;
  if (track) $("hover").textContent = "ID " + track.id + " (" + statusText[track.status] + ")";
  stage.style.cursor = track ? "pointer" : "";
  drawOverlay();
};
stage.onpointerleave = () => {hovered = null; $("hover").hidden = true; drawOverlay();};
stage.onmousedown = event => {
  if (event.button === 0 && hit(event)) event.preventDefault();
};
stage.onclick = event => {
  const track = hit(event);
  if (track) {
    stage.focus({preventScroll:true});
    selectTrack(track.id);
  }
};
const page = document.documentElement;
document.addEventListener("pointerdown", () => page.classList.remove("playback-shortcut"), {capture:true});
document.addEventListener("keydown", event => {
  const simulationShortcut = !simulation.hidden && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey;
  const playbackShortcut = simulationShortcut && event.code === "Space";
  const frameShortcut = simulationShortcut && (event.key === "ArrowLeft" || event.key === "ArrowRight");
  page.classList.toggle("playback-shortcut", playbackShortcut || frameShortcut);
  if (playbackShortcut) {
    event.preventDefault();
    if (!event.repeat && !$("play").disabled) togglePlay();
  } else if (frameShortcut) {
    event.preventDefault();
    const target = queuedSeek ?? pendingSeek ?? frame;
    seek(target + (event.key === "ArrowRight" ? 1 : -1));
  } else if (event.key === "Escape" && !estimateDialog.open && !simulation.hidden && !document.fullscreenElement) {
    if (simulation.classList.contains("simulation-expanded")) exitPresentation();
    else selectTrack(null);
  }
}, {capture:true});
for (const button of document.querySelectorAll("[data-tab]")) button.onclick = () => {
  tab = button.dataset.tab;
  track("chart_tab", 1, tab);
  for (const item of document.querySelectorAll("[data-tab]")) item.setAttribute("aria-pressed",String(item === button));
  renderCharts();
};
for (const id of ["chart-a","chart-b"]) $(id).onclick = event => {
  const canvas = event.currentTarget;
  if (canvas.frameAt) { track("seek", 1, "chart"); seek(canvas.frameAt(event.clientX - canvas.getBoundingClientRect().left)); }
};
for (const event of ["play","pause","ended"]) video.addEventListener(event,() => {
  $("play").textContent = video.paused ? "Play" : "Pause";
});
if (video.requestVideoFrameCallback) {
  const present = (_, metadata) => {
    presentFrame(metadata.mediaTime);
    video.requestVideoFrameCallback(present);
  };
  video.requestVideoFrameCallback(present);
} else {
  video.addEventListener("seeked", () => presentFrame(video.currentTime));
  video.addEventListener("timeupdate", () => {
    if (!video.seeking) presentFrame(video.currentTime);
  });
}
new ResizeObserver(() => { drawOverlay(); if (history) renderCharts(); }).observe(stage);
const chartResize = new ResizeObserver(() => { if (history) renderCharts(); });
chartResize.observe($("chart-a"));
chartResize.observe(densityCanvases[0]);

const navLinks = [...document.querySelectorAll(".lab-nav a")];

function showView(view, {focus = false} = {}) {
  currentView = view;
  viewChanged(view);
  for (const panel of [simulation, modelView, settingsView]) panel.hidden = panel.id !== view;
  if (view !== "simulation" && presentationActive()) exitPresentation();
  for (const link of navLinks) {
    if (link.dataset.view === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (view === "simulation") {
    refresh();
  } else {
    video.pause();
    if (view === "model" && manifest) populateModel(manifest);
    if (focus) $(view).focus({preventScroll:true});
  }
}

function showCurrentView() {
  const path = window.location.pathname.replace(/\/$/, "");
  const link = navLinks.find(item => item.getAttribute("href") === path);
  showView(link?.dataset.view ?? "simulation");
}

function navigateTo(view) {
  const url = navLinks.find(link => link.dataset.view === view).href;
  if (url !== window.location.href) window.history.pushState(null, "", url);
  showView(view, {focus:true});
}

for (const link of navLinks) link.onclick = event => {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigateTo(link.dataset.view);
};
window.addEventListener("popstate", showCurrentView);
showCurrentView();
delete page.dataset.initialView;

function matrixCard(symbol, name, values, subscript = "") {
  const card = document.createElement("div");
  card.className = "matrix-card";
  const heading = document.createElement("h4"), formula = document.createElement("var");
  formula.textContent = symbol;
  if (subscript) {
    const sub = document.createElement("sub");
    sub.textContent = subscript;
    formula.append(sub);
  }
  heading.append(formula, " " + name);
  const table = document.createElement("table");
  table.setAttribute("aria-label", name + " matrix");
  const body = table.createTBody();
  for (const row of values) {
    const tr = body.insertRow();
    for (const value of row) {
      const td = tr.insertCell();
      td.textContent = value;
      if (value === 0) td.className = "zero";
    }
  }
  card.append(heading, table);
  return card;
}

function scenarioDescription(item) {
  let dropout;
  if (item.schedule) {
    const {seed, online_wait: wait, outage_length: length} = item.schedule;
    dropout = "Seed " + seed + ". Geometric waits with " + wait.exponential_rate_per_online_second
      + " starts per online second. Geometric gaps have recovery probability " + length.recovery_probability_per_frame
      + " per frame and a cap of " + length.max_frames + " frames.";
  } else if (item.outages.length) {
    const durations = item.outages.map(([start, end]) => end - start);
    dropout = "Scheduled gaps of " + durations.join(", ") + " frames. The outage miss allowance is "
      + item.max_misses + " frames.";
  } else {
    dropout = "No simulated detector outages.";
  }
  const noise = item.sensor_noise;
  if (!noise) return dropout;
  return dropout + " Each available box center independently has a " + 100 * noise.probability
    + "% chance of receiving Gaussian measurement noise, with standard deviations of "
    + 100 * noise.std_fraction + "% of box width and height and seed " + noise.seed + ". R is fixed during the run.";
}

function populateModel(data) {
  const settings = run?.settings;
  const {F, H, Q, R, P0} = settings?.model ?? data.model;
  const offset = settings?.model.b ?? Array(6).fill(0);
  const velocity = settings?.model.initial_velocity ?? [0, 0];
  $("model-matrices").replaceChildren(
    matrixCard("F", "State transition", F),
    matrixCard("H", "Observation operator", H),
    matrixCard("Q", "Process covariance per step", Q, "step"),
    matrixCard("R", "Observation covariance", R),
    matrixCard("P", "Initial track covariance", P0, "0"),
    matrixCard("b", "Per-step offset", [offset]),
  );
  $("model-context").textContent = (run?.label ?? "Standard presets") + " uses initial velocity ("
    + velocity.join(", ") + ") pixels per frame and " + coverage().percent + "% display regions.";
  const [numerator, denominator = 1] = data.fps.split("/").map(Number);
  const configuredMisses = run?.max_misses ?? data.online_miss_limit;
  const onlineMisses = settings?.association.online_misses ?? Math.min(configuredMisses, data.online_miss_limit ?? configuredMisses);
  const rule = settings?.association.retire_unmatched ?? true;
  $("model-tracking").textContent = "The source plays at " + (numerator / denominator).toFixed(2)
    + " frames per second. The selected scenario uses an IoU threshold of " + (run?.min_iou ?? 0.1)
    + " and allows " + onlineMisses + " online misses and " + configuredMisses + " outage misses."
    + (rule ? " Unmatched old tracks retire when readings return." : " The usual miss allowance applies after recovery.");
  const scenarios = $("model-scenarios");
  scenarios.replaceChildren();
  for (const item of data.runs) {
    const title = document.createElement("dt"), description = document.createElement("dd");
    title.textContent = item.label;
    description.textContent = scenarioDescription(item);
    scenarios.append(title, description);
  }
}

const scenarioMenu = $("run");
const mediaControls = ["run", "play", "restart", "timeline", "frame-number"].map($);

function addRun(item) {
  if (!manifest.runs.some(existing => existing.id === item.id)) {
    manifest.runs.push(item);
    scenarioMenu.add(new Option(item.label, item.id));
  }
}

async function loadSource(id, saved = []) {
  if (currentSource === id) {
    saved.forEach(addRun);
    return;
  }
  const next = await get(sourcePath(id) + "manifest.json");
  if (next.version !== 1 || next.frame_count !== next.timestamps?.length || !next.runs?.length) {
    throw new Error("The interactive export for this video is incomplete.");
  }
  // A local blob allows precise seeking with the preview server.
  const response = await fetch(new URL(next.video, window.location.origin));
  if (!response.ok) throw new Error("The source video could not be loaded.");
  const blob = await response.blob();

  video.pause();
  mediaControls.forEach(control => { control.disabled = true; });
  pendingSeek = queuedSeek = null;
  draggingTimeline = editingFrame = false;
  frame = 0;
  run = null;
  manifest = next;
  currentSource = id;
  scenarioMenu.replaceChildren(...manifest.runs.map(item => new Option(item.label, item.id)));
  saved.forEach(addRun);
  timeline.max = frameNumber.max = manifest.frame_count - 1;
  $("last-frame").textContent = frameNumber.max;
  stage.style.aspectRatio = manifest.width + "/" + manifest.height;
  sizePresentationPlayer();
  $("outages").replaceChildren();
  const geometry = loadRun(manifest.runs[0].id);
  const metadata = new Promise((resolve, reject) => {
    const ready = () => {
      video.removeEventListener("error", failed);
      resolve();
    };
    const failed = () => {
      video.removeEventListener("loadedmetadata", ready);
      reject(new Error("This browser could not play the source MP4."));
    };
    video.addEventListener("loadedmetadata", ready, {once:true});
    video.addEventListener("error", failed, {once:true});
  });
  if (mediaUrl) URL.revokeObjectURL(mediaUrl);
  mediaUrl = URL.createObjectURL(new Blob([blob], {type:"video/mp4"}));
  video.src = mediaUrl;
  await Promise.all([geometry, metadata]);
  if (video.videoWidth !== manifest.width || video.videoHeight !== manifest.height) {
    throw new Error("The source video dimensions do not match the tracking export.");
  }
  video.playbackRate = Number($("speed").value);
  mediaControls.forEach(control => { control.disabled = false; });
  populateModel(manifest);
  refresh();
}

async function init() {
  await loadSource("default");
  if (cloudflareApi) {
    import("./usage.js").then(usage => {
      usage.measurePlayback(video);
      track = usage.reportUsage;
      viewChanged = usage.startSession(currentView).setView;
      video.addEventListener("ended", () => track("video_complete"));
    }).catch(() => {});
  }
  initializeSettings({
    onVideo: loadSource,
    track: (...args) => track(...args),
    onRun: addRun,
    onDelete: async id => {
      const selectedRun = scenarioMenu.value === id;
      manifest.runs = manifest.runs.filter(item => item.id !== id);
      [...scenarioMenu.options].find(option => option.value === id)?.remove();
      if (selectedRun) {
        scenarioMenu.value = manifest.runs[0].id;
        await loadRun(scenarioMenu.value);
      }
      populateModel(manifest);
    },
    onOpen: id => {
      navigateTo("simulation");
      scenarioMenu.value = id;
      loadRun(id);
    },
  });
}
init().catch(showError);
