"""Prepare and index local MP4 sources for the interactive viewer."""

import json
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from .experiments import VIDEO_LIBRARY, read_json, run_detector
from .export import export_run, write_json
from .media import read_video_info, source_timestamps
from .workspace import digest


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_SECONDS = 60
MAX_FRAMES = 3600


def available_videos():
    videos = [{"id": "default", "label": "Built-in traffic clip"}]
    for folder in VIDEO_LIBRARY.glob("*"):
        if re.fullmatch(r"[a-f0-9]{32}", folder.name) and (folder / "manifest.json").is_file():
            videos.append({"id": folder.name, "label": read_json(folder / "manifest.json")["label"]})
    return videos[:1] + sorted(videos[1:], key=lambda video: video["label"].casefold())


def normalize_mp4(upload, video):
    try:
        probe = subprocess.check_output([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_entries", "format=format_name,duration:format_tags=major_brand",
            "-of", "json", str(upload),
        ], timeout=20)
        metadata = json.loads(probe)["format"]
        brand = metadata.get("tags", {}).get("major_brand", "").strip().lower()
        if ("mp4" not in metadata["format_name"].split(",")
                or not brand or brand in {"qt", "mjp2", "jp2"} or brand.startswith(("3g", "mj2"))):
            raise ValueError("Upload a valid MP4 video.")
        if float(metadata.get("duration", 0)) > MAX_SECONDS:
            raise ValueError(f"Choose an MP4 of at most {MAX_SECONDS} seconds.")
        subprocess.run([
            "ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe",
            "-f", "mov", "-i", str(upload), "-map", "0:V:0", "-an", "-map_metadata", "-1",
            "-t", str(MAX_SECONDS + 1), "-frames:v", str(MAX_FRAMES + 1), "-fps_mode", "passthrough",
            "-vf", "scale=w='min(1920,iw)':h='min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(video),
        ], check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyError) as error:
        raise ValueError("Could not read this MP4. Try a short, playable video.") from error


def prepare_video(identifier, upload, label, engine, report):
    report("Preparing the MP4…")
    with TemporaryDirectory(prefix=".video-", dir=VIDEO_LIBRARY) as temporary:
        stage = Path(temporary) / "ready"
        stage.mkdir()
        video = stage / "source.mp4"
        normalize_mp4(upload, video)
        width, height, fps, _ = read_video_info(video)
        timestamps = source_timestamps(video)
        if len(timestamps) > MAX_FRAMES or timestamps[-1] >= MAX_SECONDS:
            raise ValueError(f"Choose an MP4 of at most {MAX_SECONDS} seconds and {MAX_FRAMES} frames.")
        source_hash = digest(video)
        manifest = {
            "version": 1, "label": label, "video": f"/videos/{identifier}/source.mp4",
            "width": width, "height": height, "fps": fps, "timestamps": timestamps,
            "frame_count": len(timestamps), "source_sha256": source_hash,
            "model": engine.manifest["model"], "online_miss_limit": engine.manifest["online_miss_limit"],
        }
        weights = engine.weights().get(engine.detector["weights"])
        if weights is None:
            raise ValueError("Add the default YOLO weights to inputs/ before uploading videos.")
        report("Loading detector weights…")
        frames = run_detector(video, manifest, weights, engine.detector, report)
        write_json(stage / "detections.json", {
            "identity": {"version": 1, "video_sha256": source_hash, "detector": engine.detector},
            "frames": frames,
        })
        runs = []
        scale = len(frames) / engine.manifest["frame_count"]
        for preset in engine.manifest["runs"]:
            report(f"Preparing {preset['label']}…")
            flags = preset["flags"].copy()
            # Retain the relative placement of manual outages on differently sized clips.
            for i, flag in enumerate(flags[:-1]):
                if flag == "--drop-frames":
                    start, stop = map(int, flags[i + 1].split(":"))
                    first, last = int(start * scale), int(stop * scale)
                    flags[i + 1] = f"{first}:{last}" if first < last else None
            flags = [flag for i, flag in enumerate(flags)
                     if flag is not None and (flag != "--drop-frames" or flags[i + 1] is not None)]
            run = export_run(stage / preset["id"], preset["id"], flags, frames, fps)
            runs.append(dict(run, label=preset["label"]))
        write_json(stage / "manifest.json", dict(manifest, runs=runs))
        stage.rename(VIDEO_LIBRARY / identifier)
    return {"id": identifier, "label": label}
