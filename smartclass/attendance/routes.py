"""Routes for the phase-one login and dashboard shell."""

import hmac
import re
import secrets
import threading
import time
from functools import wraps
from typing import Any, Callable, TypeVar

import numpy as np
from flask import (
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from . import attendance, db
from .face_utils import (
    ENCODING_LENGTH,
    EnrollmentImageError,
    decode_face_sample,
    invalidate_encoding_cache,
    get_known_encodings,
    recognize_faces,
)
from .engagement import BlinkTracker
from .models import Attendance, Student, Teacher
from .time_utils import local_now, local_today

ViewFunction = TypeVar("ViewFunction", bound=Callable[..., Any])
ENROLLMENT_SAMPLE_COUNT = 10
ENROLLMENT_SESSION_TIMEOUT = 15 * 60
LIVENESS_SESSION_TIMEOUT = 60 * 60
_enrollment_lock = threading.Lock()
_liveness_lock = threading.Lock()


def _enrollment_staging() -> dict[str, dict[str, Any]]:
    return current_app.extensions.setdefault("enrollment_staging", {})


def _liveness_sessions() -> dict[str, dict[str, Any]]:
    return current_app.extensions.setdefault("liveness_sessions", {})


def _csrf_token_from_json() -> bool:
    payload = request.get_json(silent=True)
    submitted = payload.get("csrf_token", "") if isinstance(payload, dict) else ""
    saved = session.get("_csrf_token", "")
    return bool(submitted and saved and hmac.compare_digest(submitted, saved))


def _prune_enrollment_staging(staging: dict[str, dict[str, Any]]) -> None:
    expire_before = time.monotonic() - ENROLLMENT_SESSION_TIMEOUT
    for token in [
        key for key, value in staging.items() if value["updated_at"] < expire_before
    ]:
        staging.pop(token, None)


def login_required(view: ViewFunction) -> ViewFunction:
    """Redirect unauthenticated visitors to the login page."""
    @wraps(view)
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        if "teacher_username" not in session:
            return redirect(url_for("attendance.login"))
        teacher_id = session.get("teacher_id")
        if teacher_id is not None:
            teacher = db.session.get(Teacher, teacher_id)
            if teacher is None or not teacher.is_approved:
                session.clear()
                return redirect(url_for("attendance.login"))
            g.teacher = teacher
            g.teacher_is_admin = False
        elif session.get("teacher_is_admin") or hmac.compare_digest(
            session.get("teacher_username", ""),
            current_app.config["ADMIN_USERNAME"],
        ):
            g.teacher = None
            g.teacher_is_admin = True
        else:
            session.clear()
            return redirect(url_for("attendance.login"))
        return view(*args, **kwargs)

    return wrapped_view  # type: ignore[return-value]


def admin_required(view: ViewFunction) -> ViewFunction:
    """Restrict teacher-account administration to the configured administrator."""
    @wraps(view)
    @login_required
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        if not g.teacher_is_admin:
            flash("Administrator access is required for that page.", "error")
            return redirect(url_for("attendance.dashboard"))
        return view(*args, **kwargs)

    return wrapped_view  # type: ignore[return-value]


def student_login_required(view: ViewFunction) -> ViewFunction:
    """Keep student pages separate from the teacher session."""
    @wraps(view)
    def wrapped_view(*args: Any, **kwargs: Any) -> Any:
        student_id = session.get("student_id")
        if student_id is None:
            return redirect(url_for("attendance.student_login"))
        student = db.session.get(Student, student_id)
        if student is None:
            session.pop("student_id", None)
            return redirect(url_for("attendance.student_login"))
        g.student = student
        return view(*args, **kwargs)

    return wrapped_view  # type: ignore[return-value]


def _csrf_token() -> str:
    token = session.get("_csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _valid_csrf_token() -> bool:
    submitted = request.form.get("csrf_token", "")
    saved = session.get("_csrf_token", "")
    return bool(submitted and saved and hmac.compare_digest(submitted, saved))


@attendance.route("/login", methods=("GET", "POST"))
def login():
    if "teacher_username" in session:
        return redirect(url_for("attendance.dashboard"))

    if request.method == "POST":
        if not _valid_csrf_token():
            flash("Your session expired. Please try again.", "error")
            return render_template("login.html", csrf_token=_csrf_token()), 400

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        is_admin = hmac.compare_digest(
            username.casefold(), current_app.config["ADMIN_USERNAME"].casefold()
        ) and check_password_hash(
            current_app.config["ADMIN_PASSWORD_HASH"], password
        )
        teacher = Teacher.query.filter(
            func.lower(Teacher.username) == username.casefold()
        ).first()
        if is_admin:
            session.clear()
            session["teacher_username"] = current_app.config["ADMIN_USERNAME"]
            session["teacher_is_admin"] = True
            _csrf_token()
            flash("Welcome to the SmartClass administrator workspace.", "success")
            return redirect(url_for("attendance.dashboard"))
        if teacher is not None and check_password_hash(
            teacher.password_hash, password
        ):
            if not teacher.is_approved:
                flash("Your teacher account is awaiting administrator approval.", "error")
            else:
                session.clear()
                session["teacher_id"] = teacher.id
                session["teacher_username"] = teacher.username
                session["teacher_name"] = teacher.name
                session["teacher_is_admin"] = False
                _csrf_token()
                flash(f"Welcome to SmartClass, {teacher.name}.", "success")
                return redirect(url_for("attendance.dashboard"))
        else:
            flash("Username or password is incorrect.", "error")

    return render_template("login.html", csrf_token=_csrf_token())


@attendance.route("/teacher/register", methods=("GET", "POST"))
def teacher_register():
    if request.method == "POST":
        if not _valid_csrf_token():
            flash("Your session expired. Please try again.", "error")
            return render_template("teacher_register.html", csrf_token=_csrf_token()), 400

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().casefold()
        username = request.form.get("username", "").strip().casefold()
        password = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        if not name or not email or not username or not password:
            flash("Complete all fields to request a teacher account.", "error")
        elif len(name) > 120 or len(email) > 254 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email
        ):
            flash("Enter a valid name and email address.", "error")
        elif not re.fullmatch(r"[a-z0-9_.-]{3,40}", username):
            flash("Username must be 3–40 characters using letters, numbers, dots, underscores, or hyphens.", "error")
        elif len(password) < 10:
            flash("Choose a password with at least 10 characters.", "error")
        elif password != password_confirm:
            flash("The passwords do not match.", "error")
        elif (
            Teacher.query.filter(func.lower(Teacher.username) == username).first()
            is not None
            or Teacher.query.filter(func.lower(Teacher.email) == email).first()
            is not None
            or hmac.compare_digest(
                username, current_app.config["ADMIN_USERNAME"].casefold()
            )
        ):
            flash("That username or email is already in use.", "error")
        else:
            teacher = Teacher()
            teacher.name = name
            teacher.email = email
            teacher.username = username
            teacher.password_hash = generate_password_hash(password)
            db.session.add(teacher)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("That username or email is already in use.", "error")
            except SQLAlchemyError:
                db.session.rollback()
                current_app.logger.exception("Database error during teacher registration")
                flash("Your registration could not be saved. Please try again.", "error")
            else:
                flash(
                    "Your account request was sent. An administrator must approve it before you can sign in.",
                    "success",
                )
                return redirect(url_for("attendance.login"))

    return render_template("teacher_register.html", csrf_token=_csrf_token())


@attendance.get("/teacher-accounts")
@admin_required
def teacher_accounts():
    pending_teachers = Teacher.query.filter_by(is_approved=False).order_by(
        Teacher.created_at
    ).all()
    approved_teachers = Teacher.query.filter_by(is_approved=True).order_by(
        Teacher.name
    ).all()
    return render_template(
        "teacher_accounts.html",
        pending_teachers=pending_teachers,
        approved_teachers=approved_teachers,
        csrf_token=_csrf_token(),
    )


@attendance.post("/teacher-accounts/<int:teacher_id>/approve")
@admin_required
def approve_teacher(teacher_id: int):
    if not _valid_csrf_token():
        flash("Your session expired. Please try again.", "error")
        return redirect(url_for("attendance.teacher_accounts"))
    teacher = db.session.get(Teacher, teacher_id)
    if teacher is None:
        flash("That teacher account no longer exists.", "error")
    else:
        teacher.is_approved = True
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Database error while approving teacher")
            flash("The teacher account could not be approved.", "error")
        else:
            flash(f"{teacher.name}'s account is approved.", "success")
    return redirect(url_for("attendance.teacher_accounts"))


@attendance.post("/teacher-accounts/<int:teacher_id>/reject")
@admin_required
def reject_teacher(teacher_id: int):
    if not _valid_csrf_token():
        flash("Your session expired. Please try again.", "error")
        return redirect(url_for("attendance.teacher_accounts"))
    teacher = db.session.get(Teacher, teacher_id)
    if teacher is None or teacher.is_approved:
        flash("That pending teacher request no longer exists.", "error")
    else:
        db.session.delete(teacher)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Database error while rejecting teacher")
            flash("The teacher request could not be rejected.", "error")
        else:
            flash("The teacher registration request was rejected.", "success")
    return redirect(url_for("attendance.teacher_accounts"))


@attendance.post("/logout")
@login_required
def logout():
    if not _valid_csrf_token():
        flash("Your session expired. Please sign in again.", "error")
        return redirect(url_for("attendance.login"))

    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("attendance.login"))


