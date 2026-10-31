"""Standalone Flask application for SmartClass."""

import os
from pathlib import Path

from flask import Flask, redirect, url_for
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

from attendance import attendance, db


def create_app(test_config: dict | None = None) -> Flask:
    """Create and configure the SmartClass web application or a test instance."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get(
            "SMARTCLASS_SECRET_KEY", "dev-only-change-this-secret-key"
        ),
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{Path(app.instance_path) / 'smartclass.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        ADMIN_USERNAME=os.environ.get("SMARTCLASS_ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD_HASH=generate_password_hash(
            os.environ.get("SMARTCLASS_ADMIN_PASSWORD", "admin123")
        ),
        MAX_CONTENT_LENGTH=3 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

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
        if "session_id" not in attendance_columns:
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

    from attendance.classroom import start_attendance_maintenance

    start_attendance_maintenance(app)

    @app.get("/")
    def index():
        return redirect(url_for("attendance.dashboard"))

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="127.0.0.1", port=5000, debug=False)
