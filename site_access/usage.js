const headers = {"Cache-Control": "no-store"};

// Every event the site may record. Nothing outside this table is ever stored.
//   value:  checks the numeric value
//   detail: a Set (or RegExp) of allowed labels; omit it when the event has no label
//   needsDetail: reject the event when the label is missing
const one = value => value === 1;
const ms = max => value => Number.isInteger(value) && value > 0 && value <= max;
const views = new Set(["simulation", "model", "settings"]);
const mix = /^(none|seeded|manual)\+(none|gaussian)$/;

const events = {
  // existing events (unchanged behavior)
  watch_time:          {value: ms(30_000)},
  video_start:         {value: one},
  experiment_complete: {value: one, detail: mix},
  // new events
  page_load:           {value: one, detail: new Set(["phone", "tablet", "desktop"]), needsDetail: true},
  page_view:           {value: one, detail: views, needsDetail: true},
  view_time:           {value: ms(3_600_000), detail: views, needsDetail: true},
  video_complete:      {value: one},
  speed_change:        {value: one, detail: /^\d(\.\d{1,2})?$/, needsDetail: true},
  seek:                {value: one, detail: new Set(["timeline", "frame_input", "chart", "restart"]), needsDetail: true},
  track_select:        {value: one},
  chart_tab:           {value: one, detail: new Set(["position", "velocity", "trajectory"]), needsDetail: true},
  chart_expand:        {value: one},
  scenario_change:     {value: one, detail: new Set(["preset", "custom"]), needsDetail: true},
  experiment_start:    {value: one, detail: mix, needsDetail: true},
  experiment_failed:   {value: one, detail: new Set(["duplicate", "storage", "worker"]), needsDetail: true},
  experiment_duration: {value: ms(600_000), detail: mix},
  experiment_delete:   {value: one},
  video_upload:        {value: one, detail: new Set(["started", "complete", "failed"]), needsDetail: true},
  client_error:        {value: one, detail: new Set(["media", "other"]), needsDetail: true},
};

const allowedKeys = new Set(["event", "value", "detail", "session"]);
const idPattern = /^[a-f0-9]{16}$/;

function detailOk(rule, detail) {
  if (detail === undefined) return !rule.needsDetail;
  if (!rule.detail || typeof detail !== "string") return false;
  return rule.detail instanceof RegExp ? rule.detail.test(detail) : rule.detail.has(detail);
}

export async function recordUsage(request, url, metrics) {
  const length = Number(request.headers.get("Content-Length"));
  if (request.headers.get("Origin") !== url.origin
      || request.headers.get("Content-Type") !== "application/json"
      || !Number.isInteger(length) || length < 1 || length > 300) {
    return new Response(null, {status:400, headers});
  }

  const data = await request.json().catch(() => null);
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return new Response(null, {status:400, headers});
  }
  const keys = Object.keys(data);
  if (!keys.includes("event") || !keys.includes("value") || keys.some(key => !allowedKeys.has(key))) {
    return new Response(null, {status:400, headers});
  }

  const {event, value, detail, session} = data;
  const rule = Object.hasOwn(events, event) ? events[event] : null;
  const idOk = id => id === undefined || (typeof id === "string" && idPattern.test(id));
  if (!rule || !rule.value(value) || !detailOk(rule, detail) || !idOk(session)) {
    return new Response(null, {status:400, headers});
  }

  // blob1 = event, blob2 = label, blob3 = random per-tab id (no personal data); double1 = value
  metrics.writeDataPoint({blobs:[event, detail ?? "", session ?? ""], doubles:[value], indexes:[event]});
  return new Response(null, {status:204, headers});
}
