"""Rerender the four demo videos in parallel from cached detections."""

import json
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from .scenarios import RUNS
from .workspace import CACHE, OUTPUTS, ROOT, VIDEO, digest, pipeline_hashes, replace_outputs


SHARED_FLAGS = ["--presentation", "--cache", str(CACHE)]
PROGRESS_INTERVAL = 3.0  # Seconds between updates
PRINT_LOCK = threading.Lock()


def status(name, message):
    """Print a complete, thread-safe status line."""
    with PRINT_LOCK:
        print(f"[{name}] {message}", flush=True)


def source_frame_count():
    """Get total source frames, counting them if metadata is unavailable."""
    def probe(count_frames=False):
        command = ["ffprobe", "-v", "error", "-select_streams", "v:0"]
        if count_frames:
            command.append("-count_frames")
        command += [
            "-show_entries", "stream=nb_frames,nb_read_frames",
            "-of", "json", str(VIDEO),
        ]
        data = json.loads(
            subprocess.check_output(command, cwd=ROOT)
        )
        return data["streams"][0]

    stream = probe()

    if stream.get("nb_frames") not in (None, "N/A", "0"):
        return int(stream["nb_frames"])

    stream = probe(count_frames=True)
    return int(stream["nb_read_frames"])


def count_rendered_frames(frames_dir):
    """Count PNG frames already written by the renderer."""
    if not frames_dir.exists():
        return 0
    return sum(1 for _ in frames_dir.glob("*.png"))


def render_run(stage, logs, name, flags, total_frames):
    video = stage / f"{name}.mp4"
    frames_dir = stage / f"{name}-frames"
    log_path = logs / f"{name}.log"

    command = [
        sys.executable, "-m", "traffic_lab.render",
        str(VIDEO), str(video),
        *SHARED_FLAGS,
        "--frames-dir", str(frames_dir),
        *flags,
    ]

    started = time.monotonic()
    status(name, "STARTED")

    # Capture renderer output to avoid mixing logs from concurrent runs.
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

        next_report = started + PROGRESS_INTERVAL

        try:
            while process.poll() is None:
                now = time.monotonic()

                if now >= next_report:
                    completed = count_rendered_frames(frames_dir)
                    elapsed = now - started
                    percentage = 100 * completed / total_frames

                    status(
                        name,
                        f"RENDERING {completed}/{total_frames} frames "
                        f"({percentage:.1f}%) | {elapsed:.1f}s"
                    )

                    next_report = now + PROGRESS_INTERVAL

                time.sleep(0.5)

            returncode = process.wait()

        except BaseException:
            process.terminate()
            process.wait()
            raise

    elapsed = time.monotonic() - started
    completed = count_rendered_frames(frames_dir)

    if returncode != 0:
        status(name, f"FAILED | exit code {returncode} | {elapsed:.1f}s")

        with log_path.open(encoding="utf-8", errors="replace") as log:
            tail = deque(log, maxlen=25)

        with PRINT_LOCK:
            print(f"\n--- Last log lines: {name} ---", file=sys.stderr)
            print("".join(tail), file=sys.stderr)
            print("--- End log ---\n", file=sys.stderr)

        raise subprocess.CalledProcessError(returncode, command)

    status(
        name,
        f"DONE | {completed}/{total_frames} frames | {elapsed:.1f}s"
    )

    probe = subprocess.check_output([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,nb_frames",
        "-of", "json", str(video),
    ], cwd=ROOT)

    return {
        "flags": flags,
        "video": json.loads(probe)["streams"][0],
    }


def main():
    for path in (VIDEO, CACHE):
        if not (ROOT / path).is_file():
            raise FileNotFoundError(f"Missing {ROOT / path}")

    total_frames = source_frame_count()

    status(
        "ALL",
        f"{len(RUNS)} renders queued | "
        f"2 concurrent | {total_frames} source frames"
    )

    workspace = ROOT / OUTPUTS / ".render-all"
    workspace.mkdir()

    stage = workspace / "new"
    backup = workspace / "previous"
    logs = workspace / "logs"

    stage.mkdir()
    logs.mkdir()

    overall_start = time.monotonic()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {
                name: pool.submit(
                    render_run,
                    stage,
                    logs,
                    name,
                    flags,
                    total_frames,
                )
                for name, (_, flags) in RUNS.items()
            }

            runs = {
                name: job.result()
                for name, job in jobs.items()
            }

        manifest = {
            "source": VIDEO.name,
            "source_sha256": digest(VIDEO),
            "renderer_sha256": pipeline_hashes(),
            "detections_cache": CACHE.name,
            "detections_sha256": digest(CACHE),
            "pngs": (
                "Rendered alongside their corresponding annotated MP4, "
                "numbered from zero."
            ),
            "shared_flags": SHARED_FLAGS,
            "runs": runs,
        }

        (stage / "simulation-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )

        status("ALL", "Installing completed outputs...")
        replace_outputs(stage, backup)

    except (Exception, KeyboardInterrupt):
        if backup.exists() and any(backup.iterdir()):
            print(
                f"Previous outputs are available for recovery in {backup}",
                file=sys.stderr,
            )
        else:
            shutil.rmtree(workspace)
        raise

    shutil.rmtree(workspace)

    elapsed = time.monotonic() - overall_start
    status(
        "ALL",
        f"Successfully refreshed all {len(RUNS)} demos "
        f"in {elapsed:.1f}s | {ROOT / OUTPUTS}"
    )


if __name__ == "__main__":
    main()
