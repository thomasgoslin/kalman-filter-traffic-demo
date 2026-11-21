const headers = {"Cache-Control": "no-store"};

export async function recordUsage(request, url, metrics) {
  const length = Number(request.headers.get("Content-Length"));
  if (request.headers.get("Origin") !== url.origin
      || request.headers.get("Content-Type") !== "application/json"
      || !Number.isInteger(length) || length < 1 || length > 128) {
    return new Response(null, {status:400, headers});
  }

  const data = await request.json().catch(() => null);
  if (!data || typeof data !== "object" || Object.keys(data).length !== 2) {
    return new Response(null, {status:400, headers});
  }
  const {event, value} = data;
  const valid = event === "watch_time"
    ? Number.isInteger(value) && value > 0 && value <= 30_000
    : (event === "video_start" || event === "experiment_complete") && value === 1;
  if (!valid) return new Response(null, {status:400, headers});

  metrics.writeDataPoint({blobs:[event], doubles:[value], indexes:[event]});
  return new Response(null, {status:204, headers});
}
