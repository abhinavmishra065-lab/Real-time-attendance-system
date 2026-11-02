"""Scheduled classes, geofenced student check-in, and teacher attendance review."""

import math
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from flask import current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from . import attendance, db
from .face_utils import EnrollmentImageError, get_known_encodings, recognize_faces
from .models import Attendance, ClassSchedule, ClassSession, Student, Subject
from .routes import (
    ENCODING_LENGTH,
    _csrf_token,
    _csrf_token_from_json,
    _valid_csrf_token,
    login_required,
    student_login_required,
)
from .time_utils import local_date_from_utc, local_now, local_today, utc_naive_from_local

_maintenance_lock = threading.Lock()
_maintenance_started = False
_session_generation_lock = threading.Lock()


def _schedule_matches_student(schedule: ClassSchedule, student: Student) -> bool:
    return (
        (student.course or "").casefold() == schedule.course.casefold()
        and (student.branch or "").casefold() == schedule.branch.casefold()
        and student.semester == schedule.semester
    )


def _eligible_students(
    schedule: ClassSchedule,
    class_date: date | None = None,
) -> list[Student]:
    query = Student.query.filter(
        func.lower(Student.course) == schedule.course.casefold(),
        func.lower(Student.branch) == schedule.branch.casefold(),
        Student.semester == schedule.semester,
    )
    if class_date is not None:
        query = query.filter(
            Student.created_at
            < utc_naive_from_local(
                datetime.combine(class_date + timedelta(days=1), datetime.min.time())
            )
        )
    return query.order_by(Student.roll_no).all()


def _ensure_sessions_through(end_day: date) -> None:
    with _session_generation_lock:
        _ensure_sessions_through_unlocked(end_day)


def _ensure_sessions_through_unlocked(end_day: date) -> None:
    schedules = ClassSchedule.query.filter_by(is_active=True).all()
    existing = {
        (schedule_id, class_date)
        for schedule_id, class_date in db.session.query(
            ClassSession.schedule_id, ClassSession.class_date
        ).filter(ClassSession.class_date <= end_day)
    }
    created = False
    for schedule in schedules:
        day = schedule.starts_on
        while day <= end_day:
            key = (schedule.id, day)
            if day.weekday() == schedule.weekday and key not in existing:
                session_record = ClassSession()
                session_record.schedule_id = schedule.id
                session_record.class_date = day
                session_record.starts_at = datetime.combine(day, schedule.start_time)
                session_record.ends_at = datetime.combine(day, schedule.end_time)
                db.session.add(session_record)
                existing.add(key)
                created = True
            day += timedelta(days=1)
    if created:
        db.session.commit()


def _attendance_for_session(class_session: ClassSession) -> dict[int, Attendance]:
    return {
        record.student_id: record
        for record in Attendance.query.filter_by(session_id=class_session.id).all()
    }


def _finalize_ended_sessions(now: datetime) -> None:
    due_sessions = ClassSession.query.filter(ClassSession.ends_at <= now).all()
    changed = False
    for class_session in due_sessions:
        schedule = db.session.get(ClassSchedule, class_session.schedule_id)
        if schedule is None:
            current_app.logger.error(
                "Class session %s references a missing timetable entry",
                class_session.id,
            )
            continue
        present_records = _attendance_for_session(class_session)
        for student in _eligible_students(schedule, class_session.class_date):
            if student.id not in present_records:
                record = Attendance()
                record.student_id = student.id
                record.session_id = class_session.id
                record.date = class_session.class_date
                record.time = None
                record.status = "Absent"
                record.source = "auto_absent"
                db.session.add(record)
                changed = True
    if changed:
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            current_app.logger.exception(
                "Concurrent class attendance finalization; retrying on next maintenance cycle"
            )
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Database error while finalizing class attendance")
            raise


def start_attendance_maintenance(app: Any) -> None:
    """Run timetable generation and absence finalization for the single-process demo."""
    global _maintenance_started
    if app.testing or app.config.get("DISABLE_ATTENDANCE_MAINTENANCE"):
        return
    with _maintenance_lock:
        if _maintenance_started:
            return
        thread = threading.Thread(
            target=_maintenance_loop,
            args=(app,),
            name="smartclass-attendance-maintenance",
            daemon=True,
        )
        _maintenance_started = True
        app.extensions["attendance_maintenance_thread"] = thread
        thread.start()


