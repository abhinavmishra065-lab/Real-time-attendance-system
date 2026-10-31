"""Blink-based liveness checks for the live attendance flow."""

from importlib import import_module
import threading
import time
from collections.abc import Sequence
from typing import Any

import numpy as np

EAR_THRESHOLD = 0.21
MIN_BLINK_CLOSED_SECONDS = 0.04
MAX_BLINK_CLOSED_SECONDS = 1.0
BLINK_VALID_SECONDS = 5.0

LEFT_EYE_LANDMARKS = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_LANDMARKS = (362, 385, 387, 263, 373, 380)


def eye_aspect_ratio(landmarks: np.ndarray) -> float:
    """Calculate EAR from six ordered eye landmarks."""
    if landmarks.shape != (6, 2):
        raise ValueError("EAR requires six ordered eye landmarks.")

    points = np.asarray(landmarks, dtype=np.float64)
    if not np.isfinite(points).all():
        raise ValueError("Eye landmarks must contain finite coordinates.")

    horizontal = np.linalg.norm(points[0] - points[3])
    if horizontal == 0:
        raise ValueError("Eye landmarks have zero horizontal width.")
    vertical = np.linalg.norm(points[1] - points[5]) + np.linalg.norm(
        points[2] - points[4]
    )
    return float(vertical / (2.0 * horizontal))


def face_eye_aspect_ratios(
    rgb_frame: np.ndarray,
    face_locations: Sequence[tuple[int, int, int, int]],
) -> list[float | None]:
    """Measure each detected face's EAR using MediaPipe Face Mesh landmarks."""
    if not face_locations:
        return []

    try:
        mediapipe = import_module("mediapipe")
    except ImportError as error:
        raise RuntimeError(
            "Blink verification needs MediaPipe. Install the smartclass requirements "
            "in Python 3.10 or 3.11."
        ) from error

    with _face_mesh_lock:
        face_mesh = _get_face_mesh(mediapipe)
        result = face_mesh.process(rgb_frame)
    ears: list[float | None] = [None] * len(face_locations)
    if not result.multi_face_landmarks:
        return ears

    height, width = rgb_frame.shape[:2]
    mesh_boxes = []
    mesh_ears = []
    for face_landmarks in result.multi_face_landmarks:
        points = np.asarray(
            [(point.x * width, point.y * height) for point in face_landmarks.landmark],
            dtype=np.float64,
        )
        if len(points) <= max(*LEFT_EYE_LANDMARKS, *RIGHT_EYE_LANDMARKS):
            continue
        mesh_boxes.append(
            (
                float(np.min(points[:, 1])),
                float(np.max(points[:, 0])),
                float(np.max(points[:, 1])),
                float(np.min(points[:, 0])),
            )
        )
        left_ear = eye_aspect_ratio(points[list(LEFT_EYE_LANDMARKS)])
        right_ear = eye_aspect_ratio(points[list(RIGHT_EYE_LANDMARKS)])
        mesh_ears.append((left_ear + right_ear) / 2.0)

    assigned_meshes: set[int] = set()
    for face_index, location in enumerate(face_locations):
        best_index = None
        best_overlap = 0.0
        for mesh_index, box in enumerate(mesh_boxes):
            if mesh_index in assigned_meshes:
                continue
            overlap = _intersection_over_union(location, box)
            if overlap > best_overlap:
                best_index = mesh_index
                best_overlap = overlap
        if best_index is not None and best_overlap > 0:
            ears[face_index] = mesh_ears[best_index]
            assigned_meshes.add(best_index)
    return ears


class BlinkTracker:
    """Track a single student's eye closures and recent verified blinks."""

    def __init__(self) -> None:
        self._closed_at: float | None = None
        self._last_blink_at: float | None = None

    def update(self, ear: float | None, now: float | None = None) -> bool:
        """Return whether a complete blink occurred in the last five seconds."""
        current_time = time.monotonic() if now is None else now
        if ear is None or not np.isfinite(ear):
            if (
                self._closed_at is not None
                and current_time - self._closed_at > MAX_BLINK_CLOSED_SECONDS
            ):
                self._closed_at = None
            return self._is_recent(current_time)

        if ear < EAR_THRESHOLD:
            if self._closed_at is None:
                self._closed_at = current_time
        elif self._closed_at is not None:
            closed_duration = current_time - self._closed_at
            self._closed_at = None
            if MIN_BLINK_CLOSED_SECONDS <= closed_duration <= MAX_BLINK_CLOSED_SECONDS:
                self._last_blink_at = current_time

        return self._is_recent(current_time)

    def _is_recent(self, now: float) -> bool:
        return (
            self._last_blink_at is not None
            and now - self._last_blink_at <= BLINK_VALID_SECONDS
        )


_face_mesh = None
_face_mesh_lock = threading.Lock()


def _get_face_mesh(mediapipe_module: Any):
    """Create one reusable, thread-safe Face Mesh processor per server process."""
    global _face_mesh
    if _face_mesh is None:
        _face_mesh = mediapipe_module.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=8,
            refine_landmarks=True,
            min_detection_confidence=0.5,
        )
    return _face_mesh


def _intersection_over_union(
    face_location: tuple[int, int, int, int],
    mesh_box: tuple[float, float, float, float],
) -> float:
    """Compare dlib and Face Mesh boxes in top/right/bottom/left order."""
    top, right, bottom, left = face_location
    mesh_top, mesh_right, mesh_bottom, mesh_left = mesh_box
    intersection_width = max(0.0, min(right, mesh_right) - max(left, mesh_left))
    intersection_height = max(0.0, min(bottom, mesh_bottom) - max(top, mesh_top))
    intersection = intersection_width * intersection_height
    face_area = max(0, right - left) * max(0, bottom - top)
    mesh_area = max(0.0, mesh_right - mesh_left) * max(0.0, mesh_bottom - mesh_top)
    union = face_area + mesh_area - intersection
    return intersection / union if union > 0 else 0.0
