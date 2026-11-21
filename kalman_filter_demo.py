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
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment


# State order: center x, center y, width, height, velocity x, velocity y.
F = np.eye(6)
F[0, 4] = F[1, 5] = 1.0
H = np.eye(6)[:4]
Q = np.diag([2, 2, 1, 1, 1, 1])
R = np.diag([25, 25, 36, 36])
P0 = np.diag([25, 25, 36, 36, 100, 100])

# Simulated detector availability. The filter and the saved boxes are unaffected.
OUTAGE_START_RATE = 1.5  # Starts per second while the detector is online.
OUTAGE_RECOVERY_PROBABILITY = 0.35  # Per missing frame, before the duration cap.
OUTAGE_MAX_FRAMES = 10

ONLINE_MISS_LIMIT = 8

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    box: Box  # Left, top, right, bottom in original-frame pixels.
    class_id: int
    confidence: float


@dataclass
class Track:
    id: int
    class_id: int
    mean: np.ndarray
    covariance: np.ndarray
    misses: int = 0


@dataclass(frozen=True)
class FrameTrack:
    id: int
    class_id: int
    forecast: Box | None = None
    detection: Box | None = None
    corrected: Box | None = None


def to_measurement(box: Box) -> np.ndarray:
    left, top, right, bottom = box
    return np.array([(left + right) / 2, (top + bottom) / 2, right - left, bottom - top])


def to_box(measurement: np.ndarray) -> Box:
    x, y, width, height = map(float, measurement)
    # Keep rectangles drawable even if a noisy estimate gives an invalid size.
    half_width, half_height = max(1.0, width) / 2, max(1.0, height) / 2
    return x - half_width, y - half_height, x + half_width, y + half_height


def intersection_over_union(a: Box, b: Box) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = width * height
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection)


def correct(mean, covariance, measurement, observation_matrix, measurement_noise, *, diagnostics=None):
    """Condition the predicted state on one measurement."""
    cross_covariance = covariance @ observation_matrix.T
    innovation_covariance = observation_matrix @ cross_covariance + measurement_noise
    gain = np.linalg.solve(innovation_covariance, cross_covariance.T).T
    innovation = measurement - observation_matrix @ mean
    if diagnostics is not None:
        diagnostics.update(
            z=measurement.tolist(), innovation=innovation.tolist(),
            S=innovation_covariance.tolist(), K=gain.tolist(),
            nis=float(innovation @ np.linalg.solve(innovation_covariance, innovation)),
        )
    residual_map = np.eye(len(mean)) - gain @ observation_matrix
    # Joseph form keeps both sources of posterior uncertainty explicit.
    return (
        mean + gain @ innovation,
        residual_map @ covariance @ residual_map.T + gain @ measurement_noise @ gain.T,
    )