def _maintenance_loop(app: Any) -> None:
    while True:
        with app.app_context():
            try:
                _ensure_sessions_through(local_today())
                _finalize_ended_sessions(local_now())
            except SQLAlchemyError:
                db.session.rollback()
                app.logger.exception("Scheduled attendance maintenance failed")
        time.sleep(15)


def _attendance_status(class_session: ClassSession, students: list[Student]) -> list[dict[str, Any]]:
    records = _attendance_for_session(class_session)
    return [
        {
            "student_id": student.id,
            "name": student.name,
            "roll_no": student.roll_no,
            "status": records[student.id].status if student.id in records else "Not checked in",
            "time": (
                records[student.id].time.strftime("%I:%M %p")
                if student.id in records and records[student.id].time
                else None
            ),
            "source": records[student.id].source if student.id in records else None,
        }
        for student in students
    ]


@attendance.route("/timetable", methods=("GET", "POST"))
@login_required
def schedules():
    if request.method == "POST":
        if not _valid_csrf_token():
            flash("Your session expired. Please try again.", "error")
            return redirect(url_for("attendance.schedules"))
        subject_value = request.form.get("subject_id", "").strip()
        weekday_value = request.form.get("weekday", "").strip()
        start_value = request.form.get("start_time", "").strip()
        end_value = request.form.get("end_time", "").strip()
        latitude_value = request.form.get("latitude", "").strip()
        longitude_value = request.form.get("longitude", "").strip()
        radius_value = request.form.get("radius_m", "100").strip()
        try:
            subject_id = int(subject_value)
            weekday = int(weekday_value)
            start_time = datetime.strptime(start_value, "%H:%M").time()
            end_time = datetime.strptime(end_value, "%H:%M").time()
            latitude = float(latitude_value)
            longitude = float(longitude_value)
            radius = int(radius_value)
        except ValueError:
            flash("Choose a subject and enter valid class times and classroom coordinates.", "error")
            return redirect(url_for("attendance.schedules"))

        subject = db.session.get(Subject, subject_id)
        if subject is None:
            flash("Choose a subject that exists before scheduling a class.", "error")
            return redirect(url_for("attendance.schedules"))
        course = subject.course
        branch = subject.branch
        semester = subject.semester
        if (
            weekday not in range(7)
            or start_time >= end_time
            or not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
            or radius not in range(25, 501)
        ):
            flash("Check the course, branch, semester, time range, and geofence values.", "error")
            return redirect(url_for("attendance.schedules"))

        overlap = ClassSchedule.query.filter(
            ClassSchedule.is_active.is_(True),
            ClassSchedule.weekday == weekday,
            ClassSchedule.semester == semester,
            func.lower(ClassSchedule.course) == course.casefold(),
            func.lower(ClassSchedule.branch) == branch.casefold(),
            ClassSchedule.start_time < end_time,
            ClassSchedule.end_time > start_time,
        ).first()
        if overlap is not None:
            flash("This class overlaps another timetable entry for the same group.", "error")
            return redirect(url_for("attendance.schedules"))

        schedule = ClassSchedule()
        schedule.course = course
        schedule.branch = branch
        schedule.semester = semester
        schedule.weekday = weekday
        schedule.start_time = start_time
        schedule.end_time = end_time
        schedule.latitude = latitude
        schedule.longitude = longitude
        schedule.radius_m = radius
        schedule.subject_id = subject.id
        schedule.teacher_id = g.teacher.id if g.teacher is not None else None
        db.session.add(schedule)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Database error while creating class schedule")
            flash("The class schedule could not be saved.", "error")
        else:
            flash("Weekly class schedule saved.", "success")
        return redirect(url_for("attendance.schedules"))

    _ensure_sessions_through(local_today())
    active_schedules = (
        ClassSchedule.query.filter_by(is_active=True)
        .order_by(ClassSchedule.weekday, ClassSchedule.start_time)
        .all()
    )
    schedules_by_subject: dict[int | None, list[ClassSchedule]] = {}
    for schedule in active_schedules:
        schedules_by_subject.setdefault(schedule.subject_id, []).append(schedule)
    subject_sections = [
        {"subject": subject, "schedules": schedules_by_subject.pop(subject.id, [])}
        for subject in Subject.query.order_by(
            Subject.course, Subject.branch, Subject.semester, Subject.name
        ).all()
    ]
    if None in schedules_by_subject:
        subject_sections.append(
            {"subject": None, "schedules": schedules_by_subject[None]}
        )

    todays_sessions = (
        ClassSession.query.join(ClassSchedule)
        .filter(ClassSession.class_date == local_today(), ClassSchedule.is_active.is_(True))
        .order_by(ClassSession.starts_at)
        .all()
    )
    return render_template(
        "timetable.html",
        subjects=Subject.query.order_by(
            Subject.course, Subject.branch, Subject.semester, Subject.name
        ).all(),
        subject_sections=subject_sections,
        todays_sessions=todays_sessions,
        weekday_names=("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
        csrf_token=_csrf_token(),
    )


@attendance.post("/subjects")
@login_required
def create_subject():
    if not _valid_csrf_token():
        flash("Your session expired. Please try again.", "error")
        return redirect(url_for("attendance.schedules"))
    name = request.form.get("name", "").strip()
    course = request.form.get("course", "").strip()
    branch = request.form.get("branch", "").strip()
    semester_value = request.form.get("semester", "").strip()
    try:
        semester = int(semester_value)
    except ValueError:
        flash("Choose a valid semester for the subject.", "error")
        return redirect(url_for("attendance.schedules"))
    if (
        not name
        or not course
        or not branch
        or len(name) > 120
        or len(course) > 120
        or len(branch) > 120
        or semester not in range(1, 13)
    ):
        flash("Enter a subject, course, branch, and semester.", "error")
        return redirect(url_for("attendance.schedules"))
    existing_subject = Subject.query.filter(
        func.lower(Subject.name) == name.casefold(),
        func.lower(Subject.course) == course.casefold(),
        func.lower(Subject.branch) == branch.casefold(),
        Subject.semester == semester,
    ).first()
    if existing_subject is not None:
        flash("That subject already exists for this course, branch, and semester.", "error")
        return redirect(url_for("attendance.schedules"))

    subject = Subject()
    subject.name = name
    subject.course = course
    subject.branch = branch
    subject.semester = semester
    subject.teacher_id = g.teacher.id if g.teacher is not None else None
    db.session.add(subject)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("That subject already exists for this course, branch, and semester.", "error")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error while creating subject")
        flash("The subject could not be saved.", "error")
    else:
        flash(f"{name} added to the subject list.", "success")
    return redirect(url_for("attendance.schedules"))


@attendance.post("/timetable/<int:schedule_id>/subject")
@login_required
def assign_schedule_subject(schedule_id: int):
    if not _valid_csrf_token():
        flash("Your session expired. Please try again.", "error")
        return redirect(url_for("attendance.schedules"))
    schedule = db.session.get(ClassSchedule, schedule_id)
    if schedule is None:
        flash("That scheduled class no longer exists.", "error")
        return redirect(url_for("attendance.schedules"))
    try:
        subject_id = int(request.form.get("subject_id", ""))
    except ValueError:
        flash("Choose a valid subject for this class.", "error")
        return redirect(url_for("attendance.schedules"))
    subject = db.session.get(Subject, subject_id)
    if subject is None or (
        subject.course.casefold() != schedule.course.casefold()
        or subject.branch.casefold() != schedule.branch.casefold()
        or subject.semester != schedule.semester
    ):
        flash("Choose a subject from the same course, branch, and semester.", "error")
        return redirect(url_for("attendance.schedules"))
    schedule.subject_id = subject.id
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error while assigning subject to schedule")
        flash("The subject could not be assigned to this scheduled class.", "error")
    else:
        flash(
            f"Existing sessions and attendance are now grouped under {subject.name}.",
            "success",
        )
    return redirect(url_for("attendance.schedules"))


@attendance.post("/timetable/<int:schedule_id>/delete")
@login_required
def delete_schedule(schedule_id: int):
    if not _valid_csrf_token():
        flash("Your session expired. Please try again.", "error")
        return redirect(url_for("attendance.schedules"))
    schedule = db.session.get(ClassSchedule, schedule_id)
    if schedule is None:
        flash("That scheduled class no longer exists.", "error")
        return redirect(url_for("attendance.schedules"))
    session_ids = db.session.query(ClassSession.id).filter_by(schedule_id=schedule.id)
    try:
        Attendance.query.filter(Attendance.session_id.in_(session_ids)).delete(
            synchronize_session=False
        )
        db.session.delete(schedule)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error while deleting scheduled class")
        flash("The scheduled class and its attendance records could not be deleted.", "error")
    else:
        flash(
            "Scheduled class deleted. Its generated sessions and attendance records were also removed.",
            "success",
        )
    return redirect(url_for("attendance.schedules"))


@attendance.get("/classes/<int:session_id>")
@login_required
def class_session_detail(session_id: int):
    class_session = db.session.get(ClassSession, session_id)
    if class_session is None:
        flash("That class session no longer exists.", "error")
        return redirect(url_for("attendance.schedules"))
    _finalize_ended_sessions(local_now())
    class_schedule = db.session.get(ClassSchedule, class_session.schedule_id)
    if class_schedule is None:
        flash("The timetable entry for this session no longer exists.", "error")
        return redirect(url_for("attendance.schedules"))
    students = _eligible_students(class_schedule, class_session.class_date)
    return render_template(
        "class_session.html",
        class_session=class_session,
        students=students,
        attendance_rows=_attendance_status(class_session, students),
        status_url=url_for("attendance.class_session_status", session_id=session_id),
        csrf_token=_csrf_token(),
        now=local_now(),
    )


@attendance.get("/api/classes/<int:session_id>/status")
@login_required
def class_session_status(session_id: int):
    class_session = db.session.get(ClassSession, session_id)
    if class_session is None:
        return jsonify(error="That class session no longer exists."), 404
    _finalize_ended_sessions(local_now())
    class_schedule = db.session.get(ClassSchedule, class_session.schedule_id)
    if class_schedule is None:
        return jsonify(error="The timetable entry for this session no longer exists."), 404
    students = _eligible_students(class_schedule, class_session.class_date)
    rows = _attendance_status(class_session, students)
    return jsonify(
        rows=rows,
        present=sum(row["status"] == "Present" for row in rows),
        absent=sum(row["status"] == "Absent" for row in rows),
        pending=sum(row["status"] == "Not checked in" for row in rows),
        ended=class_session.ends_at <= local_now(),
    )


@attendance.post("/classes/<int:session_id>/students/<int:student_id>/attendance")
@login_required
def update_class_attendance(session_id: int, student_id: int):
    wants_json = request.accept_mimetypes.best == "application/json"
    if not _valid_csrf_token():
        if wants_json:
            return jsonify(error="Your session expired. Please try again."), 400
        flash("Your session expired. Please try again.", "error")
        return redirect(url_for("attendance.class_session_detail", session_id=session_id))
    class_session = db.session.get(ClassSession, session_id)
    student = db.session.get(Student, student_id)
    class_schedule = (
        db.session.get(ClassSchedule, class_session.schedule_id)
        if class_session is not None
        else None
    )
    if class_session is None or student is None or class_schedule is None:
        if wants_json:
            return jsonify(error="That student is not part of this class session."), 404
        flash("That student is not part of this class session.", "error")
        return redirect(url_for("attendance.schedules"))
    if not _schedule_matches_student(class_schedule, student) or (
        local_date_from_utc(student.created_at) > class_session.class_date
    ):
        if wants_json:
            return jsonify(error="That student is not part of this class session."), 404
        flash("That student is not part of this class session.", "error")
        return redirect(url_for("attendance.schedules"))
    status_value = request.form.get("status", "")
    if status_value not in ("Present", "Absent"):
        if wants_json:
            return jsonify(error="Choose Present or Absent."), 400
        flash("Choose Present or Absent.", "error")
        return redirect(url_for("attendance.class_session_detail", session_id=session_id))
    record = Attendance.query.filter_by(
        session_id=session_id,
        student_id=student_id,
    ).first()
    if record is None:
        record = Attendance()
        record.student_id = student_id
        record.session_id = session_id
        record.date = class_session.class_date
        db.session.add(record)
    record.status = status_value
    record.time = local_now().time() if status_value == "Present" else None
    record.source = "teacher"
    record.marked_by_teacher_id = g.teacher.id if g.teacher is not None else None
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error while updating class attendance")
        if wants_json:
            return jsonify(error="Attendance could not be updated."), 500
        flash("Attendance could not be updated.", "error")
    else:
        if wants_json:
            return jsonify(
                name=student.name,
                status=record.status,
                time=record.time.strftime("%I:%M %p") if record.time else None,
            )
        flash(f"{student.name} marked {status_value.lower()}.", "success")
    return redirect(url_for("attendance.class_session_detail", session_id=session_id))


@attendance.get("/student/classes/<int:session_id>/check-in")
@student_login_required
def student_checkin(session_id: int):
    class_session = db.session.get(ClassSession, session_id)
    class_schedule = (
        db.session.get(ClassSchedule, class_session.schedule_id)
        if class_session is not None
        else None
    )
    if (
        class_session is None
        or class_schedule is None
        or not _schedule_matches_student(class_schedule, g.student)
        or local_date_from_utc(g.student.created_at) > class_session.class_date
    ):
        flash("That class session is not available for your student account.", "error")
        return redirect(url_for("attendance.student_dashboard"))
    token = secrets.token_urlsafe(32)
    session["student_checkin_token"] = token
    existing_record = Attendance.query.filter_by(
        session_id=session_id,
        student_id=g.student.id,
    ).first()
    return render_template(
        "student_checkin.html",
        student=g.student,
        class_session=class_session,
        csrf_token=_csrf_token(),
        checkin_token=token,
        existing_record=existing_record,
        now=local_now(),
    )


def _distance_meters(latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float) -> float:
    earth_radius_m = 6_371_000
    lat_a, lat_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2) ** 2
    )
    return earth_radius_m * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _payload_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