@attendance.get("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


@attendance.route("/student", methods=("GET", "POST"))
def student_login():
    if session.get("student_id") is not None:
        student = db.session.get(Student, session["student_id"])
        if student is not None:
            return redirect(url_for("attendance.student_dashboard"))
        session.pop("student_id", None)

    if request.method == "POST":
        if not _valid_csrf_token():
            flash("Your session expired. Please try again.", "error")
            return render_template("student_login.html", csrf_token=_csrf_token()), 400

        roll_no = request.form.get("roll_no", "").strip()
        email = request.form.get("email", "").strip().casefold()
        student = Student.query.filter(
            func.lower(Student.roll_no) == roll_no.casefold(),
            func.lower(Student.email) == email,
        ).first()

        if student is not None:
            session.clear()
            session["student_id"] = student.id
            _csrf_token()
            return redirect(url_for("attendance.student_dashboard"))

        flash("Those details did not match a student record.", "error")

    return render_template("student_login.html", csrf_token=_csrf_token())


@attendance.get("/student/attendance")
@student_login_required
def student_dashboard():
    student = g.student
    today = local_today()
    from .classroom import student_attendance_summary

    summary = student_attendance_summary(student, today)
    class_days = summary["class_days"]
    present_days = summary["present_days"]
    attendance_percent = (
        round(present_days * 100 / class_days) if class_days else 0
    )
    return render_template(
        "student_dashboard.html",
        student=student,
        today=today,
        now=local_now(),
        today_record=summary["today_record"],
        today_sessions=summary["today_sessions"],
        today_present_count=summary["today_present_count"],
        today_completed_count=summary["today_completed_count"],
        present_days=present_days,
        class_days=class_days,
        attendance_percent=attendance_percent,
        subject_summaries=summary["subject_summaries"],
        recent_records=summary["recent_records"],
    )


@attendance.post("/student/logout")
@student_login_required
def student_logout():
    if not _valid_csrf_token():
        flash("Your session expired. Please sign in again.", "error")
        return redirect(url_for("attendance.student_login"))
    session.clear()
    return redirect(url_for("attendance.student_login"))


@attendance.get("/enroll")
@login_required
def enroll():
    if "enrollment_token" not in session:
        session["enrollment_token"] = secrets.token_urlsafe(32)
    return render_template(
        "enroll.html",
        students=Student.query.order_by(Student.roll_no).all(),
        csrf_token=_csrf_token(),
        enrollment_token=session["enrollment_token"],
        required_samples=ENROLLMENT_SAMPLE_COUNT,
    )


@attendance.get("/live")
@login_required
def live_attendance():
    enrolled_count = Student.query.filter(Student.face_encoding.isnot(None)).count()
    token = secrets.token_urlsafe(32)
    session["live_token"] = token
    return render_template(
        "live.html",
        csrf_token=_csrf_token(),
        frame_url=url_for("attendance.live_attendance_frame"),
        enrolled_count=enrolled_count,
        live_token=token,
    )


@attendance.post("/api/live/frame")
@login_required
def live_attendance_frame():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not _csrf_token_from_json():
        return jsonify(error="Your session expired. Reload the live attendance page."), 400

    try:
        face_results = recognize_faces(
            payload.get("frame"),
            get_known_encodings(),
        )
        student_ids = {
            student_id
            for _, _, _, _, student_id, _ in face_results
            if student_id is not None
        }
        students = {
            student.id: student
            for student in Student.query.filter(Student.id.in_(student_ids)).all()
        } if student_ids else {}
        today = local_today()
        token = session.get("live_token")
        if not token:
            return jsonify(error="Reload the live attendance page to start verification."), 400
        now = time.monotonic()
        with _liveness_lock:
            liveness_sessions = _liveness_sessions()
            expire_before = now - LIVENESS_SESSION_TIMEOUT
            for expired_token in [
                key
                for key, value in liveness_sessions.items()
                if value["updated_at"] < expire_before
            ]:
                liveness_sessions.pop(expired_token, None)
            liveness_state = liveness_sessions.setdefault(
                token, {"students": {}, "updated_at": now}
            )
            liveness_state["updated_at"] = now

        present_ids = {
            student_id
            for (student_id,) in db.session.query(Attendance.student_id)
            .filter(
                Attendance.date == today,
                Attendance.status == "Present",
                Attendance.session_id.is_(None),
            )
            .all()
        }
        seen_student_ids: set[int] = set()
        faces = []

        for top, right, bottom, left, student_id, ear in face_results:
            student = students.get(student_id) if student_id is not None else None
            newly_marked = False
            already_marked = False
            blink_verified = False
            if student is not None:
                tracker = liveness_state["students"].setdefault(
                    student.id, BlinkTracker()
                )
                blink_verified = tracker.update(ear, now)
            if student is not None and student.id not in seen_student_ids:
                seen_student_ids.add(student.id)
                if student.id not in present_ids:
                    if blink_verified:
                        attendance_record = Attendance()
                        attendance_record.student_id = student.id
                        attendance_record.date = today
                        attendance_record.time = local_now().time()
                        attendance_record.status = "Present"
                        db.session.add(attendance_record)
                        try:
                            db.session.commit()
                        except IntegrityError:
                            db.session.rollback()
                            already_marked = True
                        except SQLAlchemyError:
                            db.session.rollback()
                            raise
                        else:
                            newly_marked = True
                        present_ids.add(student.id)
                else:
                    already_marked = True

            faces.append(
                {
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                    "left": left,
                    "name": student.name if student is not None else "Unknown",
                    "known": student is not None,
                    "newly_marked": newly_marked,
                    "already_marked": already_marked,
                    "blink_verified": blink_verified,
                    "please_blink": (
                        student is not None
                        and not already_marked
                        and not newly_marked
                        and not blink_verified
                    ),
                }
            )

        present_students = (
            db.session.query(Student.id, Student.name, Student.roll_no)
            .join(Attendance, Attendance.student_id == Student.id)
            .filter(
                Attendance.date == today,
                Attendance.status == "Present",
                Attendance.session_id.is_(None),
            )
            .order_by(Student.roll_no)
            .all()
        )
        return jsonify(
            faces=faces,
            present=[
                {"id": student_id, "name": name, "roll_no": roll_no}
                for student_id, name, roll_no in present_students
            ],
        )
    except EnrollmentImageError as error:
        return jsonify(error=str(error)), 400
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error during live attendance")
        return jsonify(error="Attendance could not be saved. Please try again."), 500
    except RuntimeError as error:
        current_app.logger.exception("Face recognition dependency or runtime error")
        return jsonify(error=str(error)), 503


@attendance.post("/api/enrollment/sample")
@login_required
def enrollment_sample():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not _csrf_token_from_json():
        return jsonify(error="Your session expired. Reload the enrollment page."), 400

    form_fields = (
        payload.get("roll_no", ""),
        payload.get("name", ""),
        payload.get("email", ""),
        payload.get("course", ""),
        payload.get("branch", ""),
        payload.get("semester", ""),
        payload.get("parent_email", ""),
    )
    if not all(isinstance(value, str) for value in form_fields):
        return jsonify(error="Student details must be text values."), 400
    roll_no, name, email, course, branch, semester_value, parent_email = (
        value.strip() for value in form_fields
    )
    token = session.get("enrollment_token")

    if not token:
        return jsonify(error="Reload the enrollment page to start a new session."), 400
    if not all((roll_no, name, email, course, branch, semester_value)):
        return jsonify(
            error="Roll number, name, email, course, branch, and semester are required."
        ), 400
    if len(roll_no) > 40 or len(name) > 120 or len(email) > 254 or (
        len(course) > 120 or len(branch) > 120 or len(parent_email) > 254
    ):
        return jsonify(error="One or more fields exceed the allowed length."), 400
    if not re.fullmatch(r"(?:[1-9]|1[0-2])", semester_value):
        return jsonify(error="Select a valid semester from 1 through 12."), 400
    email_pattern = r"[^@\s]+@[^@\s]+\.[^@\s]+"
    if not re.fullmatch(email_pattern, email) or (
        parent_email and not re.fullmatch(email_pattern, parent_email)
    ):
        return jsonify(error="Enter a valid student and parent email address."), 400

    try:
        if Student.query.filter_by(roll_no=roll_no).first() is not None:
            return jsonify(error=f"Roll number {roll_no} is already enrolled."), 409

        encoding = decode_face_sample(payload.get("frame"))
        with _enrollment_lock:
            staging = _enrollment_staging()
            _prune_enrollment_staging(staging)
            sample_state = staging.setdefault(
                token,
                {
                    "samples": [],
                    "student_data": (
                        roll_no,
                        name,
                        email,
                        course,
                        branch,
                        semester_value,
                        parent_email,
                    ),
                    "updated_at": time.monotonic(),
                },
            )
            if sample_state["student_data"] != (
                roll_no,
                name,
                email,
                course,
                branch,
                semester_value,
                parent_email,
            ):
                return jsonify(
                    error="Student details changed. Cancel and restart enrollment."
                ), 409
            sample_state["samples"].append(encoding)
            sample_state["updated_at"] = time.monotonic()
            sample_count = len(sample_state["samples"])

            if sample_count < ENROLLMENT_SAMPLE_COUNT:
                return jsonify(
                    captured=sample_count,
                    required=ENROLLMENT_SAMPLE_COUNT,
                    complete=False,
                )

            average_encoding = _average_encoding(sample_state["samples"])
            student = Student()
            student.roll_no = roll_no
            student.name = name
            student.email = email
            student.course = course
            student.branch = branch
            student.semester = int(semester_value)
            student.parent_email = parent_email or None
            student.face_encoding = average_encoding.tobytes()
            db.session.add(student)
            db.session.commit()
            staging.pop(token, None)
            invalidate_encoding_cache()

        session.pop("enrollment_token", None)
        return jsonify(
            captured=ENROLLMENT_SAMPLE_COUNT,
            required=ENROLLMENT_SAMPLE_COUNT,
            complete=True,
            message=f"{name} was enrolled successfully.",
        )
    except EnrollmentImageError as error:
        return jsonify(error=str(error)), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify(error="That roll number is already enrolled."), 409
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error while enrolling student")
        return jsonify(error="The student could not be saved. Please try again."), 500
    except RuntimeError as error:
        current_app.logger.exception("Face enrollment dependency or runtime error")
        return jsonify(error=str(error)), 503


def _average_encoding(samples: list[Any]) -> np.ndarray:
    """Average ten face encodings after validating their shared shape."""
    averaged = np.mean(np.stack(samples), axis=0)
    if averaged.shape != (ENCODING_LENGTH,) or not np.isfinite(averaged).all():
        raise ValueError("The captured face samples could not be averaged.")
    return averaged


@attendance.post("/api/enrollment/cancel")
@login_required
def cancel_enrollment():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not _csrf_token_from_json():
        return jsonify(error="Your session expired. Reload the enrollment page."), 400
    token = session.get("enrollment_token")
    if token:
        with _enrollment_lock:
            _enrollment_staging().pop(token, None)
    return jsonify(cancelled=True)


@attendance.post("/students/<int:student_id>/delete")
@login_required
def delete_student(student_id: int):
    if not _valid_csrf_token():
        flash("Your session expired. Please try again.", "error")
        return redirect(url_for("attendance.enroll"))

    student = db.session.get(Student, student_id)
    if student is None:
        flash("That student no longer exists.", "error")
        return redirect(url_for("attendance.enroll"))

    try:
        db.session.delete(student)
        db.session.commit()
        invalidate_encoding_cache()
        flash(f"{student.name} was removed.", "success")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error while deleting student")
        flash("The student could not be removed. Please try again.", "error")
    return redirect(url_for("attendance.enroll"))


@attendance.post("/students/<int:student_id>/academics")
@login_required
def update_student_academics(student_id: int):
    if not _valid_csrf_token():
        flash("Your session expired. Please try again.", "error")
        return redirect(url_for("attendance.enroll"))
    student = db.session.get(Student, student_id)
    if student is None:
        flash("That student no longer exists.", "error")
        return redirect(url_for("attendance.enroll"))
    course = request.form.get("course", "").strip()
    branch = request.form.get("branch", "").strip()
    semester_value = request.form.get("semester", "").strip()
    if (
        not course
        or not branch
        or len(course) > 120
        or len(branch) > 120
        or not re.fullmatch(r"(?:[1-9]|1[0-2])", semester_value)
    ):
        flash("Enter a course, branch, and semester from 1 through 12.", "error")
        return redirect(url_for("attendance.enroll"))
    student.course = course
    student.branch = branch
    student.semester = int(semester_value)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Database error while updating student academics")
        flash("The student's academic details could not be saved.", "error")
    else:
        flash(f"{student.name}'s academic details were saved.", "success")
    return redirect(url_for("attendance.enroll"))
