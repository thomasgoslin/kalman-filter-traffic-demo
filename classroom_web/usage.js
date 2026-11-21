export function reportUsage(event, value = 1) {
  if (location.protocol !== "https:") return;
  fetch("/api/usage", {
    method: "POST",
    keepalive: true,
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({event, value}),
  }).catch(() => {});
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
