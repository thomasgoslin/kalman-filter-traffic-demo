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
const simulation = $("simulation"), modelView = $("model");
let draggingTimeline = false, editingFrame = false;
let manifest, run, frames = [], index = [], history = null, selected = null, hovered = null;
let frame = 0, tab = "position", runVersion = 0, trackVersion = 0;
let pendingSeek = null, queuedSeek = null;

async function get(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error("Could not load " + path + " (" + response.status + ").");
  return response.json();
}

function showError(error) {
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
  const rx = Math.sqrt(5.991464547 * Math.max(0, (a + d + delta) / 2));
  const ry = Math.sqrt(5.991464547 * Math.max(0, (a + d - delta) / 2));
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
  $("analytics").hidden = !history;
  $("expand-estimate").disabled = !history;
  $("metrics").hidden = !history;
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
  const sd = row => 1.96 * Math.sqrt(Math.max(0,row.P_current[coordinate][coordinate]));
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

function renderCharts() {
  const a = $("chart-a"), b = $("chart-b");
  b.hidden = tab === "trajectory";
  $("graph-observation").hidden = tab === "velocity";
  $("graph-initial").hidden = tab !== "velocity";
  if (tab === "trajectory") {
    trajectory(a);
    $("chart-note").textContent = "The horizontal axis is c_x and the vertical axis is c_y. Both are measured in pixels from the video’s top-left corner, with c_y increasing downward. The ellipse shows the 95% model region at the current frame.";
  } else if (tab === "position") {
    timeChart(a,0,"Horizontal center c_x","px"); timeChart(b,1,"Vertical center c_y","px");
    $("chart-note").textContent = "The band shows z̄ᵢ ± 1.96√Pᵢᵢ at each frame. Coral shading marks a detector outage. Click a chart to seek.";
  } else {
    timeChart(a,4,"Horizontal velocity v_x","px/frame"); timeChart(b,5,"Vertical velocity v_y","px/frame");
    $("chart-note").textContent = "The filter infers v_x and v_y from successive boxes. The band shows z̄ᵢ ± 1.96√Pᵢᵢ.";
  }
}

async function selectTrack(id) {
  const version = ++trackVersion;
  selected = id; history = null;
  $("track").value = id ?? "";
  refresh();
  if (id === null) return;
  try {
    const data = await get(run.id + "/tracks/" + id + ".json");
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
  $("error").hidden = true;
  video.pause();
  frames = []; index = []; hovered = null; $("hover").hidden = true;
  selectTrack(null);
  $("track").disabled = true;
  try {
    const [geometry, tracks] = await Promise.all([get(next.id + "/frames.json"), get(next.id + "/tracks-index.json")]);
    if (version !== runVersion) return;
    if (geometry.length !== manifest.frame_count) throw new Error("Simulation frame count does not match the source video.");
    run = next; frames = geometry; index = tracks;
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
$("restart").onclick = () => seek(0, {pause:false});
$("speed").onchange = event => {video.playbackRate = Number(event.target.value);};
timeline.oninput = () => seek(timeline.valueAsNumber, {pause:false});
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
frameNumber.onchange = () => seek(frameNumber.valueAsNumber);
frameNumber.onblur = () => {
  editingFrame = false;
  frameNumber.value = queuedSeek ?? pendingSeek ?? frame;
};
frameNumber.onkeydown = event => {
  if (event.key === "Enter") frameNumber.blur();
};
$("run").onchange = event => loadRun(event.target.value);
$("track").onchange = event => selectTrack(event.target.value ? Number(event.target.value) : null);
$("clear").onclick = () => selectTrack(null);
const estimateHome = estimatePanel.parentElement;
$("expand-estimate").onclick = () => {
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
stage.onclick = event => {
  const track = hit(event);
  if (track) selectTrack(track.id);
};
stage.onkeydown = event => {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    const target = queuedSeek ?? pendingSeek ?? frame;
    event.preventDefault(); seek(target + (event.key === "ArrowRight" ? 1 : -1));
  }
};
const page = document.documentElement;
document.addEventListener("pointerdown", () => page.classList.remove("playback-shortcut"), {capture:true});
document.addEventListener("keydown", event => {
  const playbackShortcut = !simulation.hidden && event.code === "Space" && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey;
  page.classList.toggle("playback-shortcut", playbackShortcut);
  if (playbackShortcut) {
    event.preventDefault();
    if (!event.repeat && !$("play").disabled) togglePlay();
  } else if (event.key === "Escape" && !estimateDialog.open && !simulation.hidden) {
    selectTrack(null);
  }
}, {capture:true});
for (const button of document.querySelectorAll("[data-tab]")) button.onclick = () => {
  tab = button.dataset.tab;
  for (const item of document.querySelectorAll("[data-tab]")) item.setAttribute("aria-pressed",String(item === button));
  renderCharts();
};
for (const id of ["chart-a","chart-b"]) $(id).onclick = event => {
  const canvas = event.currentTarget;
  if (canvas.frameAt) seek(canvas.frameAt(event.clientX - canvas.getBoundingClientRect().left));
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
new ResizeObserver(() => { if (history) renderCharts(); }).observe($("chart-a"));

const navButtons = document.querySelectorAll(".lab-nav button");
for (const button of navButtons) button.onclick = () => {
  const showModel = button.dataset.view === "model";
  if (showModel) video.pause();
  simulation.hidden = showModel;
  modelView.hidden = !showModel;
  $("scenario-control").hidden = showModel;
  for (const item of navButtons) {
    if (item === button) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
  if (showModel) modelView.focus({preventScroll:true});
  else refresh();
};

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
  if (!item.schedule) {
    if (!item.outages.length) return "Cached detector readings without simulated outages or jitter.";
    const durations = item.outages.map(([start, end]) => end - start);
    return "Scheduled gaps of " + durations.join(", ") + " frames. The outage miss allowance is " + item.max_misses + " frames.";
  }
  const {seed, online_wait: wait, outage_length: length} = item.schedule;
  const dropout = "Seed " + seed + ". Geometric waits with " + wait.exponential_rate_per_online_second
    + " starts per online second. Geometric gaps have recovery probability " + length.recovery_probability_per_frame
    + " per frame and a cap of " + length.max_frames + " frames.";
  const noise = item.sensor_noise;
  if (!noise) return dropout;
  return dropout + " Each available box center independently has a " + Math.round(100 * noise.probability)
    + "% chance of receiving Gaussian jitter, with standard deviations of "
    + Math.round(100 * noise.std_fraction) + "% of box width and height and seed " + noise.seed + ". The assumed R stays fixed.";
}

function populateModel(data) {
  const {F, H, Q, R, P0} = data.model;
  $("model-matrices").replaceChildren(
    matrixCard("F", "State transition", F),
    matrixCard("H", "Observation operator", H),
    matrixCard("Q", "Process covariance per step", Q, "step"),
    matrixCard("R", "Observation covariance", R),
    matrixCard("P", "Initial track covariance", P0, "0"),
  );
  const [numerator, denominator = 1] = data.fps.split("/").map(Number);
  const caps = [...new Set(data.runs.map(item => Math.min(item.max_misses, data.online_miss_limit ?? item.max_misses)))];
  const overlap = [...new Set(data.runs.map(item => item.min_iou))];
  $("model-tracking").textContent = "The source plays at " + (numerator / denominator).toFixed(2)
    + " frames per second. Association uses matching classes and an IoU threshold of " + overlap.join(" / ")
    + ". While the detector is online, the miss allowance is " + caps.join(" / ")
    + " frames. Unmatched old tracks retire on the first frame with readings after an outage.";
  const scenarios = $("model-scenarios");
  for (const item of data.runs) {
    const title = document.createElement("dt"), description = document.createElement("dd");
    title.textContent = item.label;
    description.textContent = scenarioDescription(item);
    scenarios.append(title, description);
  }
}

async function init() {
  manifest = await get("manifest.json");
  if (manifest.version !== 1 || manifest.frame_count !== manifest.timestamps?.length || !manifest.runs?.length) {
    throw new Error("The demo export is incomplete. Run build_interactive_export.py again.");
  }
  populateModel(manifest);
  $("run").replaceChildren(...manifest.runs.map(item => new Option(item.label,item.id)));
  timeline.max = frameNumber.max = manifest.frame_count - 1;
  $("last-frame").textContent = frameNumber.max;
  stage.style.aspectRatio = manifest.width + "/" + manifest.height;
  // Loading the small source clip once enables arbitrary seeks on the static Python server.
  const media = fetch(manifest.video).then(response => {
    if (!response.ok) throw new Error("The source video is missing. Rebuild the interactive export.");
    return response.blob();
  });
  await loadRun(manifest.runs[0].id);
  const blob = await media;
  video.addEventListener("loadedmetadata",() => {
    if (video.videoWidth !== manifest.width || video.videoHeight !== manifest.height) {
      showError(new Error("The source video dimensions do not match the tracking export."));
      return;
    }
    for (const id of ["run","play","restart","timeline","frame-number"]) $(id).disabled = false;
    refresh();
  },{once:true});
  video.addEventListener("error",() => showError(new Error("This browser could not play the source MP4.")),{once:true});
  video.src = URL.createObjectURL(new Blob([blob],{type:"video/mp4"}));
}
init().catch(showError);