@attendance.post("/api/student/check-in/frame")
@student_login_required
def student_checkin_frame():
    payload = request.get_json(silent=True)
    if (
        not isinstance(payload, dict)
        or not _csrf_token_from_json()
        or payload.get("checkin_token") != session.get("student_checkin_token")
    ):
        return jsonify(error="Your session expired. Reload the check-in page."), 400
    raw_session_id = payload.get("session_id")
    if isinstance(raw_session_id, bool) or not isinstance(raw_session_id, (str, int)):
        return jsonify(error="Allow location access to check in."), 400
    try:
        session_id = int(raw_session_id)
    except ValueError:
        return jsonify(error="Allow location access to check in."), 400
    latitude = _payload_number(payload.get("latitude"))
    longitude = _payload_number(payload.get("longitude"))
    accuracy = _payload_number(payload.get("accuracy"))
    if latitude is None or longitude is None or accuracy is None:
        return jsonify(error="Allow location access to check in."), 400
    if (
        not all(math.isfinite(value) for value in (latitude, longitude, accuracy))
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
        or accuracy <= 0
    ):
        return jsonify(error="Could not read a valid location. Allow location access and try again."), 400

    class_session = db.session.get(ClassSession, session_id)
    now = local_now()
    class_schedule = (
        db.session.get(ClassSchedule, class_session.schedule_id)
        if class_session is not None
        else None
    )
    if (
        class_session is None
        or class_schedule is None
        or not _schedule_matches_student(class_schedule, g.student)
        or local_date_from_utc(g.student.created_at) > class_session.class_date
    ):
        return jsonify(error="That class session is not available for your account."), 403
    if now < class_session.starts_at or now > class_session.ends_at:
        return jsonify(error="Check-in is available only during the scheduled class."), 403
    distance = _distance_meters(
        latitude,
        longitude,
        class_schedule.latitude,
        class_schedule.longitude,
    )
    if distance > class_schedule.radius_m:
        return jsonify(error="You are outside the classroom check-in area."), 403

    existing_record = Attendance.query.filter_by(
        session_id=session_id,
        student_id=g.student.id,
    ).first()
    if existing_record is not None:
        return jsonify(status=existing_record.status, already_recorded=True)
    encodings = get_known_encodings()
    student_encoding = encodings.get(g.student.id)
    if student_encoding is None or student_encoding.shape != (ENCODING_LENGTH,):
        return jsonify(error="Your face profile is unavailable. Ask your teacher for help."), 409
    try:
        face_results = recognize_faces(payload.get("frame"), {g.student.id: student_encoding})
    except EnrollmentImageError as error:
        return jsonify(error=str(error)), 400
    except RuntimeError as error:
        current_app.logger.exception("Student check-in face recognition failed")
        return jsonify(error=str(error)), 503

    if not face_results:
        return jsonify(
            recognized=False,
            status="No face detected. Look at the camera and try again.",
        )
    if len(face_results) != 1:
        return jsonify(
            recognized=False,
            status="More than one face detected. Make sure only you are in view.",
        )
    if face_results[0][4] != g.student.id:
        return jsonify(
            recognized=False,
            status="Face not verified. Look toward the camera and try again.",
        )

    record = Attendance()
    record.student_id = g.student.id
    record.session_id = session_id
    record.date = class_session.class_date
    record.time = now.time()
    record.status = "Present"
    record.source = "student"
    db.session.add(record)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing_record = Attendance.query.filter_by(
            session_id=session_id,
            student_id=g.student.id,
        ).first()
        if existing_record is None:
            raise
        return jsonify(status=existing_record.status, already_recorded=True)
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error during student check-in")
        return jsonify(error="Attendance could not be saved. Please try again."), 500
    return jsonify(status="Present", newly_marked=True)