class Tracker:
    def __init__(self, min_iou: float = 0.1, max_misses: int = ONLINE_MISS_LIMIT):
        self.min_iou = min_iou
        self.max_misses = max_misses
        self.tracks: list[Track] = []
        self.next_id = 1
        self._awaiting_measurements = False

    def _assign(self, forecasts: list[Box], detections: list[Detection]) -> dict[int, int]:
        if not forecasts or not detections:
            return {}
        # A rejected pair costs more than all permitted matches combined.
        penalty = min(len(forecasts), len(detections)) + 1
        costs = np.full((len(forecasts), len(detections)), penalty, dtype=float)
        for i, (track, box) in enumerate(zip(self.tracks, forecasts)):
            for j, detection in enumerate(detections):
                if track.class_id == detection.class_id:
                    overlap = intersection_over_union(box, detection.box)
                    if overlap >= self.min_iou:
                        costs[i, j] = 1 - overlap
        rows, columns = linear_sum_assignment(costs)
        return {int(i): int(j) for i, j in zip(rows, columns) if costs[i, j] < penalty}

    def step(self, detections: list[Detection], *, withheld=False, telemetry=None) -> list[FrameTrack]:
        """Advance one source frame, optionally appending numerical track records."""
        if withheld:
            self._awaiting_measurements = True
        recovering = self._awaiting_measurements and not withheld and bool(detections)
        if recovering:
            self._awaiting_measurements = False
        forecasts = []
        for track in self.tracks:
            track.mean = F @ track.mean
            track.covariance = F @ track.covariance @ F.T + Q
            forecasts.append(to_box(H @ track.mean))

        miss_limit = self.max_misses if withheld else min(self.max_misses, ONLINE_MISS_LIMIT)
        matches = self._assign(forecasts, detections)
        used_detections = set(matches.values())
        visible = []
        survivors = []
        for i, track in enumerate(self.tracks):
            record = None
            expired = False
            if telemetry is not None:
                record = {"x_pred": track.mean.tolist(), "P_pred": track.covariance.tolist()}
            if i in matches:
                box = detections[matches[i]].box
                track.mean, track.covariance = correct(
                    track.mean, track.covariance, to_measurement(box), H, R, diagnostics=record,
                )
                track.misses = 0
                if record is not None:
                    detection = detections[matches[i]]
                    record.update(
                        status="updated", detection_index=matches[i],
                        detection_confidence=detection.confidence,
                        assignment_iou=intersection_over_union(forecasts[i], box),
                    )
                visible.append(
                    FrameTrack(track.id, track.class_id, forecasts[i], box, to_box(H @ track.mean))
                )
            else:
                track.misses += 1
                expired = recovering or track.misses > miss_limit
                if record is not None:
                    record["status"] = "expired" if expired else "predicted"
                    if expired:
                        record["expiry_reason"] = "unmatched_on_recovery" if recovering else "miss_limit"
                if not expired:
                    visible.append(FrameTrack(track.id, track.class_id, forecasts[i]))
            if record is not None:
                telemetry.append(self._track_record(track, record))
            if not expired:
                survivors.append(track)

        for j, detection in enumerate(detections):
            if j not in used_detections:
                mean = np.r_[to_measurement(detection.box), 0.0, 0.0]
                track = Track(self.next_id, detection.class_id, mean, P0.copy())
                self.next_id += 1
                survivors.append(track)
                visible.append(FrameTrack(track.id, track.class_id, detection=detection.box))
                if telemetry is not None:
                    telemetry.append(self._track_record(track, {
                        "status": "initialized", "detection_index": j,
                        "detection_confidence": detection.confidence,
                        "z": to_measurement(detection.box).tolist(),
                    }))
        self.tracks = survivors
        return visible


    @staticmethod
    def _track_record(track, details):
        return {
            "id": track.id, "class_id": track.class_id, "misses": track.misses,
            "x_current": track.mean.tolist(), "P_current": track.covariance.tolist(),
            "x_pred": None, "P_pred": None, "z": None, "innovation": None,
            "S": None, "K": None, "nis": None, "detection_index": None,
            "detection_confidence": None, "assignment_iou": None,
            **details,
        }


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


def seeded_outages(frame_count, fps, seed):
    """Alternate geometric online waits and short, capped geometric outages."""
    rng = random.Random(seed)
    # The chance an exponential waiting time ends during one frame interval.
    start_probability = -math.expm1(-OUTAGE_START_RATE / float(Fraction(fps)))

    def geometric(probability):
        return 1 + math.floor(math.log1p(-rng.random()) / math.log1p(-probability))

    dropouts = []
    cursor = 0
    while cursor < frame_count:
        start = cursor + geometric(start_probability)
        if start >= frame_count:
            break
        duration = min(geometric(OUTAGE_RECOVERY_PROBABILITY), OUTAGE_MAX_FRAMES)
        cursor = min(frame_count, start + duration)
        dropouts.append(range(start, cursor))

    schedule = {
        "frame_count": frame_count,
        "fps": fps,
        "seed": seed,
        "online_wait": {
            "distribution": "geometric",
            "exponential_rate_per_online_second": OUTAGE_START_RATE,
            "per_frame_dropout_probability": start_probability,
            "minimum_online_frames_between_outages": 1,
        },
        "outage_length": {
            "distribution": "capped geometric",
            "recovery_probability_per_frame": OUTAGE_RECOVERY_PROBABILITY,
            "max_frames": OUTAGE_MAX_FRAMES,
        },
        "outages": len(dropouts),
        "withheld_frames": sum(map(len, dropouts)),
        "ranges_zero_based_end_exclusive": [[d.start, d.stop] for d in dropouts],
    }
    return dropouts, schedule


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


COLORS = {"forecast": "#00D5FF", "detector": "#FFD54F", "update": "#77F477"}


def jitter_detections(detections, rng, probability, std_fraction):
    """Translate selected box centers by independent Gaussian offsets."""
    jittered = []
    for detection in detections:
        if rng.random() < probability:
            left, top, right, bottom = detection.box
            # Scale to the box so the same setting works on near and distant cars.
            dx = rng.gauss(0, std_fraction * (right - left))
            dy = rng.gauss(0, std_fraction * (bottom - top))
            detection = Detection(
                (left + dx, top + dy, right + dx, bottom + dy),
                detection.class_id, detection.confidence,
            )
        jittered.append(detection)
    return jittered


def effective_detections(detections, withheld, sensor_rng, probability, std_fraction):
    if withheld:
        return []
    if sensor_rng is not None:
        return jitter_detections(detections, sensor_rng, probability, std_fraction)
    return detections


