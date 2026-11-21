const form = document.getElementById("experiment-form");
const field = name => form.elements.namedItem(name);
const status = document.getElementById("experiment-status");
const runButton = document.getElementById("run-experiment");
const resetButton = document.getElementById("reset-settings");
const openButton = document.getElementById("open-experiment");
const matrixFields = document.getElementById("matrix-fields");
const matrices = {
  F: "F — state transition", H: "H — observation operator",
  Q: "Q per step — process covariance", R: "R — observation covariance",
  P0: "P₀ — initial covariance", b: "b — per-frame offset",
  initial_velocity: "Initial velocity (pixels/frame)",
};


const fieldHelp = {
  preset: "Loads the standard values for this scenario. Reset to preset restores them.",
  label: "This name identifies the saved run in the Simulation menu.",
  weights: "Which local YOLO model detects vehicles. A different model usually needs a new detection pass.",
  confidence: "Keep detections at or above this score. Raising it usually keeps fewer boxes.",
  classes: "Filter by detector class ID. Use 2 for cars, or leave blank to keep all classes.",
  min_iou: "Minimum box overlap required for a match. Higher values demand closer alignment.",
  online_misses: "How many consecutive frames a track can coast without a match while the detector is online.",
  max_misses: "How many consecutive missed frames a track can survive during a simulated outage.",
  retire_unmatched: "Retire old tracks without a match on the first frame with readings after an outage.",
  outage_mode: "Choose continuous detections, repeatable random gaps, or specific frame ranges.",
  outage_seed: "Repeating this seed with the same settings repeats the outage schedule.",
  start_rate: "Higher values start more gaps while the detector is online. Zero disables random outage starts.",
  recovery_probability: "The chance an outage ends on each missing frame. Higher values tend to shorten gaps.",
  max_frames: "The longest allowed geometric outage, in source frames.",
  ranges: "Enter start:end pairs. For example, 170:185 withholds frames 170 through 184.",
  noise_mode: "Choose the original detector readings or add Gaussian noise to some box centers.",
  noise_seed: "Repeats the random box shifts when the detections and measurement noise settings are the same.",
  noise_probability: "The chance any available box gets shifted. For example, 0.18 means an 18% chance.",
  std_fraction: "Scale of the Gaussian shift. A value of 0.16 gives SDs of 16% of box width and height. Zero disables the added noise.",
  coverage: "The model probability shown by the uncertainty bands and ellipse. Larger values draw wider regions.",
};
const matrixHelp = {
  F: "Maps the previous state to its one-frame forecast. The preset adds velocity to the center coordinates.",
  H: "Maps a state to the box the detector is expected to measure.",
  Q: "Process covariance added after each frame. Larger values represent more uncertainty about the motion model.",
  R: "Detector measurement covariance. Larger values generally give the observation less weight.",
  P0: "The filter's initial uncertainty in a newly initialized track.",
  b: "A constant vector added to each state forecast after applying F.",
  initial_velocity: "The initial horizontal and vertical center velocity assigned to each new track.",
};

function addHelp(caption, id, name, text, control) {
  const button = document.createElement("button"), tip = document.createElement("span");
  button.type = "button";
  button.className = "setting-help";
  button.textContent = "?";
  button.setAttribute("aria-label", "About " + name);
  button.setAttribute("aria-expanded", "false");
  tip.className = "setting-tip";
  tip.id = "setting-help-" + id;
  tip.setAttribute("role", "tooltip");
  tip.textContent = text;
  button.setAttribute("aria-describedby", tip.id);
  control.setAttribute("aria-describedby", tip.id);
  button.onclick = () => {
    button.setAttribute("aria-expanded", String(button.getAttribute("aria-expanded") === "false"));
  };
  button.onblur = () => button.setAttribute("aria-expanded", "false");
  caption.append(button, tip);
}

for (const caption of form.querySelectorAll("[data-help-for]")) {
  const name = caption.dataset.helpFor;
  addHelp(caption, name, caption.querySelector("label").textContent, fieldHelp[name], field(name));
}

