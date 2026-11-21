"""Validate Settings input and run private, reproducible tracking experiments."""

import hashlib
import json
import math
import re
import shutil
import subprocess
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
from scipy.stats import norm

from .export import export_run, write_json
from .media import detect, read_cache
from .scenarios import (
    OUTAGE_MAX_FRAMES, OUTAGE_RECOVERY_PROBABILITY, OUTAGE_START_RATE,
    scenario_parser, seeded_outages,
)
from .tracker import ONLINE_MISS_LIMIT, FilterModel, Tracker
from .workspace import CACHE, OUTPUTS, ROOT, VIDEO, digest


EXPERIMENTS = ROOT / OUTPUTS / "experiments"
DETECTION_CACHES = ROOT / OUTPUTS / "experiment-detections"
VIDEO_LIBRARY = ROOT / OUTPUTS / "videos"
MATRIX_SHAPES = {"F": (6, 6), "H": (4, 6), "Q": (6, 6), "R": (4, 4), "P0": (6, 6),
                 "b": (6,), "initial_velocity": (2,)}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def number(value, label, low, high, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f"{label} must be between {low} and {high}.")
    if integer and int(value) != value:
        raise ValueError(f"{label} must be a whole number.")
    return int(value) if integer else float(value)


def matrix(value, name, shape):
    try:
        result = np.asarray(value)
        if result.shape != shape or result.dtype.kind not in "fiu":
            raise ValueError
        result = result.astype(float)
        if not np.isfinite(result).all() or np.abs(result).max() > 1e6:
            raise ValueError
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must have shape {' × '.join(map(str, shape))} and finite entries.") from error
    if name in {"Q", "R", "P0"}:
        if not np.allclose(result, result.T, rtol=0, atol=1e-10):
            raise ValueError(f"{name} must be symmetric.")
        result = (result + result.T) / 2
        eig_min = np.linalg.eigvalsh(result)[0]
        if name == "R" and eig_min <= 0:
            raise ValueError("R must be positive definite.")
        if name != "R" and eig_min < -1e-10:
            raise ValueError(f"{name} must be positive semidefinite.")
    return result


