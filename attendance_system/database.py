"""SQLite helpers for students and attendance records."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

import pandas as pd


DATABASE_PATH = Path(__file__).with_name("attendance.db")


@contextmanager
def _connection() -> Generator[sqlite3.Connection, None, None]:
    """Open a database connection and close it when the operation is done."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db() -> None:
    """Create the tables if they do not exist yet."""
    with _connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roll_no TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                status TEXT NOT NULL,
                FOREIGN KEY (student_id) REFERENCES students (id),
                UNIQUE (student_id, date)
            )
            """
        )


def add_student(roll_no: str, name: str, email: str) -> int:
    """Add a student and return the new student's ID."""
    with _connection() as connection:
        cursor = connection.execute(
            "INSERT INTO students (roll_no, name, email) VALUES (?, ?, ?)",
            (roll_no, name, email),
        )
        return int(cursor.lastrowid)


def get_all_students() -> list[dict]:
    """Return all students as a list of dictionaries."""
    with _connection() as connection:
        rows = connection.execute(
            "SELECT id, roll_no, name, email, created_at "
            "FROM students ORDER BY roll_no"
        ).fetchall()
        return [dict(row) for row in rows]


def mark_attendance(
    student_id: int,
    attendance_date: Optional[str] = None,
    attendance_time: Optional[str] = None,
    status: str = "Present",
) -> bool:
    """Record attendance once per student per date; return False for duplicates."""
    now = datetime.now()
    attendance_date = attendance_date or now.strftime("%Y-%m-%d")
    if attendance_time is None:
        attendance_time = now.strftime("%H:%M:%S")

    with _connection() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO attendance (student_id, date, time, status)
            VALUES (?, ?, ?, ?)
            """,
            (student_id, attendance_date, attendance_time, status),
        )
        return cursor.rowcount == 1


def get_attendance_df(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """Return attendance records with student details as a pandas DataFrame."""
    query = """
        SELECT attendance.id, attendance.student_id, students.roll_no,
               students.name, attendance.date, attendance.time, attendance.status
        FROM attendance
        JOIN students ON students.id = attendance.student_id
    """
    conditions = []
    parameters = []

    if start_date:
        conditions.append("attendance.date >= ?")
        parameters.append(start_date)
    if end_date:
        conditions.append("attendance.date <= ?")
        parameters.append(end_date)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY attendance.date DESC, attendance.time DESC"

    with _connection() as connection:
        return pd.read_sql_query(query, connection, params=parameters)
