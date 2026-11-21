import {reportUsage} from "./usage.js";

const jobs = new Map();
let setup, database, worker, active, activeMeta;

async function loadSetup() {
  const response = await fetch("/classroom-settings.json");
  if (!response.ok) throw new Error("The classroom settings could not be loaded.");
  return response.json();
}

async function openDatabase() {
  const {source} = await (setup ??= loadSetup());
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("kalman-classroom-" + source, 1);
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore("experiments", {keyPath:"run.id"});
      store.createIndex("name", "name", {unique:true});
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error("This browser could not open its experiment storage."));
  });
}

async function access(mode, operation) {
  const db = await (database ??= openDatabase());
  return new Promise((resolve, reject) => {
    const tx = db.transaction("experiments", mode);
    const request = operation(tx.objectStore("experiments"));
    tx.oncomplete = () => resolve(request.result);
    tx.onabort = () => reject(tx.error?.name === "ConstraintError"
      ? new Error("An experiment with that name already exists for this video.")
      : new Error("Could not access the experiments stored on this device."));
  });
}

const normalizedName = name => name.trim().toLowerCase();

function fail(job, message, reason = "worker") {
  jobs.set(job, {status:"failed", message});
  active = null;
  reportUsage("experiment_failed", 1, reason);
}

function startWorker() {
  const instance = new Worker("/classroom-worker.js");
  instance.onmessage = async ({data: {job, message, result, error}}) => {
    if (job !== active) return;
    if (error) {
      fail(job, error, "worker");
    } else if (result) {
      try {
        await access("readwrite", store => store.add({...result, name:normalizedName(result.run.label)}));
        jobs.set(job, {status:"complete", message:"Experiment complete.", run:result.run});
        active = null;
        reportUsage("experiment_complete", 1, activeMeta?.mix);
        if (activeMeta) reportUsage("experiment_duration", Math.max(1, Math.min(600_000, Math.round(performance.now() - activeMeta.startedAt))), activeMeta.mix);
      } catch (error) {
        fail(job, error.message, /already exists/.test(error.message) ? "duplicate" : "storage");
      }
    } else {
      jobs.set(job, {status:"running", message});
    }
  };
  instance.onerror = () => {
    if (active) fail(active, "Could not start the browser simulator.", "worker");
    instance.terminate();
    worker = null;
  };
  return instance;
}

export async function request(path, options = {}) {
  const url = new URL(path, location.origin);
  if (url.pathname === "/api/settings") {
    const info = await (setup ??= loadSetup());
    const records = await access("readonly", store => store.getAll());
    const runs = records.map(record => record.run).sort((a,b) => a.created - b.created);
    return {...info, runs, active};
  }
  if (url.pathname === "/api/experiments" && options.method === "POST") {
    const parsed = JSON.parse(options.body);
    const {label} = parsed;
    if (active) throw new Error("An experiment is already running.");
    if (!label.trim()) throw new Error("Enter an experiment name.");
    const existing = await access("readonly", store => store.index("name").getKey(normalizedName(label)));
    if (existing !== undefined) throw new Error("An experiment with that name already exists for this video.");
    if (active) throw new Error("An experiment is already running.");
    worker ??= startWorker();
    const job = crypto.randomUUID().replaceAll("-", "");
    active = job;
    activeMeta = {mix:(parsed.outage?.mode ?? "none") + "+" + (parsed.noise?.std_fraction > 0 ? "gaussian" : "none"), startedAt:performance.now()};
    reportUsage("experiment_start", 1, activeMeta.mix);
    jobs.set(job, {status:"running", message:"Preparing the experiment…"});
    worker.postMessage({job, body:options.body});
    return {job};
  }
  const job = url.pathname.match(/^\/api\/jobs\/([a-f0-9]{32})$/);
  if (job && jobs.has(job[1])) return jobs.get(job[1]);
  const deletion = url.pathname.match(/^\/api\/experiments\/([a-f0-9]{32})$/);
  if (deletion && options.method === "DELETE") {
    reportUsage("experiment_delete");
    return access("readwrite", store => store.delete(deletion[1]));
  }
  const data = url.pathname.match(/^\/experiments\/([a-f0-9]{32})\/(.+)$/);
  if (data) {
    const record = await access("readonly", store => store.get(data[1]));
    if (record && Object.hasOwn(record.files, data[2])) return record.files[data[2]];
  }
  throw new Error("Experiment data could not be found on this device.");
}
