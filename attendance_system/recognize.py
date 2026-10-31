"""Recognize enrolled students and mark attendance from a live webcam feed."""

import pickle
import math
import time
from datetime import date
from pathlib import Path
from typing import Sequence

from database import (
    get_all_students,
    get_attendance_df,
    init_db,
    mark_attendance,
)


TOLERANCE = 0.5
FRAME_SCALE = 0.25
PROCESS_EVERY_N_FRAMES = 2
BLINK_WINDOW_SECONDS = 5.0
EAR_THRESHOLD = 0.21
LEFT_EYE_LANDMARKS = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_LANDMARKS = (362, 385, 387, 263, 373, 380)


def _eye_aspect_ratio(
    landmarks: Sequence[tuple[float, float]],
    eye_indices: tuple[int, int, int, int, int, int],
) -> float:
    """Calculate EAR from six ordered eye landmarks."""
    first, second, third, fourth, fifth, sixth = (
        landmarks[index] for index in eye_indices
    )
    horizontal = math.dist(first, fourth)
    if horizontal == 0:
        return 0.0
    vertical = math.dist(second, sixth) + math.dist(third, fifth)
    return vertical / (2.0 * horizontal)


def main() -> None:
    import cv2
    import face_recognition
    import mediapipe as mp
    import numpy as np

    init_db()
    students_by_id = {student["id"]: student for student in get_all_students()}
    students_by_roll = {student["roll_no"]: student for student in students_by_id.values()}
    encodings_folder = Path(__file__).with_name("encodings")
    known_encodings = []
    known_student_ids = []

    # Load roll-number pickle files created by enroll.py.
    for encoding_file in encodings_folder.glob("*.pkl"):
        student = students_by_roll.get(encoding_file.stem)
        if student is not None:
            with encoding_file.open("rb") as file:
                encoding = np.asarray(pickle.load(file))
            if encoding.shape != (128,):
                raise ValueError(f"Invalid face encoding in {encoding_file.name}.")
            known_encodings.append(encoding)
            known_student_ids.append(student["id"])

    # Keep support for older encodings saved by the earlier enrollment script.
    for encoding_file in encodings_folder.glob("student_*.npy"):
        student_id = int(encoding_file.stem.split("_")[1])
        if student_id in students_by_id and student_id not in known_student_ids:
            encoding = np.load(encoding_file)
            if encoding.shape != (128,):
                raise ValueError(f"Invalid face encoding in {encoding_file.name}.")
            known_encodings.append(encoding)
            known_student_ids.append(student_id)

    if not known_encodings:
        raise RuntimeError("No enrolled face encodings found. Run enroll.py first.")

    today = date.today().isoformat()
    todays_attendance = get_attendance_df(today, today)
    present_student_ids = set(
        todays_attendance.loc[
            todays_attendance["status"].isin(("Present", "Late")), "student_id"
        ].tolist()
    )
    present_count = len(present_student_ids)
    attendance_messages = {}
    blink_states = {}

    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError("Could not open the webcam.")

    frame_number = 0
    fps = 0.0
    previous_frame_time = time.perf_counter()
    displayed_faces = []

    try:
        with mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=10,
            refine_landmarks=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ) as face_mesh:
            print("Press Q to stop recognition.")
            while True:
                success, frame = camera.read()
                if not success:
                    raise RuntimeError("Could not read a frame from the webcam.")

                frame_number += 1
                if frame_number % PROCESS_EVERY_N_FRAMES == 0:
                    small_frame = cv2.resize(
                        frame, (0, 0), fx=FRAME_SCALE, fy=FRAME_SCALE
                    )
                    rgb_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
                    face_locations = face_recognition.face_locations(rgb_frame)
                    face_encodings = face_recognition.face_encodings(
                        rgb_frame, face_locations
                    )
                    mesh_result = face_mesh.process(rgb_frame)
                    mesh_faces = mesh_result.multi_face_landmarks or []
                    frame_height, frame_width = rgb_frame.shape[:2]
                    mesh_data = []
                    for mesh_face in mesh_faces:
                        mesh_landmarks = [
                            (point.x, point.y) for point in mesh_face.landmark
                        ]
                        mesh_xs = [point[0] for point in mesh_landmarks]
                        mesh_ys = [point[1] for point in mesh_landmarks]
                        mesh_data.append(
                            {
                                "landmarks": mesh_landmarks,
                                "center": (
                                    (min(mesh_xs) + max(mesh_xs))
                                    * frame_width
                                    / 2,
                                    (min(mesh_ys) + max(mesh_ys))
                                    * frame_height
                                    / 2,
                                ),
                            }
                        )

                    used_mesh_faces = set()
                    displayed_faces = []
                    recognition_time = time.perf_counter()

                    for face_location, face_encoding in zip(
                        face_locations, face_encodings
                    ):
                        top, right, bottom, left = face_location
                        scaled_location = (
                            int(top / FRAME_SCALE),
                            int(right / FRAME_SCALE),
                            int(bottom / FRAME_SCALE),
                            int(left / FRAME_SCALE),
                        )
                        distances = face_recognition.face_distance(
                            known_encodings, face_encoding
                        )
                        closest_index = int(np.argmin(distances))

                        if distances[closest_index] <= TOLERANCE:
                            student_id = known_student_ids[closest_index]
                            student = students_by_id[student_id]
                            face_center_x = (left + right) / 2
                            face_center_y = (top + bottom) / 2
                            matching_mesh_index = None
                            matching_mesh_distance = float("inf")

                            for mesh_index, mesh_face in enumerate(mesh_data):
                                if mesh_index in used_mesh_faces:
                                    continue
                                mesh_center_x, mesh_center_y = mesh_face["center"]
                                if left <= mesh_center_x <= right and top <= mesh_center_y <= bottom:
                                    center_distance = math.hypot(
                                        (mesh_center_x - face_center_x)
                                        / max(right - left, 1),
                                        (mesh_center_y - face_center_y)
                                        / max(bottom - top, 1),
                                    )
                                    if center_distance < matching_mesh_distance:
                                        matching_mesh_distance = center_distance
                                        matching_mesh_index = mesh_index

                            state = blink_states.setdefault(
                                student_id,
                                {
                                    "eyes_opened": False,
                                    "eyes_closed": False,
                                    "last_blink_at": None,
                                },
                            )
                            if matching_mesh_index is not None:
                                used_mesh_faces.add(matching_mesh_index)
                                landmarks = mesh_data[matching_mesh_index]["landmarks"]
                                ear = (
                                    _eye_aspect_ratio(landmarks, LEFT_EYE_LANDMARKS)
                                    + _eye_aspect_ratio(
                                        landmarks, RIGHT_EYE_LANDMARKS
                                    )
                                ) / 2.0

                                if ear >= EAR_THRESHOLD:
                                    if state["eyes_closed"]:
                                        state["last_blink_at"] = recognition_time
                                        state["eyes_closed"] = False
                                    state["eyes_opened"] = True
                                elif state["eyes_opened"]:
                                    state["eyes_closed"] = True

                            if attendance_messages.get(student_id) not in (
                                "Attendance marked",
                                "Already marked",
                            ):
                                last_blink_at = state["last_blink_at"]
                                blink_is_recent = (
                                    last_blink_at is not None
                                    and recognition_time - last_blink_at
                                    <= BLINK_WINDOW_SECONDS
                                )
                                if blink_is_recent:
                                    was_marked = mark_attendance(student_id)
                                    attendance_messages[student_id] = (
                                        "Attendance marked"
                                        if was_marked
                                        else "Already marked"
                                    )
                                    if was_marked:
                                        present_student_ids.add(student_id)
                                        present_count = len(present_student_ids)
                                else:
                                    attendance_messages[student_id] = "Please blink"

                            displayed_faces.append(
                                (
                                    scaled_location,
                                    student["name"],
                                    attendance_messages[student_id],
                                    (0, 255, 0),
                                )
                            )
                        else:
                            displayed_faces.append(
                                (scaled_location, "Unknown", "", (0, 0, 255))
                            )

                for location, name, attendance_message, color in displayed_faces:
                    top, right, bottom, left = location
                    cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                    cv2.putText(
                        frame,
                        name,
                        (left, max(top - 10, 20)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        color,
                        2,
                    )
                    if attendance_message:
                        cv2.putText(
                            frame,
                            attendance_message,
                            (left, min(bottom + 25, frame.shape[0] - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            color,
                            2,
                        )

                current_time = time.perf_counter()
                frame_duration = current_time - previous_frame_time
                if frame_duration > 0:
                    instant_fps = 1.0 / frame_duration
                    fps = (
                        instant_fps
                        if fps == 0
                        else (0.9 * fps) + (0.1 * instant_fps)
                    )
                previous_frame_time = current_time
                cv2.putText(
                    frame,
                    f"FPS: {fps:.1f}  Present: {present_count}",
                    (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (255, 255, 255),
                    2,
                )
                cv2.imshow("Live attendance - press Q to quit", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
