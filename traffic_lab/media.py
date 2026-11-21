"""Video metadata, YOLO detections, and detection-cache I/O."""

import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

from .tracker import Detection


def read_video_info(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-of", "json",
         str(path)],
        check=True, capture_output=True, text=True,
    )
    streams = json.loads(result.stdout)["streams"]
    if not streams:
        raise ValueError("The input has no video stream.")
    stream = streams[0]
    width, height = stream["width"], stream["height"]
    for item in stream.get("side_data_list", []):
        if abs(item.get("rotation", 0)) % 180 == 90:
            width, height = height, width
            break
    for name in ("avg_frame_rate", "r_frame_rate"):
        rate = stream.get(name, "0/0")
        if rate not in ("0/0", "N/A") and Fraction(rate) > 0:
            count = stream.get("nb_frames")
            if str(count).isdigit():
                count = int(count)
            else:
                try:
                    count = round(Fraction(stream["duration"]) * Fraction(rate))
                except (KeyError, ValueError, ZeroDivisionError):
                    count = None
            return width, height, rate, count
    raise ValueError("Could not determine the video frame rate.")


def count_video_frames(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    count = result.stdout.strip()
    if not count.isdigit() or int(count) == 0:
        raise ValueError("Could not count source frames for the seeded outage schedule.")
    return int(count)


def source_timestamps(video):
    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(video),
    ])
    timestamps = [float(frame["best_effort_timestamp_time"]) for frame in json.loads(probe)["frames"]]
    if not timestamps or any(a >= b for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("Expected strictly increasing source-frame timestamps.")
    return [round(value - timestamps[0], 6) for value in timestamps]


def detect(model, rgb_frame, settings):
    result = model.predict(
        source=np.ascontiguousarray(rgb_frame[:, :, ::-1]),  # RGB to detector's BGR.
        conf=settings["confidence"], classes=settings["classes"], verbose=False,
    )[0]
    boxes = result.boxes
    detections = []
    for box, score, class_id in zip(
        boxes.xyxy.cpu().tolist(), boxes.conf.cpu().tolist(), boxes.cls.cpu().tolist()
    ):
        if box[2] > box[0] and box[3] > box[1]:
            detections.append(Detection(tuple(box), int(class_id), float(score)))
    return detections


def detector_settings(args, defaults=None):
    if defaults is None:
        local_weights = Path("inputs/yolo26n.pt")
        weights = local_weights.as_posix() if local_weights.is_file() else "yolo26n.pt"
        defaults = {"weights": weights, "confidence": 0.25, "classes": None}
    if args is None:
        return {key: defaults[key] for key in ("weights", "confidence", "classes")}
    return {
        "weights": args.weights if args.weights is not None else defaults["weights"],
        "confidence": args.confidence if args.confidence is not None else defaults["confidence"],
        "classes": sorted(set(args.classes)) if args.classes is not None else defaults["classes"],
    }


def read_cache(path, video_sha256, args=None):
    cache = json.loads(path.read_text(encoding="utf-8"))
    identity = cache.get("identity")
    if (not isinstance(identity, dict) or identity.get("version") != 1
            or identity.get("video_sha256") != video_sha256
            or not isinstance(cache.get("frames"), list)):
        raise ValueError("Detection cache is incompatible with this video or detector configuration.")
    saved_detector = identity.get("detector")
    if not isinstance(saved_detector, dict):
        raise ValueError("Detection cache is missing detector settings.")
    settings = detector_settings(args, saved_detector)
    if settings != saved_detector:
        raise ValueError("Detection cache is incompatible with the explicit detector options.")
    for index, record in enumerate(cache["frames"]):
        if record["frame"] != index:
            raise ValueError(f"Detection cache is missing or out of order at frame {index}.")
        for row in record["detections"]:
            left, top, right, bottom = row["box"]
            if not np.isfinite(row["box"]).all() or right <= left or bottom <= top:
                raise ValueError(f"Invalid cached box at frame {index}.")
    return cache["frames"], settings


def cached_detections(rows):
    return [Detection(tuple(row["box"]), row["class_id"], row["confidence"]) for row in rows]
