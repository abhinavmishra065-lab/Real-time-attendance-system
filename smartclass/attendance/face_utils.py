"""Face-image validation and in-memory encoding cache helpers."""

import base64
import binascii
import threading
from typing import Any

import numpy as np

from .engagement import face_eye_aspect_ratios
from .models import Student

MAX_FRAME_BYTES = 2 * 1024 * 1024
ENCODING_LENGTH = 128

_known_encodings: dict[int, np.ndarray] = {}
_cache_lock = threading.Lock()


class EnrollmentImageError(ValueError):
    """A webcam capture could not be used as a single face sample."""


def decode_face_sample(frame_data: Any) -> np.ndarray:
    """Decode one webcam data URL and return its 128-value face encoding."""
    try:
        import face_recognition
    except ImportError as error:
        raise RuntimeError(
            "Face enrollment needs OpenCV and face_recognition. "
            "Install the smartclass requirements in Python 3.10 or 3.11."
        ) from error

    frame = _decode_image(frame_data)
    small_frame = _resize_for_recognition(frame)
    rgb_frame = _to_rgb(small_frame)
    face_locations = face_recognition.face_locations(rgb_frame)
    if len(face_locations) != 1:
        raise EnrollmentImageError(
            "Exactly one face must be visible. Adjust the camera and try again."
        )

    encodings = face_recognition.face_encodings(rgb_frame, face_locations)
    if len(encodings) != 1:
        raise EnrollmentImageError("The face could not be encoded. Please try again.")
    encoding = np.asarray(encodings[0], dtype=np.float64)
    if encoding.shape != (ENCODING_LENGTH,) or not np.isfinite(encoding).all():
        raise EnrollmentImageError("The face encoding is invalid. Please try again.")
    return encoding


def recognize_faces(
    frame_data: Any,
    known_encodings: dict[int, np.ndarray],
) -> list[tuple[int, int, int, int, int | None, float | None]]:
    """Find faces in a quarter-size frame and match them to enrolled students."""
    try:
        import face_recognition
    except ImportError as error:
        raise RuntimeError(
            "Live attendance needs OpenCV and face_recognition. "
            "Install the smartclass requirements in Python 3.10 or 3.11."
        ) from error

    frame = _decode_image(frame_data)
    height, width = frame.shape[:2]
    small_frame = _resize_for_recognition(frame)
    small_height, small_width = small_frame.shape[:2]
    rgb_frame = _to_rgb(small_frame)
    face_locations = face_recognition.face_locations(rgb_frame)
    face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
    eye_aspect_ratios = face_eye_aspect_ratios(rgb_frame, face_locations)
    known_ids = list(known_encodings)
    known_values = [known_encodings[student_id] for student_id in known_ids]
    scale_x = width / small_width
    scale_y = height / small_height

    results = []
    for (top, right, bottom, left), encoding, ear in zip(
        face_locations, face_encodings, eye_aspect_ratios
    ):
        matched_student_id = None
        if known_values:
            matches = face_recognition.compare_faces(
                known_values, encoding, tolerance=0.5
            )
            distances = face_recognition.face_distance(known_values, encoding)
            matching_indexes = [index for index, matched in enumerate(matches) if matched]
            if matching_indexes:
                best_index = min(matching_indexes, key=lambda index: distances[index])
                matched_student_id = known_ids[best_index]

        results.append(
            (
                round(top * scale_y),
                round(right * scale_x),
                round(bottom * scale_y),
                round(left * scale_x),
                matched_student_id,
                ear,
            )
        )
    return results


def _decode_image(frame_data: Any) -> np.ndarray:
    """Decode and validate a webcam data URL without retaining its image."""
    if not isinstance(frame_data, str) or not frame_data.startswith("data:image/"):
        raise EnrollmentImageError("Capture an image from the webcam and try again.")
    header, separator, encoded_image = frame_data.partition(",")
    if not separator or ";base64" not in header:
        raise EnrollmentImageError("The webcam image format is invalid. Please retry.")
    if len(encoded_image) > MAX_FRAME_BYTES * 4 // 3 + 8:
        raise EnrollmentImageError("The captured image is too large. Please retry.")

    try:
        image_bytes = base64.b64decode(encoded_image, validate=True)
    except (binascii.Error, ValueError) as error:
        raise EnrollmentImageError("The webcam image could not be read. Please retry.") from error
    if not image_bytes or len(image_bytes) > MAX_FRAME_BYTES:
        raise EnrollmentImageError("The captured image is too large or empty.")

    try:
        import cv2
    except ImportError as error:
        raise RuntimeError(
            "Face processing needs OpenCV and face_recognition. "
            "Install the smartclass requirements in Python 3.10 or 3.11."
        ) from error

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if frame is None:
        raise EnrollmentImageError("The webcam image could not be decoded. Please retry.")
    height, width = frame.shape[:2]
    if width < 80 or height < 80 or width > 1920 or height > 1080:
        raise EnrollmentImageError("Use a webcam frame between 80×80 and 1920×1080.")

    return frame


def _resize_for_recognition(frame: np.ndarray) -> np.ndarray:
    """Reduce webcam frames to one quarter size before face processing."""
    import cv2

    return cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)


def _to_rgb(frame: np.ndarray) -> np.ndarray:
    """Convert OpenCV's BGR image into a contiguous RGB image."""
    import cv2

    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def get_known_encodings() -> dict[int, np.ndarray]:
    """Return cached student encodings for later live-recognition phases."""
    global _known_encodings
    with _cache_lock:
        if not _known_encodings:
            _known_encodings = {
                student.id: np.frombuffer(student.face_encoding, dtype=np.float64).copy()
                for student in Student.query.filter(
                    Student.face_encoding.isnot(None)
                ).all()
                if len(student.face_encoding) == ENCODING_LENGTH * 8
            }
        return {student_id: encoding.copy() for student_id, encoding in _known_encodings.items()}


def invalidate_encoding_cache() -> None:
    """Discard cached face encodings after enrollment or deletion."""
    global _known_encodings
    with _cache_lock:
        _known_encodings = {}
