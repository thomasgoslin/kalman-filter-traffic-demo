"""Shared demo presets and deterministic detector perturbations."""

import argparse
import math
import random
from fractions import Fraction

from .tracker import ONLINE_MISS_LIMIT, Detection


# Simulated detector availability. The filter and the saved boxes are unaffected.
OUTAGE_START_RATE = 1.5  # Starts per second while the detector is online.
OUTAGE_RECOVERY_PROBABILITY = 0.35  # Per missing frame, before the duration cap.
OUTAGE_MAX_FRAMES = 10

SHORT_OUTAGES = [
    "--outage-seed", "2026",
    "--max-misses", str(max(ONLINE_MISS_LIMIT, OUTAGE_MAX_FRAMES)),
]

RUNS = {
    "traffic-demo": ("Baseline", []),
    "traffic-demo-presentation-random-dropouts": ("Short outages", SHORT_OUTAGES),
    "traffic-demo-noisy-dropouts": ("Short outages + noise", [
        *SHORT_OUTAGES,
        "--sensor-noise-std", "0.16",
        "--sensor-noise-probability", "0.18",
        "--sensor-noise-seed", "2027",
    ]),
    "traffic-demo-presentation-long-outages": ("Long outages", [
        "--max-misses", "60",
        "--drop-frames", "170:185",
        "--drop-frames", "195:225",
        "--drop-frames", "280:325",
        "--drop-frames", "550:610",
    ]),
}


def frame_range(value):
    try:
        start, end = map(int, value.split(":"))
        if 0 <= start < end:
            return range(start, end)
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("Expected START:END with 0 <= START < END (end excluded).")


def scenario_parser():
    parser = argparse.ArgumentParser(add_help=False)
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
    return parser


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


def resolve_dropouts(args, frame_count, fps):
    if args.outage_seed is None:
        return args.drop_frames, None
    return seeded_outages(frame_count, fps, args.outage_seed)


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
