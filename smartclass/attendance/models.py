"""Database models for students, attendance, and engagement snapshots."""

from datetime import date, datetime, timezone

from . import db


class Student(db.Model):
    """A student profile; face encodings may be stored, never face photos."""

    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)
    roll_no = db.Column(db.String(40), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), nullable=False)
    course = db.Column(db.String(120))
    branch = db.Column(db.String(120))
    semester = db.Column(db.Integer)
    parent_email = db.Column(db.String(254))
    face_encoding = db.Column(db.LargeBinary)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    attendance_records = db.relationship(
        "Attendance",
        back_populates="student",
        cascade="all, delete-orphan",
    )
    engagement_snapshots = db.relationship(
        "EngagementSnapshot",
        back_populates="student",
        cascade="all, delete-orphan",
    )


class Teacher(db.Model):
    """A teacher account that requires administrator approval before sign-in."""

    __tablename__ = "teachers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_approved = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    schedules = db.relationship("ClassSchedule", back_populates="teacher")


class Subject(db.Model):
    """A subject offered to a specific course, branch, and semester."""

    __tablename__ = "subjects"
    __table_args__ = (
        db.UniqueConstraint(
            "name", "course", "branch", "semester", name="uq_subject_cohort_name"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    course = db.Column(db.String(120), nullable=False)
    branch = db.Column(db.String(120), nullable=False)
    semester = db.Column(db.Integer, nullable=False)
    teacher_id = db.Column(
        db.Integer,
        db.ForeignKey("teachers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
    )

    schedules = db.relationship("ClassSchedule", back_populates="subject")


class ClassSchedule(db.Model):
    """A weekly class timetable entry and its classroom geofence."""

    __tablename__ = "class_schedules"

    id = db.Column(db.Integer, primary_key=True)
    course = db.Column(db.String(120), nullable=False)
    branch = db.Column(db.String(120), nullable=False)
    semester = db.Column(db.Integer, nullable=False)
    weekday = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    radius_m = db.Column(db.Integer, nullable=False, default=100)
    starts_on = db.Column(db.Date, nullable=False, default=date.today)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    subject_id = db.Column(
        db.Integer,
        db.ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    teacher_id = db.Column(
        db.Integer,
        db.ForeignKey("teachers.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    teacher = db.relationship("Teacher", back_populates="schedules")
    subject = db.relationship("Subject", back_populates="schedules")
    sessions = db.relationship(
        "ClassSession",
        back_populates="schedule",
        cascade="all, delete-orphan",
    )


class ClassSession(db.Model):
    """One date-specific occurrence of a recurring class schedule."""

    __tablename__ = "class_sessions"
    __table_args__ = (
        db.UniqueConstraint("schedule_id", "class_date", name="uq_session_schedule_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    schedule_id = db.Column(
        db.Integer,
        db.ForeignKey("class_schedules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    class_date = db.Column(db.Date, nullable=False, index=True)
    starts_at = db.Column(db.DateTime, nullable=False)
    ends_at = db.Column(db.DateTime, nullable=False)

    schedule = db.relationship("ClassSchedule", back_populates="sessions")
    attendance_records = db.relationship("Attendance", back_populates="class_session")


class Attendance(db.Model):
    """One student's attendance status for a date or scheduled class session."""

    __tablename__ = "attendance"
    __table_args__ = (
        db.UniqueConstraint(
            "session_id", "student_id", name="uq_attendance_session_student"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("class_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    time = db.Column(db.Time, nullable=True, default=lambda: datetime.now().time())
    status = db.Column(db.String(20), nullable=False, default="Present")
    source = db.Column(db.String(20), nullable=False, default="legacy")
    marked_by_teacher_id = db.Column(
        db.Integer,
        db.ForeignKey("teachers.id", ondelete="SET NULL"),
        nullable=True,
    )

    student = db.relationship("Student", back_populates="attendance_records")
    class_session = db.relationship("ClassSession", back_populates="attendance_records")


class EngagementSnapshot(db.Model):
    """A periodic engagement summary for a student."""

    __tablename__ = "engagement_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(
        db.Integer,
        db.ForeignKey("students.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    captured_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    engagement_percent = db.Column(db.Float, nullable=False)
    attentive_count = db.Column(db.Integer, nullable=False, default=0)
    drowsy_count = db.Column(db.Integer, nullable=False, default=0)
    distracted_count = db.Column(db.Integer, nullable=False, default=0)

    student = db.relationship("Student", back_populates="engagement_snapshots")
