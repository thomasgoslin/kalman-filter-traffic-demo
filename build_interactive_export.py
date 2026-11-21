"""Build the browser demo from the original video and the saved YOLO cache."""

import json
import random
import shutil
import subprocess
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from kalman_filter_demo import (
    F, H, P0, Q, R, Detection, Tracker, effective_detections, make_parser,
    read_cache, read_video_info, seeded_outages,
)
from rerender_all import CACHE, OUTPUTS, RENDERER, ROOT, RUNS, VIDEO, digest, replace_outputs


LABELS = ["Baseline", "Short outages", "Short outages + noise", "Long outages"]


def write_json(path, data):
    path.write_text(json.dumps(data, allow_nan=False, separators=(",", ":")), encoding="utf-8")


def source_timestamps(video):
    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(video),
    ])
    timestamps = [float(frame["best_effort_timestamp_time"]) for frame in json.loads(probe)["frames"]]
    if not timestamps or any(a >= b for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("Expected strictly increasing source-frame timestamps.")
    return [round(value - timestamps[0], 6) for value in timestamps]


def export_run(folder, name, flags, frames, fps):
    args = make_parser().parse_args([str(VIDEO), f"{name}.mp4", *flags])
    dropouts = args.drop_frames
    if args.outage_seed is not None:
        dropouts, _ = seeded_outages(len(frames), fps, args.outage_seed)
    withheld_frames = {index for gap in dropouts for index in gap}
    rng = random.Random(args.sensor_noise_seed) if args.sensor_noise_std else None
    tracker = Tracker(args.min_iou, args.max_misses)
    geometry = []
    histories = {}
    for frame in frames:
        index = frame["frame"]
        raw = frame["detections"]
        detections = [Detection(tuple(d["box"]), d["class_id"], d["confidence"]) for d in raw]
        withheld = index in withheld_frames
        effective = effective_detections(
            detections, withheld, rng, args.sensor_noise_probability, args.sensor_noise_std,
        )
        telemetry = []
        visible = tracker.step(effective, withheld=withheld, telemetry=telemetry)
        reports = {record["id"]: record for record in telemetry}
        geometry.append({
            "withheld": withheld,
            "tracks": [
                dict(asdict(track), status=reports[track.id]["status"])
                for track in visible
            ],
        })
        for record in telemetry:
            matched = record["detection_index"]
            record.update(frame=index, withheld=withheld)
            if matched is not None:
                record.update(raw_box=raw[matched]["box"], effective_box=effective[matched].box)
            histories.setdefault(record["id"], []).append(record)

    tracks = folder / "tracks"
    tracks.mkdir(parents=True)
    index = []
    for track_id, history in histories.items():
        expired = history[-1]["status"] == "expired"
        index.append({
            "id": track_id, "first": history[0]["frame"],
            "last": history[-2 if expired else -1]["frame"],
            "expired": history[-1]["frame"] if expired else None,
        })
        write_json(tracks / f"{track_id}.json", {"id": track_id, "run": name, "frames": history})
    write_json(folder / "tracks-index.json", index)
    write_json(folder / "frames.json", geometry)
    return {
        "id": name, "flags": flags, "outages": [[gap.start, gap.stop] for gap in dropouts],
        "min_iou": args.min_iou, "max_misses": args.max_misses,
    }


def main():
    video, cache = ROOT / VIDEO, ROOT / CACHE
    source_hash = digest(VIDEO)
    frames, _ = read_cache(cache, source_hash, make_parser().parse_args([str(video), "unused.mp4"]))
    width, height, fps, _ = read_video_info(video)
    timestamps = source_timestamps(video)
    if len(timestamps) != len(frames):
        raise ValueError("Source frame count differs from the detection cache.")
    with TemporaryDirectory(prefix=".interactive-", dir=ROOT / OUTPUTS) as temporary:
        workspace = Path(temporary)
        stage = workspace / "new"
        interactive = stage / "interactive"
        shutil.copytree(ROOT / "interactive_web", interactive)
        shutil.copyfile(video, interactive / "source.mp4")
        runs = []
        for (name, flags), label in zip(RUNS.items(), LABELS, strict=True):
            print(f"Exporting {label}...", flush=True)
            runs.append(dict(export_run(interactive / name, name, flags, frames, fps), label=label))
        write_json(interactive / "manifest.json", {
            "version": 1, "video": "source.mp4", "width": width, "height": height,
            "fps": str(Fraction(fps)), "timestamps": timestamps, "frame_count": len(frames),
            "source_sha256": source_hash, "cache_sha256": digest(CACHE),
            "renderer_sha256": digest(RENDERER), "runs": runs,
            "model": {name: matrix.tolist() for name, matrix in
                      {"F": F, "H": H, "Q": Q, "R": R, "P0": P0}.items()},
        })
        replace_outputs(stage, workspace / "previous")
    print("Interactive demo ready in outputs/interactive.", flush=True)


if __name__ == "__main__":
    main()
