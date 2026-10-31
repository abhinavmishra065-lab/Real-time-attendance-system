"""Create reproducible demo students and 30 days of weekday attendance."""

import random
from datetime import date, timedelta

from database import add_student, get_all_students, init_db, mark_attendance


ATTENDANCE_PROBABILITIES = (
    0.55,
    0.58,
    0.61,
    0.64,
    0.67,
    0.70,
    0.73,
    0.76,
    0.79,
    0.82,
    0.85,
    0.88,
    0.91,
    0.95,
    0.98,
)


def main() -> None:
    init_db()
    existing_students = {
        student["roll_no"]: student for student in get_all_students()
    }

    # Keep demo students separate from any real students already in the database.
    for number in range(1, len(ATTENDANCE_PROBABILITIES) + 1):
        roll_no = f"DEMO{number:03}"
        if roll_no not in existing_students:
            add_student(
                roll_no,
                f"Demo Student {number}",
                f"student{number}@example.com",
            )
    students = {
        student["roll_no"]: student
        for student in get_all_students()
        if student["roll_no"].startswith("DEMO")
        and student["roll_no"][4:].isdigit()
        and 1 <= int(student["roll_no"][4:]) <= len(ATTENDANCE_PROBABILITIES)
    }

    today = date.today()
    inserted_count = 0
    attendance_randomizers = {
        roll_no: random.Random(42 + int(roll_no[4:]))
        for roll_no in students
    }
    time_randomizers = {
        roll_no: random.Random(1042 + int(roll_no[4:]))
        for roll_no in students
    }

    for day_offset in range(29, -1, -1):
        attendance_day = today - timedelta(days=day_offset)
        if attendance_day.weekday() >= 5:
            continue

        attendance_date = attendance_day.isoformat()
        for roll_no, student in sorted(students.items()):
            student_number = int(roll_no[4:])
            probability = ATTENDANCE_PROBABILITIES[student_number - 1]
            attendance_randomizer = attendance_randomizers[roll_no]
            time_randomizer = time_randomizers[roll_no]
            if attendance_randomizer.random() < probability:
                status = "Late" if time_randomizer.random() < 0.08 else "Present"
                attendance_time = (
                    f"09:{time_randomizer.randint(1, 20):02}:00"
                    if status == "Late"
                    else f"08:{time_randomizer.randint(0, 59):02}:00"
                )
            else:
                status = "Absent"
                attendance_time = ""
            if mark_attendance(
                student["id"], attendance_date, attendance_time, status
            ):
                inserted_count += 1

    print(
        f"Added {inserted_count} attendance records "
        f"for {len(students)} demo students on weekdays in the last 30 days."
    )


if __name__ == "__main__":
    main()
