const runtime = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
let ready;

async function bytes(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error("Could not load the Cloudflare mode simulation data.");
  return new Uint8Array(await response.arrayBuffer());
}

async function initialize() {
  const data = Promise.all([bytes("/simulator.zip"), bytes("/manifest.json"), bytes("/detections.json")]);
  importScripts(runtime + "pyodide.js");
  const pyodide = await loadPyodide({indexURL:runtime});
  await pyodide.loadPackage(["numpy", "scipy"]);
  const [source, manifest, detections] = await data;
  pyodide.unpackArchive(source, "zip", {extractDir:"/cloudflare"});
  pyodide.FS.writeFile("/cloudflare/manifest.json", manifest);
  pyodide.FS.writeFile("/cloudflare/detections.json", detections);
  pyodide.runPython(`
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, "/cloudflare")
from traffic_lab.experiments import Experiments

engine = Experiments(hosted_dir=Path("/cloudflare"))

def run_experiment(identifier, payload):
    settings = engine.validate(json.loads(payload))
    if settings["detector"] != engine.detector:
        raise ValueError("Cloudflare mode uses the bundled detections.")
    with TemporaryDirectory() as work:
        folder = Path(work) / "run"
        run = engine.simulate(folder, identifier, settings, engine.frames)
        files = {str(path.relative_to(folder)): json.loads(path.read_text(encoding="utf-8"))
                 for path in folder.rglob("*.json")}
    return json.dumps({"run": run, "files": files}, allow_nan=False)
`);
  return pyodide;
}

self.onmessage = async ({data: {job, body}}) => {
  try {
    if (!ready) {
      self.postMessage({job, message:"Loading the simulator on this device…"});
      ready = initialize().catch(error => { ready = null; throw error; });
    }
    const pyodide = await ready;
    self.postMessage({job, message:"Running the Kalman filter…"});
    const run = pyodide.globals.get("run_experiment");
    try {
      self.postMessage({job, result:JSON.parse(run(job, body))});
    } finally {
      run.destroy();
    }
  } catch (error) {
    const valueError = error.message.match(/ValueError: ([^\n]+)\s*$/);
    console.error(error);
    self.postMessage({job, error:valueError?.[1] ?? "Could not run the browser simulation. Try again after reloading."});
  }
};