def student_attendance_summary(student: Student, today: date) -> dict[str, Any]:
    _ensure_sessions_through(today)
    _finalize_ended_sessions(local_now())
    schedules = ClassSchedule.query.filter_by(is_active=True).all()
    student_schedules = [
        schedule for schedule in schedules if _schedule_matches_student(schedule, student)
    ]
    schedule_ids = [schedule.id for schedule in student_schedules]
    sessions = (
        ClassSession.query.filter(
            ClassSession.schedule_id.in_(schedule_ids),
            ClassSession.class_date >= local_date_from_utc(student.created_at),
            ClassSession.class_date <= today,
            ClassSession.ends_at <= local_now(),
        )
        .order_by(ClassSession.class_date.desc(), ClassSession.starts_at.desc())
        .all()
        if schedule_ids
        else []
    )
    session_ids = [class_session.id for class_session in sessions]
    attendance_records = (
        Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.session_id.in_(session_ids),
        )
        .all()
        if session_ids
        else []
    )
    present_days = sum(record.status == "Present" for record in attendance_records)
    attendance_by_session = {
        record.session_id: record for record in attendance_records
    }
    subject_stats: dict[int | None, dict[str, Any]] = {}
    for schedule in student_schedules:
        subject_id = schedule.subject_id
        subject_stats.setdefault(
            subject_id,
            {
                "name": schedule.subject.name if schedule.subject is not None else "Subject not assigned",
                "scheduled_classes": 0,
                "present_classes": 0,
            },
        )
    schedules_by_id = {schedule.id: schedule for schedule in student_schedules}
    for class_session in sessions:
        schedule = schedules_by_id[class_session.schedule_id]
        stats = subject_stats[schedule.subject_id]
        stats["scheduled_classes"] += 1
        record = attendance_by_session.get(class_session.id)
        if record is not None and record.status == "Present":
            stats["present_classes"] += 1
    subject_summaries = [
        {
            **stats,
            "attendance_percent": round(
                stats["present_classes"] * 100 / stats["scheduled_classes"]
            )
            if stats["scheduled_classes"]
            else 0,
        }
        for stats in sorted(subject_stats.values(), key=lambda item: item["name"].casefold())
    ]
    recent_records = (
        Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.date <= today,
        )
        .order_by(Attendance.date.desc(), Attendance.time.desc())
        .limit(10)
        .all()
    )
    today_records = [
        record
        for record in Attendance.query.filter_by(student_id=student.id, date=today).all()
        if record.status == "Present"
    ]
    todays_sessions = (
        ClassSession.query.filter(
            ClassSession.schedule_id.in_(schedule_ids),
            ClassSession.class_date == today,
        )
        .order_by(ClassSession.starts_at)
        .all()
        if schedule_ids
        else []
    )
    today_records_by_session = {
        record.session_id: record
        for record in Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.session_id.in_([item.id for item in todays_sessions]),
        ).all()
    } if todays_sessions else {}
    return {
        "class_days": len(sessions),
        "present_days": present_days,
        "subject_summaries": subject_summaries,
        "recent_records": recent_records,
        "today_record": today_records[0] if today_records else None,
        "today_present_count": sum(
            record.status == "Present"
            for record in today_records_by_session.values()
        ),
        "today_completed_count": sum(
            class_session.ends_at <= local_now()
            for class_session in todays_sessions
        ),
        "today_sessions": [
            {
                "session": class_session,
                "record": today_records_by_session.get(class_session.id),
            }
            for class_session in todays_sessions
        ],
    }