class Experiments:
    def __init__(self, source_id="default"):
        self.source_id = source_id
        folder = ROOT / OUTPUTS / "interactive" if source_id == "default" else VIDEO_LIBRARY / source_id
        self.manifest = read_json(folder / "manifest.json")
        self.source = ROOT / VIDEO if source_id == "default" else folder / "source.mp4"
        cache = ROOT / CACHE if source_id == "default" else folder / "detections.json"
        self.source_hash = self.manifest["source_sha256"]
        if source_id == "default" and digest(self.source) != self.source_hash:
            raise ValueError("The preview uses a different video. Run python -m traffic_lab.export first.")
        self.frames, detector = read_cache(cache, self.source_hash)
        self.detector = dict(detector, weights=Path(detector["weights"]).name)

    def weights(self):
        choices = {}
        for directory in (ROOT, ROOT / "inputs"):
            for path in directory.glob("*.pt"):
                if path.is_file() and path.resolve().parent == directory:
                    choices[path.name] = path
        return choices

    def presets(self):
        presets = []
        for run in self.manifest["runs"]:
            args = scenario_parser().parse_args(run["flags"])
            schedule = run.get("schedule") or {}
            wait = schedule.get("online_wait", {})
            length = schedule.get("outage_length", {})
            presets.append({
                "id": run["id"], "label": run["label"],
                "settings": {
                    "label": run["label"],
                    "model": dict(deepcopy(self.manifest["model"]), b=[0]*6, initial_velocity=[0, 0]),
                    "detector": deepcopy(self.detector),
                    "association": {
                        "min_iou": args.min_iou, "max_misses": args.max_misses,
                        "online_misses": self.manifest.get("online_miss_limit", ONLINE_MISS_LIMIT),
                        "retire_unmatched": True,
                    },
                    "outage": {
                        "mode": "seeded" if args.outage_seed is not None else "manual" if args.drop_frames else "none",
                        "seed": args.outage_seed if args.outage_seed is not None else 2026,
                        "start_rate": wait.get("exponential_rate_per_online_second", OUTAGE_START_RATE),
                        "recovery_probability": length.get("recovery_probability_per_frame", OUTAGE_RECOVERY_PROBABILITY),
                        "max_frames": length.get("max_frames", OUTAGE_MAX_FRAMES),
                        "ranges": [[gap.start, gap.stop] for gap in args.drop_frames],
                    },
                    "noise": {
                        "seed": args.sensor_noise_seed, "probability": args.sensor_noise_probability,
                        "std_fraction": args.sensor_noise_std,
                    },
                    "coverage": 95,
                },
            })
        return presets

    def runs_for_video_hash(self):
        for path in EXPERIMENTS.glob("*/run.json"):
            if re.fullmatch(r"[a-f0-9]{32}", path.parent.name):
                try:
                    run = read_json(path)
                except FileNotFoundError:
                    continue
                if run.get("source_sha256") == self.source_hash:
                    yield run

    def saved_runs(self):
        return sorted((run for run in self.runs_for_video_hash() if self.owns(run)),
                      key=lambda item: item["created"])

    def unshared_detection_caches(self, run):
        detector = run["settings"]["detector"]
        # The same encoded video can appear under more than one source ID.
        if any(other["id"] != run["id"] and other["settings"]["detector"] == detector
               for other in self.runs_for_video_hash()):
            return
        for path in DETECTION_CACHES.glob("*.json"):
            if not re.fullmatch(r"[a-f0-9]{64}", path.stem) or path.is_symlink():
                continue
            try:
                identity = read_json(path).get("identity", {})
            except (FileNotFoundError, json.JSONDecodeError):
                continue
            if identity.get("video_sha256") == self.source_hash and identity.get("detector") == detector:
                yield path

    def ensure_unique_name(self, label):
        name = label.strip().casefold()
        if any(run["label"].strip().casefold() == name for run in self.saved_runs()):
            raise ValueError("An experiment with that name already exists for this video.")

    def owns(self, run):
        return (run.get("source_id", "default") == self.source_id
                and run.get("source_sha256") == self.source_hash)

    def delete(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            return False
        folder = EXPERIMENTS / identifier
        if folder.is_symlink():
            return False
        try:
            run = read_json(folder / "run.json")
        except FileNotFoundError:
            return False
        if not self.owns(run):
            return False

        caches = list(self.unshared_detection_caches(run))
        deleted = folder.with_name("." + identifier + "-deleted")
        folder.rename(deleted)
        moved = []
        try:
            for path in caches:
                path.rename(deleted / path.name)
                moved.append(path)
        except OSError:
            for path in moved:
                (deleted / path.name).rename(path)
            deleted.rename(folder)
            raise
        shutil.rmtree(deleted)
        return True

    def validate(self, raw):
        label = raw["label"]
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 64:
            raise ValueError("Name the experiment using 1–64 characters.")
        model = {name: matrix(raw["model"][name], name, shape).tolist() for name, shape in MATRIX_SHAPES.items()}

        detector = raw["detector"]
        weights = detector["weights"]
        if not isinstance(weights, str) or weights not in self.weights().keys() | {self.detector["weights"]}:
            raise ValueError("Choose the cached detector or a weights file from inputs/.")
        classes = detector["classes"]
        if classes is not None:
            if not isinstance(classes, list) or not classes or len(classes) > 100:
                raise ValueError("Enter up to 100 class IDs, or leave the field blank for all classes.")
            classes = sorted({number(item, "Class ID", 0, 999, integer=True) for item in classes})
        detector = {
            "weights": weights, "classes": classes,
            "confidence": number(detector["confidence"], "Detector confidence", 0, 1),
        }

        association = raw["association"]
        if not isinstance(association["retire_unmatched"], bool):
            raise ValueError("Choose whether to retire unmatched tracks after recovery.")
        association = {
            "min_iou": number(association["min_iou"], "Matching IoU", 0.0001, 1),
            "max_misses": number(association["max_misses"], "Outage misses", 0, 10000, integer=True),
            "online_misses": number(association["online_misses"], "Online misses", 0, 10000, integer=True),
            "retire_unmatched": association["retire_unmatched"],
        }

        outage = raw["outage"]
        mode = outage["mode"]
        if mode not in ("none", "seeded", "manual"):
            raise ValueError("Choose an outage mode.")
        ranges = []
        if mode == "manual":
            for pair in outage["ranges"]:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("Enter manual outages as start:end ranges.")
                start, end = [number(item, "Outage boundary", 0, 10000000, integer=True) for item in pair]
                if end <= start:
                    raise ValueError("Each outage end must be greater than its start.")
                if start < len(self.frames):
                    ranges.append([start, min(end, len(self.frames))])
            ranges.sort()
            if any(previous[1] > following[0] for previous, following in zip(ranges, ranges[1:])):
                raise ValueError("Manual outages must not overlap.")
        outage = {
            "mode": mode, "ranges": ranges,
            "seed": number(outage["seed"], "Outage seed", -2**31, 2**31-1, integer=True),
            "start_rate": number(outage["start_rate"], "Outage start rate", 0, 1000),
            "recovery_probability": number(outage["recovery_probability"], "Recovery probability", 0, 1),
            "max_frames": number(outage["max_frames"], "Maximum outage duration", 1, 10000, integer=True),
        }
        noise = raw["noise"]
        return {
            "label": label.strip(), "model": model, "detector": detector,
            "association": association, "outage": outage,
            "noise": {
                "seed": number(noise["seed"], "Measurement noise seed", -2**31, 2**31-1, integer=True),
                "probability": number(noise["probability"], "Measurement noise probability", 0, 1),
                "std_fraction": number(noise["std_fraction"], "Measurement noise scale", 0, 4),
            },
            "coverage": number(raw["coverage"], "Display probability", 1, 99.99),
        }

    def detector_frames(self, settings, report):
        if settings == self.detector:
            report("Reusing the original detection cache.")
            return self.frames
        weights = self.weights().get(settings["weights"])
        if weights is None:
            raise ValueError("Put the selected detector weights in inputs/ to run fresh detections.")
        fingerprint = json.dumps([self.source_hash, digest(weights), settings], sort_keys=True).encode()
        DETECTION_CACHES.mkdir(parents=True, exist_ok=True)
        cache = DETECTION_CACHES / (hashlib.sha256(fingerprint).hexdigest() + ".json")
        if cache.exists():
            report("Reusing the matching experiment detection cache.")
            frames, _ = read_cache(cache, self.source_hash)
            return frames
        report("Loading detector weights…")
        frames = run_detector(self.source, self.manifest, weights, settings, report)
        with TemporaryDirectory(dir=DETECTION_CACHES) as temporary:
            staged = Path(temporary) / "detections.json"
            write_json(staged, {
                "identity": {"version": 1, "video_sha256": self.source_hash, "detector": settings},
                "frames": frames,
            })
            staged.replace(cache)
        return frames

    def run(self, identifier, settings, report):
        if digest(self.source) != self.source_hash:
            raise ValueError("The source video changed. Rebuild the browser export and restart the preview.")
        frames = self.detector_frames(settings["detector"], report)
        report("Running the Kalman filter…")
        outage, noise, association = (settings[key] for key in ("outage", "noise", "association"))
        fps = self.manifest["fps"]
        if outage["mode"] == "seeded":
            dropouts, schedule = seeded_outages(
                len(frames), fps, outage["seed"], start_rate=outage["start_rate"],
                recovery_probability=outage["recovery_probability"], max_frames=outage["max_frames"],
            )
        else:
            dropouts = [range(start, end) for start, end in outage["ranges"]]
            schedule = None
        model = FilterModel(**{name: np.asarray(value) for name, value in settings["model"].items()})
        tracker = Tracker(**association, model=model)
        args = SimpleNamespace(
            min_iou=tracker.min_iou, max_misses=tracker.max_misses,
            sensor_noise_std=noise["std_fraction"], sensor_noise_probability=noise["probability"],
            sensor_noise_seed=noise["seed"],
        )
        EXPERIMENTS.mkdir(parents=True, exist_ok=True)
        try:
            with TemporaryDirectory(prefix=".experiment-", dir=EXPERIMENTS) as temporary:
                folder = Path(temporary) / "run"
                with np.errstate(over="raise", invalid="raise", divide="raise"):
                    run = export_run(folder, identifier, [], frames, fps, setup=(args, dropouts, schedule, tracker))
                probability = settings["coverage"] / 100
                run.update(
                    label=settings["label"], settings=settings, source_sha256=self.source_hash,
                    source_id=self.source_id,
                    created=time.time(), coverage={
                        "percent": settings["coverage"], "normal": float(norm.ppf((1 + probability) / 2)),
                        "chi2": -2 * math.log1p(-probability),
                    },
                )
                write_json(folder / "run.json", run)
                folder.rename(EXPERIMENTS / identifier)
        except (FloatingPointError, np.linalg.LinAlgError) as error:
            raise ValueError("The filter became numerically unstable. Check F, H, and the covariances.") from error
        return run


def run_detector(source, manifest, weights, settings, report):
    from ultralytics import YOLO

    model = YOLO(str(weights))
    width, height = manifest["width"], manifest["height"]
    frame_bytes = width * height * 3
    command = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(source),
               "-map", "0:v:0", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    frames = []
    with subprocess.Popen(command, stdout=subprocess.PIPE) as decoder:
        try:
            while raw := decoder.stdout.read(frame_bytes):
                if len(raw) != frame_bytes:
                    raise ValueError("Could not decode a complete source frame.")
                rgb = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
                boxes = detect(model, rgb, settings)
                index = len(frames)
                frames.append({"frame": index, "detections": [asdict(box) for box in boxes]})
                if index % 12 == 0:
                    report(f"Running detector: {index + 1} / {manifest['frame_count']} frames.")
            if decoder.wait() != 0 or len(frames) != manifest["frame_count"]:
                raise ValueError("Fresh detections did not match the source frame count.")
        finally:
            if decoder.poll() is None:
                decoder.terminate()
    return frames
