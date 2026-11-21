"""Linear Kalman filter, detection assignment, and per-track lifecycle."""

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment


# State order: center x, center y, width, height, velocity x, velocity y.
F = np.eye(6)
F[0, 4] = F[1, 5] = 1.0
H = np.eye(6)[:4]
Q = np.diag([2, 2, 1, 1, 1, 1])
R = np.diag([25, 25, 36, 36])
P0 = np.diag([25, 25, 36, 36, 100, 100])

ONLINE_MISS_LIMIT = 8

@dataclass(frozen=True)
class FilterModel:
    F: np.ndarray
    H: np.ndarray
    Q: np.ndarray
    R: np.ndarray
    P0: np.ndarray
    b: np.ndarray = field(default_factory=lambda: np.zeros(6))
    initial_velocity: np.ndarray = field(default_factory=lambda: np.zeros(2))


DEFAULT_MODEL = FilterModel(F, H, Q, R, P0)


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
    def __init__(
        self, min_iou: float = 0.1, max_misses: int = ONLINE_MISS_LIMIT, *,
        model: FilterModel = DEFAULT_MODEL, online_misses: int | None = None,
        retire_unmatched: bool = True,
    ):
        self.min_iou = min_iou
        self.max_misses = max_misses
        self.model = model
        self.online_misses = min(max_misses, ONLINE_MISS_LIMIT) if online_misses is None else online_misses
        self.retire_unmatched = retire_unmatched
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
        model = self.model
        forecasts, predicted_observations = [], []
        for track in self.tracks:
            track.mean = model.F @ track.mean + model.b
            track.covariance = model.F @ track.covariance @ model.F.T + model.Q
            forecasts.append(to_box(track.mean[:4]))
            predicted_observations.append(to_box(model.H @ track.mean))

        miss_limit = self.max_misses if withheld else self.online_misses
        matches = self._assign(predicted_observations, detections)
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
                    track.mean, track.covariance, to_measurement(box), model.H, model.R, diagnostics=record,
                )
                track.misses = 0
                if record is not None:
                    detection = detections[matches[i]]
                    record.update(
                        status="updated", detection_index=matches[i],
                        detection_confidence=detection.confidence,
                        assignment_iou=intersection_over_union(predicted_observations[i], box),
                    )
                visible.append(
                    FrameTrack(track.id, track.class_id, forecasts[i], box, to_box(track.mean[:4]))
                )
            else:
                track.misses += 1
                retired_on_recovery = recovering and self.retire_unmatched
                expired = retired_on_recovery or track.misses > miss_limit
                if record is not None:
                    record["status"] = "expired" if expired else "predicted"
                    if expired:
                        record["expiry_reason"] = "unmatched_on_recovery" if retired_on_recovery else "miss_limit"
                if not expired:
                    visible.append(FrameTrack(track.id, track.class_id, forecasts[i]))
            if record is not None:
                telemetry.append(self._track_record(track, record))
            if not expired:
                survivors.append(track)

        for j, detection in enumerate(detections):
            if j not in used_detections:
                mean = np.r_[to_measurement(detection.box), model.initial_velocity]
                track = Track(self.next_id, detection.class_id, mean, model.P0.copy())
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
