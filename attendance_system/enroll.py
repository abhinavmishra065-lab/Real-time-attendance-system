"""Enroll a student and save a face encoding from the webcam."""

import pickle
from pathlib import Path

from database import add_student, get_all_students, init_db


def main() -> None:
    import cv2
    import face_recognition
    import numpy as np

    init_db()
    roll_no = input("Roll number: ").strip()
    name = input("Student name: ").strip()
    email = input("Student email: ").strip()
    if not roll_no or not name or not email:
        raise ValueError("Roll number, name, and email are required.")

    if any(character in roll_no for character in '<>:"/\\|?*') or roll_no.endswith(
        (" ", ".")
    ):
        raise ValueError("Roll number contains characters that cannot be used in a filename.")
    if any(student["roll_no"] == roll_no for student in get_all_students()):
        raise ValueError(f"Roll number {roll_no} is already enrolled.")

    encodings_folder = Path(__file__).with_name("encodings")
    encoding_path = encodings_folder / f"{roll_no}.pkl"
    if encoding_path.exists():
        raise FileExistsError(f"An encoding file already exists for roll number {roll_no}.")

    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError("Could not open the webcam.")

    face_samples = []
    try:
        print("Look at the camera. Press SPACE to capture each face sample.")
        print("Press Q to cancel.")
        while len(face_samples) < 10:
            success, frame = camera.read()
            if not success:
                raise RuntimeError("Could not read a frame from the webcam.")

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_frame)
            for top, right, bottom, left in face_locations:
                cv2.rectangle(frame, (left, top), (right, bottom), (0, 255, 0), 2)

            cv2.putText(
                frame,
                f"Samples: {len(face_samples)}/10",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (255, 255, 255),
                2,
            )
            cv2.imshow("Student enrollment", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord(" "):
                if len(face_locations) != 1:
                    print("Exactly one face must be visible to capture a sample.")
                    continue
                encodings = face_recognition.face_encodings(
                    rgb_frame, face_locations
                )
                if not encodings:
                    print("Could not encode the face. Please try again.")
                    continue
                face_samples.append(encodings[0])
                print(f"Captured {len(face_samples)}/10 samples.")
            if key == ord("q"):
                print("Enrollment cancelled.")
                return
    finally:
        camera.release()
        cv2.destroyAllWindows()

    average_encoding = np.mean(face_samples, axis=0)
    if average_encoding.shape != (128,):
        raise ValueError("Expected each face encoding to contain 128 values.")

    encodings_folder.mkdir(exist_ok=True)
    with encoding_path.open("xb") as encoding_file:
        pickle.dump(average_encoding, encoding_file)
    add_student(roll_no, name, email)
    print(f"Enrolled {name} (roll number {roll_no}) with 10 face samples.")


if __name__ == "__main__":
    main()
