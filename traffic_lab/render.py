"""Offline bounding-box tracking demo for the Kalman filter seminar."""

import argparse
import hashlib
import json
import math
import random
import shutil
import subprocess
import tempfile
from contextlib import ExitStack, suppress
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

import numpy as np

from .media import (
    cached_detections, count_video_frames, detect, detector_settings, read_cache, read_video_info,
)
from .overlay import COLORS, PresentationOverlay, draw_frame
from .scenarios import effective_detections, frame_range, resolve_dropouts, scenario_parser
from .tracker import Tracker


def render(args, staged_output, settings, cached_frames, staged_frames=None):
    width, height, fps, frame_count = read_video_info(args.input)
    total = len(cached_frames) if cached_frames is not None else frame_count
    if args.outage_seed is not None and cached_frames is None:
        # Cache length is exact. Container metadata may only estimate frame count.
        total = count_video_frames(args.input)
    dropouts, schedule = resolve_dropouts(args, total, fps)
    sensor_rng = random.Random(args.sensor_noise_seed) if args.sensor_noise_std else None
    if schedule is not None and sensor_rng is not None:
        schedule["sensor_noise"] = {
            "distribution": "Bernoulli-gated Gaussian box-center translation",
            "probability_per_available_detection": args.sensor_noise_probability,
            "std_fraction_of_box_dimensions": args.sensor_noise_std,
            "seed": args.sensor_noise_seed,
        }
    overlay = None
    if args.presentation:
        span = args.clip_frames or (range(total) if total else None)
        overlay = PresentationOverlay(width, span, dropouts, COLORS)
    output_height = height + (overlay.height if overlay else 0)
    hold_frames = round(args.recovery_hold * float(Fraction(fps)))
    model = None
    if cached_frames is None:
        from ultralytics import YOLO

        model = YOLO(settings["weights"])
    tracker = Tracker(args.min_iou, args.max_misses)
    decoder = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(args.input), "-map", "0:v:0",
               "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    encoder = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width}x{output_height}", "-framerate", fps, "-i", "pipe:0", "-an",
               "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(staged_output)]
    records = []
    count = 0
    rendered = 0
    expected_frames = len(args.clip_frames) if args.clip_frames is not None else total
    progress_interval = max(1, math.ceil(expected_frames / 10)) if expected_frames else 60
    previous_withheld = False
    frame_bytes = width * height * 3
    with subprocess.Popen(decoder, stdout=subprocess.PIPE) as source:
        try:
            with subprocess.Popen(encoder, stdin=subprocess.PIPE) as output:
                try:
                    while raw := source.stdout.read(frame_bytes):
                        if len(raw) != frame_bytes:
                            raise ValueError("Incomplete decoded frame.")
                        frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
                        if cached_frames is None:
                            detections = detect(model, frame, settings)
                            records.append({"frame": count, "detections": [asdict(d) for d in detections]})
                        else:
                            if count >= len(cached_frames):
                                raise ValueError("Detection cache ended before the video.")
                            detections = cached_detections(cached_frames[count]["detections"])
                        withheld = any(count in dropout for dropout in dropouts)
                        recovered = previous_withheld and not withheld
                        previous_withheld = withheld
                        detections = effective_detections(
                            detections, withheld, sensor_rng,
                            args.sensor_noise_probability, args.sensor_noise_std,
                        )
                        tracks = tracker.step(detections, withheld=withheld)
                        if args.clip_frames is None or count in args.clip_frames:
                            annotated = draw_frame(frame, tracks, count, withheld,
                                                   show_header=overlay is None)
                            if overlay is not None:
                                annotated = overlay.add_to(annotated, count, withheld)
                            if staged_frames is not None:
                                annotated.save(staged_frames / f"frame_{count:06d}.png", compress_level=3)
                            pixels = annotated.tobytes()
                            repeats = args.slowdown + (hold_frames if recovered else 0)
                            for _ in range(repeats):
                                output.stdin.write(pixels)
                            rendered += 1
                            if args.progress and (rendered % progress_interval == 0
                                                  or rendered == expected_frames):
                                progress = f"{rendered}/{expected_frames}" if expected_frames else str(rendered)
                                print(f"[{args.output.stem}] {progress} frames", flush=True)
                        count += 1
                    output.stdin.close()
                    if output.wait() != 0 or source.wait() != 0:
                        raise ValueError("FFmpeg could not finish decoding or encoding the video.")
                finally:
                    with suppress(BrokenPipeError):
                        output.stdin.close()
        finally:
            source.stdout.close()
    if count == 0 or (cached_frames is not None and count != len(cached_frames)):
        raise ValueError("Video is empty or its frame count differs from the cache.")
    if schedule is not None and count != schedule["frame_count"]:
        raise ValueError("The decoded frame count differs from the seeded outage schedule.")
    if args.clip_frames is not None and rendered != len(args.clip_frames):
        raise ValueError("--clip-frames extends beyond the source video.")
    return rendered, records, schedule


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__, parents=[scenario_parser()])
    parser.add_argument("input", type=Path, help="Source video")
    parser.add_argument("output", type=Path, help="Output MP4")
    parser.add_argument("--cache", type=Path, help="Detection JSON, default: OUTPUT.detections.json")
    parser.add_argument("--frames-dir", type=Path, help="New folder for zero-based annotated PNGs")
    parser.add_argument("--weights", help="Ultralytics detection model (new run: yolo26n.pt)")
    parser.add_argument("--classes", type=int, nargs="+", help="Optional detector class IDs")
    parser.add_argument("--confidence", type=float, help="Detection threshold (new run: 0.25)")
    parser.add_argument("--presentation", action="store_true", help="Add an outage header and timeline")
    parser.add_argument("--clip-frames", type=frame_range, help="Export START:END, end excluded")
    parser.add_argument("--slowdown", type=int, default=1, help="Playback slowdown factor (default: 1)")
    parser.add_argument("--recovery-hold", type=float, default=0,
                        help="Seconds to hold the first frame after each gap")
    parser.add_argument("--progress", action="store_true", help="Print periodic render progress")
    return parser