def draw_frame(frame, tracks, index, withheld, *, show_header=True):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.fromarray(frame)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(12, min(24, image.width // 65)))

    def label(x, y, text, color="white"):
        draw.text((x, y), text, font=font, fill=color, stroke_width=2, stroke_fill="black")

    if show_header:
        for x, text, kind in (
            (8, "Forecast", "forecast"),
            (image.width // 3, "Detector", "detector"),
            (image.width * 2 // 3, "Kalman update", "update"),
        ):
            label(x, 5, text, COLORS[kind])
        title = f"Frame {index}"
        if withheld:
            title += " | Detection withheld"
        label(8, font.size + 10, title)

    def draw_box(box, color, style):
        if box is None:
            return
        left, top, right, bottom = (round(value) for value in box)
        if right <= 0 or bottom <= 0 or left >= image.width or top >= image.height:
            return
        left, right = max(0, left), min(image.width - 1, right)
        top, bottom = max(0, top), min(image.height - 1, bottom)
        if style == "solid":
            draw.rectangle((left, top, right, bottom), outline=color, width=2)
            return
        for low, high, fixed, horizontal in (
            (left, right, top, True), (left, right, bottom, True),
            (top, bottom, left, False), (top, bottom, right, False),
        ):
            for start in range(low, high, 14):
                if style == "corners" and min(start - low, high - start) > 14:
                    continue
                end = min(start + 8, high)
                line = (start, fixed, end, fixed) if horizontal else (fixed, start, fixed, end)
                draw.line(line, fill=color, width=2)

    for track in tracks:
        draw_box(track.corrected, COLORS["update"], "solid")
        draw_box(track.forecast, COLORS["forecast"], "dashed")
        draw_box(track.detection, COLORS["detector"], "corners")
        if track.corrected is not None:
            box, kind = track.corrected, "update"
        elif track.forecast is not None:
            box, kind = track.forecast, "forecast"
        else:
            box, kind = track.detection, "detector"
        label(max(0, round(box[0])), max(2 * font.size + 16, round(box[1]) - font.size - 4),
              f"ID {track.id}", COLORS[kind])
    return image


def detector_settings(args, defaults=None):
    if defaults is None:
        defaults = {"weights": "yolo26n.pt", "confidence": 0.25, "classes": None}
    return {
        "weights": args.weights if args.weights is not None else defaults["weights"],
        "confidence": args.confidence if args.confidence is not None else defaults["confidence"],
        "classes": sorted(set(args.classes)) if args.classes is not None else defaults["classes"],
    }


def read_cache(path, video_sha256, args):
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


def render(args, staged_output, settings, cached_frames, staged_frames=None):
    width, height, fps, frame_count = read_video_info(args.input)
    total = len(cached_frames) if cached_frames is not None else frame_count
    dropouts, schedule = args.drop_frames, None
    if args.outage_seed is not None:
        # Cache length is exact. Container metadata may only estimate frame count.
        if cached_frames is None:
            total = count_video_frames(args.input)
        dropouts, schedule = seeded_outages(total, fps, args.outage_seed)
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
        from presentation_overlay import PresentationOverlay

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
                            detections = [
                                Detection(tuple(d["box"]), d["class_id"], d["confidence"])
                                for d in cached_frames[count]["detections"]
                            ]
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


def frame_range(value):
    try:
        start, end = map(int, value.split(":"))
        if 0 <= start < end:
            return range(start, end)
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Expected START:END with 0 <= START < END (end excluded).")


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Source video")
    parser.add_argument("output", type=Path, help="Output MP4")
    parser.add_argument("--cache", type=Path, help="Detection JSON, default: OUTPUT.detections.json")
    parser.add_argument("--frames-dir", type=Path, help="New folder for zero-based annotated PNGs")
    parser.add_argument("--weights", help="Ultralytics detection model (new run: yolo26n.pt)")
    parser.add_argument("--classes", type=int, nargs="+", help="Optional detector class IDs")
    parser.add_argument("--confidence", type=float, help="Detection threshold (new run: 0.25)")
    parser.add_argument("--min-iou", type=float, default=0.10)
    parser.add_argument("--max-misses", type=int, default=ONLINE_MISS_LIMIT,
                        help=f"Consecutive missed frames allowed, capped at {ONLINE_MISS_LIMIT} while online")
    outage_mode = parser.add_mutually_exclusive_group()
    outage_mode.add_argument("--drop-frames", type=frame_range, action="append", default=[],
                             help="Withhold detections on START:END, end excluded. Repeat for several gaps.")
    outage_mode.add_argument("--outage-seed", type=int,
                             help="Simulate repeatable short dropouts and save an outage schedule")
    parser.add_argument("--sensor-noise-std", type=float, default=0,
                        help="Box-center noise as a fraction of box width and height (0 disables it)")
    parser.add_argument("--sensor-noise-probability", type=float, default=0.18,
                        help="Chance of shifting each available detection (default: 0.18)")
    parser.add_argument("--sensor-noise-seed", type=int, default=2027,
                        help="Seed for detection noise, independent of the outage seed (default: 2027)")
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
