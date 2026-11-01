"""SmartClass Flask application factory and WSGI entry point."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, redirect, url_for
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

from attendance import attendance, db
from attendance.time_utils import DEFAULT_TIMEZONE


def create_app(test_config: dict | None = None) -> Flask:
    """Create and configure the SmartClass web application or a test instance."""
    is_vercel = os.environ.get("VERCEL") == "1"
    database_uri = os.environ.get("SMARTCLASS_DATABASE_URL")
    if is_vercel and (
        not database_uri or database_uri.startswith("sqlite:")
    ):
        raise RuntimeError(
            "Set SMARTCLASS_DATABASE_URL to a persistent PostgreSQL database "
            "before deploying SmartClass to Vercel."
        )
    if is_vercel:
        missing_settings = [
            name
            for name in (
                "SMARTCLASS_SECRET_KEY",
                "SMARTCLASS_ADMIN_USERNAME",
                "SMARTCLASS_ADMIN_PASSWORD",
            )
            if not os.environ.get(name)
        ]
        if missing_settings:
            raise RuntimeError(
                "Set the required Vercel environment variables: "
                + ", ".join(missing_settings)
            )
    if database_uri and database_uri.startswith("postgres://"):
        database_uri = database_uri.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_uri and database_uri.startswith("postgresql://"):
        database_uri = database_uri.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )

    app = Flask(
        __name__,
        instance_relative_config=True,
        static_folder="public/static",
        static_url_path="/static",
    )
    app.config.from_mapping(
        SECRET_KEY=os.environ.get(
            "SMARTCLASS_SECRET_KEY", "dev-only-change-this-secret-key"
        ),
        SQLALCHEMY_DATABASE_URI=database_uri
        or f"sqlite:///{Path(app.instance_path) / 'smartclass.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        ADMIN_USERNAME=os.environ.get("SMARTCLASS_ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD_HASH=generate_password_hash(
            os.environ.get("SMARTCLASS_ADMIN_PASSWORD", "admin123")
        ),
        MAX_CONTENT_LENGTH=3 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        DISABLE_ATTENDANCE_MAINTENANCE=is_vercel,
        TIMEZONE=os.environ.get("SMARTCLASS_TIMEZONE", DEFAULT_TIMEZONE),
    )
    if test_config:
        app.config.update(test_config)
    try:
        ZoneInfo(app.config["TIMEZONE"])
    except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
        raise RuntimeError(
            "Set TIMEZONE or SMARTCLASS_TIMEZONE to a valid IANA timezone."
        ) from error

    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:"):
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    app.register_blueprint(attendance, url_prefix="/attendance")

    with app.app_context():
        from attendance import models  # noqa: F401

        db.create_all()
        student_columns = {
            column["name"] for column in inspect(db.engine).get_columns("students")
        }
        for column_name, column_type in (
            ("course", "VARCHAR(120)"),
            ("branch", "VARCHAR(120)"),
            ("semester", "INTEGER"),
        ):
            if column_name not in student_columns:
                db.session.execute(
                    text(f"ALTER TABLE students ADD COLUMN {column_name} {column_type}")
                )
        schedule_columns = {
            column["name"] for column in inspect(db.engine).get_columns("class_schedules")
        }
        if "subject_id" not in schedule_columns:
            db.session.execute(
                text(
                    "ALTER TABLE class_schedules ADD COLUMN subject_id "
                    "INTEGER REFERENCES subjects (id) ON DELETE RESTRICT"
                )
            )
            db.session.execute(
                text("CREATE INDEX ix_class_schedules_subject_id ON class_schedules (subject_id)")
            )
        db.session.commit()

        attendance_columns = {
            column["name"] for column in inspect(db.engine).get_columns("attendance")
        }
        missing_attendance_columns = {
            "session_id",
            "source",
            "marked_by_teacher_id",
        } - attendance_columns
        if missing_attendance_columns and db.engine.dialect.name == "sqlite":
            connection = db.engine.raw_connection()
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=OFF")
                cursor.execute("BEGIN")
                cursor.execute(
                    """
                    CREATE TABLE attendance_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        student_id INTEGER NOT NULL,
                        session_id INTEGER,
                        date DATE NOT NULL,
                        time TIME,
                        status VARCHAR(20) NOT NULL,
                        source VARCHAR(20) NOT NULL DEFAULT 'legacy',
                        marked_by_teacher_id INTEGER,
                        FOREIGN KEY(student_id) REFERENCES students (id) ON DELETE CASCADE,
                        FOREIGN KEY(session_id) REFERENCES class_sessions (id) ON DELETE CASCADE,
                        FOREIGN KEY(marked_by_teacher_id) REFERENCES teachers (id) ON DELETE SET NULL,
                        CONSTRAINT uq_attendance_session_student UNIQUE (session_id, student_id)
                    )
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO attendance_new (id, student_id, date, time, status, source)
                    SELECT id, student_id, date, time, status, 'legacy' FROM attendance
                    """
                )
                cursor.execute("DROP TABLE attendance")
                cursor.execute("ALTER TABLE attendance_new RENAME TO attendance")
                cursor.execute(
                    "CREATE INDEX ix_attendance_student_id ON attendance (student_id)"
                )
                cursor.execute(
                    "CREATE INDEX ix_attendance_session_id ON attendance (session_id)"
                )
                cursor.execute(
                    "CREATE INDEX ix_attendance_date ON attendance (date)"
                )
                connection.commit()
            finally:
                if connection.in_transaction:
                    connection.rollback()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
                connection.close()
        elif missing_attendance_columns:
            if "session_id" in missing_attendance_columns:
                db.session.execute(
                    text(
                        "ALTER TABLE attendance ADD COLUMN session_id INTEGER "
                        "REFERENCES class_sessions (id) ON DELETE CASCADE"
                    )
                )
            if "source" in missing_attendance_columns:
                db.session.execute(
                    text(
                        "ALTER TABLE attendance ADD COLUMN source VARCHAR(20) "
                        "NOT NULL DEFAULT 'legacy'"
                    )
                )
            if "marked_by_teacher_id" in missing_attendance_columns:
                db.session.execute(
                    text(
                        "ALTER TABLE attendance ADD COLUMN marked_by_teacher_id "
                        "INTEGER REFERENCES teachers (id) ON DELETE SET NULL"
                    )
                )
            unique_constraints = {
                constraint.get("name")
                for constraint in inspect(db.engine).get_unique_constraints(
                    "attendance"
                )
            }
            if "uq_attendance_session_student" not in unique_constraints:
                db.session.execute(
                    text(
                        "ALTER TABLE attendance ADD CONSTRAINT "
                        "uq_attendance_session_student UNIQUE (session_id, student_id)"
                    )
                )
            for column_name in ("student_id", "session_id", "date"):
                db.session.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS ix_attendance_{column_name} "
                        f"ON attendance ({column_name})"
                    )
                )
            db.session.commit()

    from attendance.classroom import start_attendance_maintenance

    start_attendance_maintenance(app)

    @app.get("/")
    def index():
        return redirect(url_for("attendance.dashboard"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
