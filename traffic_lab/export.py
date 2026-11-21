"""Build the interactive browser demo from the saved detection cache."""

import json
import random
import shutil
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from .media import cached_detections, read_cache, read_video_info, source_timestamps
from .scenarios import RUNS, effective_detections, resolve_dropouts, scenario_parser
from .tracker import F, H, ONLINE_MISS_LIMIT, P0, Q, R, Tracker
from .workspace import CACHE, OUTPUTS, RENDERER, ROOT, VIDEO, digest, pipeline_hashes, replace_outputs


def write_json(path, data):
    path.write_text(json.dumps(data, allow_nan=False, separators=(",", ":")), encoding="utf-8")


def export_run(folder, name, flags, frames, fps):
    args = scenario_parser().parse_args(flags)
    dropouts, schedule = resolve_dropouts(args, len(frames), fps)
    withheld_frames = {index for gap in dropouts for index in gap}
    rng = random.Random(args.sensor_noise_seed) if args.sensor_noise_std else None
    tracker = Tracker(args.min_iou, args.max_misses)
    geometry = []
    histories = {}
    for frame in frames:
        index = frame["frame"]
        raw = frame["detections"]
        detections = cached_detections(raw)
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
        "schedule": schedule,
        "sensor_noise": {
            "probability": args.sensor_noise_probability,
            "std_fraction": args.sensor_noise_std,
            "seed": args.sensor_noise_seed,
        } if args.sensor_noise_std else None,
    }


def main():
    video, cache = ROOT / VIDEO, ROOT / CACHE
    source_hash = digest(VIDEO)
    frames, _ = read_cache(cache, source_hash)
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
        for name, (label, flags) in RUNS.items():
            print(f"Exporting {label}...", flush=True)
            runs.append(dict(export_run(interactive / name, name, flags, frames, fps), label=label))
        write_json(interactive / "manifest.json", {
            "version": 1, "video": "source.mp4", "width": width, "height": height,
            "fps": str(Fraction(fps)), "timestamps": timestamps, "frame_count": len(frames),
            "source_sha256": source_hash, "cache_sha256": digest(CACHE),
            "renderer_sha256": digest(RENDERER), "pipeline_sha256": pipeline_hashes(), "runs": runs,
            "online_miss_limit": ONLINE_MISS_LIMIT,
            "model": {name: matrix.tolist() for name, matrix in
                      {"F": F, "H": H, "Q": Q, "R": R, "P0": P0}.items()},
        })
        replace_outputs(stage, workspace / "previous")
    print("Interactive demo ready in outputs/interactive.", flush=True)


if __name__ == "__main__":
    main()
