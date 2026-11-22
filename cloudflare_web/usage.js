// Random 16-hex ID with no personal data. It lasts until the tab closes and is never stored longer.
function storedId(storageName, key) {
  try {
    const storage = window[storageName];
    let id = storage.getItem(key);
    if (!/^[a-f0-9]{16}$/.test(id ?? "")) {
      id = Array.from(crypto.getRandomValues(new Uint8Array(8)), byte => byte.toString(16).padStart(2, "0")).join("");
      storage.setItem(key, id);
    }
    return id;
  } catch {
    return null;
  }
}

const sessionId = storedId("sessionStorage", "usage-session");
try { localStorage.removeItem("usage-visitor"); } catch {} // remove a leftover ID from older builds

export function reportUsage(event, value = 1, detail) {
  if (location.protocol !== "https:") return;
  const body = {event, value};
  if (detail) body.detail = detail;
  if (sessionId) body.session = sessionId;
  fetch("/api/usage", {
    method: "POST",
    keepalive: true,
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  }).catch(() => {});
}

export function startSession(initialView) {
  if (location.protocol !== "https:") return {setView() {}};
  let view = initialView;
  let since = document.hidden ? null : performance.now();
  reportUsage("page_load", 1, innerWidth < 640 ? "phone" : innerWidth < 1024 ? "tablet" : "desktop");
  reportUsage("page_view", 1, view);

  function flush() {
    if (since === null) return;
    const elapsed = Math.min(3_600_000, Math.round(performance.now() - since));
    since = null;
    if (elapsed > 0) reportUsage("view_time", elapsed, view);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) flush();
    else since = performance.now();
  });
  window.addEventListener("pagehide", flush);
  return {
    setView(next) {
      if (next === view) return;
      flush();
      view = next;
      if (!document.hidden) since = performance.now();
      reportUsage("page_view", 1, view);
    },
  };
}

export function measurePlayback(video) {
  if (location.protocol !== "https:") return;
  let firstPlay = true;
  let startedAt = null;
  let timer;

  function reportElapsed() {
    const now = performance.now();
    const milliseconds = Math.min(30_000, Math.round(now - startedAt));
    startedAt = now;
    if (milliseconds > 0) reportUsage("watch_time", milliseconds);
  }

  function stop() {
    if (startedAt === null) return;
    clearInterval(timer);
    reportElapsed();
    startedAt = null;
  }

  function sync() {
    if (document.hidden || video.paused || video.seeking || video.ended || video.readyState < 3) {
      stop();
      return;
    }
    if (startedAt !== null) return;
    if (firstPlay) {
      reportUsage("video_start");
      firstPlay = false;
    }
    startedAt = performance.now();
    timer = setInterval(reportElapsed, 15_000);
  }

  for (const event of ["playing", "seeked", "canplay"]) video.addEventListener(event, sync);
  for (const event of ["pause", "waiting", "seeking", "ended", "emptied"]) video.addEventListener(event, stop);
  document.addEventListener("visibilitychange", sync);
  window.addEventListener("pagehide", stop);
  window.addEventListener("pageshow", sync);
  sync();
}