const sliders = [...form.querySelectorAll("[data-number]")];
function syncSlider(slider) {
  const number = field(slider.dataset.number);
  if (number.value !== "") slider.value = number.value;
}
for (const slider of sliders) {
  const number = field(slider.dataset.number);
  slider.setAttribute("aria-describedby", number.getAttribute("aria-describedby"));
  slider.oninput = () => { number.value = slider.value; };
  number.addEventListener("input", () => syncSlider(slider));
}

async function request(path, options) {
  const response = await fetch(path, options);
  if (!(response.headers.get("Content-Type") ?? "").includes("application/json")) {
    throw new Error("Start the Traffic Lab with its launcher to run experiments.");
  }
  const body = await response.json();
  if (!response.ok) throw new Error(body.message);
  return body;
}

function showStatus(message, error = false) {
  status.textContent = message;
  status.classList.toggle("is-error", error);
}

function drawMatrix(name, value) {
  const wrapper = document.createElement("div");
  wrapper.className = "matrix-card matrix-editor";
  const heading = document.createElement("h4");
  heading.className = "setting-caption";
  heading.textContent = matrices[name];
  const table = document.createElement("table");
  table.setAttribute("aria-label", matrices[name]);
  const body = table.createTBody();
  const values = Array.isArray(value[0]) ? value : [value];
  values.forEach((row, i) => {
    const tr = body.insertRow();
    row.forEach((item, j) => {
      const input = document.createElement("input");
      input.type = "number";
      input.step = "any";
      input.name = [name, i, j].join("-");
      input.value = item;
      input.setAttribute("aria-label", matrices[name] + ", row " + (i + 1) + ", column " + (j + 1));
      tr.insertCell().append(input);
    });
  });
  addHelp(heading, "matrix-" + name, matrices[name], matrixHelp[name], table);
  const scroller = document.createElement("div");
  scroller.className = "matrix-scroller";
  scroller.append(table);
  wrapper.append(heading, scroller);
  return wrapper;
}

function showConditionalFields() {
  document.getElementById("seeded-fields").hidden = field("outage_mode").value !== "seeded";
  document.getElementById("manual-fields").hidden = field("outage_mode").value !== "manual";
  document.getElementById("measurement-noise-fields").hidden = field("noise_mode").value !== "gaussian";
}

function populate(settings, defaultNoiseScale) {
  const {model, detector, association, outage, noise} = settings;
  const values = {
    label: settings.label + " experiment",
    weights: detector.weights, confidence: detector.confidence, classes: detector.classes?.join(", ") ?? "",
    min_iou: association.min_iou, online_misses: association.online_misses, max_misses: association.max_misses,
    outage_mode: outage.mode, outage_seed: outage.seed, start_rate: outage.start_rate,
    recovery_probability: outage.recovery_probability, max_frames: outage.max_frames,
    ranges: outage.ranges.map(pair => pair.join(":")).join(", "),
    noise_mode: noise.std_fraction > 0 ? "gaussian" : "none",
    noise_seed: noise.seed, noise_probability: noise.probability,
    std_fraction: noise.std_fraction || defaultNoiseScale,
    coverage: settings.coverage,
  };
  for (const [name, value] of Object.entries(values)) field(name).value = value;
  for (const slider of sliders) syncSlider(slider);
  field("retire_unmatched").checked = association.retire_unmatched;
  matrixFields.replaceChildren(...Object.entries(model).map(([name, value]) => drawMatrix(name, value)));
  showConditionalFields();
}

function numeric(name, label = name) {
  const value = field(name).valueAsNumber;
  if (!Number.isFinite(value)) throw new Error("Enter a number for " + label + ".");
  return value;
}

