"""Attendance blueprint and SQLAlchemy extension."""

from flask import Blueprint
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
attendance = Blueprint("attendance", __name__)

from . import routes  # noqa: E402,F401
from . import classroom  # noqa: E402,F401