def main():
    parser = make_parser()
    args = parser.parse_args()
    cache = args.cache or args.output.with_suffix(".detections.json")
    schedule_path = args.output.with_suffix(".schedule.json") if args.outage_seed is not None else None
    paths = [args.input, args.output, cache]
    if schedule_path is not None:
        paths.append(schedule_path)
    if args.output.suffix.lower() != ".mp4" or len({p.resolve() for p in paths}) != len(paths):
        parser.error("Use distinct paths for the input, output MP4, detection cache, and schedule.")
    if args.frames_dir is not None:
        frames_path = args.frames_dir.resolve()
        if args.frames_dir.exists() or args.frames_dir.is_symlink():
            parser.error("--frames-dir must be a new folder.")
        if any(p.resolve().is_relative_to(frames_path) for p in paths):
            parser.error("Keep the input, output MP4, cache, and schedule outside --frames-dir.")
    if not (0 < args.min_iou <= 1 and args.max_misses >= 0
            and (args.confidence is None or 0 <= args.confidence <= 1)):
        parser.error("Use 0 < min IoU <= 1, 0 <= confidence <= 1, and nonnegative max misses.")
    if args.slowdown < 1 or not math.isfinite(args.recovery_hold) or args.recovery_hold < 0:
        parser.error("Use a positive slowdown factor and nonnegative recovery hold.")
    if (not math.isfinite(args.sensor_noise_std) or args.sensor_noise_std < 0
            or not 0 <= args.sensor_noise_probability <= 1):
        parser.error("Use a finite nonnegative sensor-noise scale and a probability in [0, 1].")
    if any(shutil.which(tool) is None for tool in ("ffmpeg", "ffprobe")):
        parser.error("FFmpeg and ffprobe must be available on PATH.")

    try:
        with args.input.open("rb") as video:
            digest = hashlib.file_digest(video, "sha256").hexdigest()
        if cache.exists():
            cached_frames, settings = read_cache(cache, digest, args)
        else:
            cached_frames, settings = None, detector_settings(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with ExitStack() as staging:
            directory = staging.enter_context(tempfile.TemporaryDirectory(dir=args.output.parent))
            staged = Path(directory) / "render.mp4"
            staged_frames = None
            if args.frames_dir is not None:
                args.frames_dir.parent.mkdir(parents=True, exist_ok=True)
                staged_frames = Path(staging.enter_context(
                    tempfile.TemporaryDirectory(dir=args.frames_dir.parent)
                )) / "frames"
                staged_frames.mkdir()
            count, records, schedule = render(args, staged, settings, cached_frames, staged_frames)
            if cached_frames is None:
                identity = {"version": 1, "video_sha256": digest, "detector": settings}
                cache.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=cache.parent) as cache_dir:
                    staged_cache = Path(cache_dir) / "detections.json"
                    staged_cache.write_text(
                        json.dumps({"identity": identity, "frames": records}), encoding="utf-8"
                    )
                    staged_cache.replace(cache)
            if schedule is not None:
                schedule.update(source=args.input.name, source_sha256=digest)
                staged_schedule = Path(directory) / "schedule.json"
                staged_schedule.write_text(json.dumps(schedule, indent=2) + "\n", encoding="utf-8")
                staged_schedule.replace(schedule_path)
            staged.replace(args.output)
            if staged_frames is not None:
                staged_frames.rename(args.frames_dir)
        print(f"Rendered {count} source frames to {args.output}. Detections: {cache}")
        if args.frames_dir is not None:
            print(f"Saved {count} annotated PNGs to {args.frames_dir}")
        if schedule is not None:
            print(f"Withheld {schedule['withheld_frames']} frames across {schedule['outages']} "
                  f"outages. Schedule: {schedule_path}")
    except (OSError, ValueError, KeyError, ImportError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Demo failed: {error}\n")


if __name__ == "__main__":
    main()