function readSettings() {
  const model = {};
  for (const [name, label] of Object.entries(matrices)) {
    const table = matrixFields.querySelector('table[aria-label="' + label + '"]');
    const rows = Array.from(table.rows, (row, i) => Array.from(row.cells, (_, j) =>
      numeric([name, i, j].join("-"), label + ", row " + (i + 1) + ", column " + (j + 1))));
    model[name] = name === "b" || name === "initial_velocity" ? rows[0] : rows;
  }
  const classes = field("classes").value.trim();
  if (classes && !/^\d+(?:\s*,\s*\d+)*$/.test(classes)) {
    throw new Error("Enter class IDs as comma-separated integers.");
  }
  const mode = field("outage_mode").value;
  const addNoise = field("noise_mode").value === "gaussian";
  const ranges = field("ranges").value.trim();
  if (mode === "manual" && !/^\d+:\d+(?:[\s,]+\d+:\d+)*$/.test(ranges)) {
    throw new Error("Enter manual ranges such as 170:185, 195:225.");
  }
  return {
    label: field("label").value,
    model,
    detector: {weights:field("weights").value, confidence:numeric("confidence"),
               classes:classes ? classes.split(",").map(Number) : null},
    association: {
      min_iou: numeric("min_iou", "minimum IoU"), online_misses: numeric("online_misses", "online misses"),
      max_misses: numeric("max_misses", "outage misses"), retire_unmatched: field("retire_unmatched").checked,
    },
    outage: {
      mode, seed:numeric("outage_seed", "outage seed"), start_rate:numeric("start_rate", "start rate"),
      recovery_probability:numeric("recovery_probability", "recovery probability"),
      max_frames:numeric("max_frames", "maximum gap"),
      ranges: mode === "manual" ? ranges.split(/[\s,]+/).map(pair => pair.split(":").map(Number)) : [],
    },
    noise: addNoise ? {
      seed:numeric("noise_seed", "measurement noise seed"),
      probability:numeric("noise_probability", "measurement noise probability"),
      std_fraction:numeric("std_fraction", "measurement noise scale"),
    } : {seed:0, probability:0, std_fraction:0},
    coverage:numeric("coverage", "uncertainty region"),
  };
}

export async function initializeSettings({onRun, onOpen}) {
  try {
    const {presets, weights, runs, active} = await request("/api/settings");
    for (const run of runs) onRun(run);
    field("preset").replaceChildren(...presets.map(item => new Option(item.label,item.id)));
    field("weights").replaceChildren(...weights.map(name => new Option(name,name)));
    const defaultNoiseScale = presets.find(item => item.settings.noise.std_fraction > 0)?.settings.noise.std_fraction ?? 0.16;
    const reset = () => {
      openButton.hidden = true;
      populate(presets.find(item => item.id === field("preset").value).settings, defaultNoiseScale);
      showStatus("Using the selected preset.");
    };
    field("preset").disabled = resetButton.disabled = runButton.disabled = false;
    field("preset").onchange = reset;
    field("outage_mode").onchange = field("noise_mode").onchange = showConditionalFields;
    resetButton.onclick = reset;
    reset();

    let latestRun = null;
    openButton.onclick = () => onOpen(latestRun.id);

    async function watch(job) {
      while (true) {
        const result = await request("/api/jobs/" + job);
        showStatus(result.message, result.status === "failed");
        if (result.status === "failed") return;
        if (result.status === "complete") {
          latestRun = result.run;
          onRun(latestRun);
          openButton.hidden = false;
          return;
        }
        await new Promise(resolve => setTimeout(resolve,500));
      }
    }

    async function runJob(start) {
      runButton.disabled = resetButton.disabled = field("preset").disabled = true;
      openButton.hidden = true;
      try {
        await watch(await start());
      } catch (error) {
        showStatus(error.message, true);
      } finally {
        runButton.disabled = resetButton.disabled = field("preset").disabled = false;
      }
    }

    form.onsubmit = async event => {
      event.preventDefault();
      if (runButton.disabled) return;
      await runJob(async () => {
        const settings = readSettings();
        showStatus("Starting the experiment…");
        const {job} = await request("/api/experiments", {
          method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(settings),
        });
        return job;
      });
    };
    if (active) await runJob(async () => active);
  } catch (error) {
    showStatus(error.message, true);
  }
}
