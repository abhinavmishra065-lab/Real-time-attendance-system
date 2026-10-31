"""Streamlit dashboard for classroom attendance analytics."""

import calendar
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from database import get_all_students, get_attendance_df, init_db


AT_RISK_THRESHOLD = 75.0
WINDOW_DAYS = 30
ATTENDED_STATUSES = ("Present", "Late")


def _weekdays(start: date, end: date) -> list[date]:
    """Return weekdays in the inclusive date range."""
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _attendance_matrix(
    students: list[dict[str, Any]],
    attendance: pd.DataFrame,
    days: list[date],
) -> pd.DataFrame:
    """Build a student-by-day matrix, treating unrecorded weekdays as absent."""
    student_ids = [student["id"] for student in students]
    day_strings = [day.isoformat() for day in days]
    matrix = pd.DataFrame(False, index=student_ids, columns=day_strings)
    if not attendance.empty and day_strings:
        attended = attendance[
            attendance["status"].isin(ATTENDED_STATUSES)
            & attendance["student_id"].isin(student_ids)
            & attendance["date"].isin(day_strings)
        ]
        for row in attended.itertuples(index=False):
            matrix.at[row.student_id, row.date] = True
    return matrix


def _attendance_percentages(matrix: pd.DataFrame) -> pd.Series:
    if matrix.empty or matrix.shape[1] == 0:
        return pd.Series(0.0, index=matrix.index, dtype=float)
    return matrix.sum(axis=1).div(matrix.shape[1]).mul(100)


def _month_end_projection(
    student_id: int,
    month_days: list[date],
    month_matrix: pd.DataFrame,
    today: date,
) -> float:
    """Project the student's cumulative weekday attendance rate to month end."""
    elapsed_days = [day for day in month_days if day <= today]
    if not elapsed_days:
        return 0.0

    daily = month_matrix.loc[student_id, [day.isoformat() for day in elapsed_days]]
    cumulative_rate = (
        daily.astype(float).cumsum().to_numpy()
        / np.arange(1, len(daily) + 1)
        * 100
    )
    if len(cumulative_rate) < 2:
        return float(cumulative_rate[-1])

    projected = _fit_projection(
        list(range(1, len(cumulative_rate) + 1)),
        cumulative_rate.tolist(),
        len(month_days),
    )
    return max(0.0, min(100.0, projected))


def _fit_projection(x_values: list[int], y_values: list[float], target_x: int) -> float:
    """Use a simple least-squares line to estimate attendance at month end."""
    slope, intercept = np.polyfit(x_values, y_values, 1)
    return float(slope * target_x + intercept)


def _calendar_table(
    selected_month: date,
    student_id: int,
    attendance: pd.DataFrame,
) -> tuple[list[list[int]], dict[str, str]]:
    month_grid = calendar.monthcalendar(selected_month.year, selected_month.month)
    statuses = {}
    student_rows = attendance[attendance["student_id"] == student_id]
    for row in student_rows.itertuples(index=False):
        statuses[row.date] = row.status
    return month_grid, statuses


def _render_calendar(
    selected_month: date,
    student_id: int,
    attendance: pd.DataFrame,
) -> None:
    month_grid, statuses = _calendar_table(selected_month, student_id, attendance)
    st.caption("Present, Late, Absent; weekends and future dates have no class.")
    headers = st.columns(7)
    for column, weekday in zip(headers, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        column.markdown(f"**{weekday}**")

    for week in month_grid:
        columns = st.columns(7)
        for column, day_number in zip(columns, week):
            if not day_number:
                column.markdown("&nbsp;")
                continue
            current_day = date(selected_month.year, selected_month.month, day_number)
            if current_day.weekday() >= 5 or current_day > date.today():
                label, color = "—", "#64748b"
            else:
                status = statuses.get(current_day.isoformat(), "Absent")
                label = {
                    "Present": "P",
                    "Late": "L",
                    "Absent": "A",
                }.get(status, "?")
                color = {
                    "Present": "#15803d",
                    "Late": "#b45309",
                    "Absent": "#b91c1c",
                }.get(status, "#475569")
            column.markdown(
                f"<div style='padding:8px 4px;text-align:center;border-radius:8px;"
                f"background:#f1f5f9;color:{color};font-weight:600'>"
                f"{day_number}<br>{label}</div>",
                unsafe_allow_html=True,
            )


def _render_overview(
    students: list[dict[str, Any]],
    attendance: pd.DataFrame,
    today: date,
    recent_matrix: pd.DataFrame,
) -> None:
    today_rows = attendance[
        (attendance["date"] == today.isoformat())
        & attendance["status"].isin(ATTENDED_STATUSES)
    ] if not attendance.empty else attendance
    present_today = int(today_rows["student_id"].nunique())
    attendance_percent = present_today / len(students) * 100 if students else 0.0
    recent_percentages = _attendance_percentages(recent_matrix)
    at_risk_count = int((recent_percentages < AT_RISK_THRESHOLD).sum())

    st.subheader("Classroom at a glance")
    cards = st.columns(4)
    cards[0].metric("Total students", len(students))
    cards[1].metric("Present today", present_today)
    cards[2].metric("Today's attendance", f"{attendance_percent:.1f}%")
    cards[3].metric("At-risk students", at_risk_count)

    if not students:
        st.info("Enroll students or add demo data to see attendance analytics.")
        return
    st.caption(
        f"Attendance risk is measured across the last {WINDOW_DAYS} calendar days, "
        "excluding weekends. Present and Late count as attended."
    )


def _render_trends(
    students: list[dict[str, Any]],
    recent_days: list[date],
    recent_matrix: pd.DataFrame,
) -> None:
    st.subheader("Attendance trends")
    if not students or not recent_days:
        st.info("There is not enough student or weekday data to show trends.")
        return

    daily_percentages = recent_matrix.mean(axis=0).mul(100)
    line_data = pd.DataFrame(
        {
            "Date": [date.fromisoformat(day) for day in daily_percentages.index],
            "Attendance": daily_percentages.values,
        }
    )
    line_chart = px.line(
        line_data,
        x="Date",
        y="Attendance",
        markers=True,
        title="Daily attendance percentage",
        labels={"Attendance": "Attendance (%)"},
    )
    line_chart.update_layout(yaxis_range=[0, 100], margin=dict(l=20, r=20, t=55, b=20))
    st.plotly_chart(line_chart, width="stretch")

    percentages = _attendance_percentages(recent_matrix)
    bar_data = pd.DataFrame(
        {
            "Student": [
                f"{student['name']} ({student['roll_no']})"
                for student in students
            ],
            "Attendance": [percentages.get(student["id"], 0.0) for student in students],
        }
    ).sort_values("Attendance")
    bar_chart = px.bar(
        bar_data,
        x="Attendance",
        y="Student",
        orientation="h",
        title="Attendance by student (last 30 days)",
        labels={"Attendance": "Attendance (%)"},
        color="Attendance",
        color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
        range_color=[0, 100],
    )
    bar_chart.update_layout(
        xaxis_range=[0, 100], coloraxis_showscale=False,
        margin=dict(l=20, r=20, t=55, b=20),
    )
    st.plotly_chart(bar_chart, width="stretch")


def _render_at_risk(
    students: list[dict[str, Any]],
    attendance: pd.DataFrame,
    recent_matrix: pd.DataFrame,
    today: date,
) -> None:
    st.subheader("Students who may need support")
    if not students:
        st.info("No students are enrolled yet.")
        return

    recent_percentages = _attendance_percentages(recent_matrix)
    at_risk = [
        student for student in students
        if recent_percentages.get(student["id"], 0.0) < AT_RISK_THRESHOLD
    ]
    if not at_risk:
        st.success("No students are currently below the 75% attendance threshold.")
        return

    month_start = today.replace(day=1)
    month_days = _weekdays(month_start, date(today.year, today.month, calendar.monthrange(today.year, today.month)[1]))
    month_matrix = _attendance_matrix(students, attendance, month_days)
    rows = []
    for student in at_risk:
        current = float(recent_percentages.get(student["id"], 0.0))
        projected = _month_end_projection(
            student["id"], month_days, month_matrix, today
        )
        rows.append(
            {
                "Roll number": student["roll_no"],
                "Student": student["name"],
                "Attendance (30 days)": current,
                "Predicted month-end %": projected,
                "Warning": "⚠ Will fall short" if projected < AT_RISK_THRESHOLD else "On track",
            }
        )

    risk_df = pd.DataFrame(rows).sort_values("Attendance (30 days)")
    styled = risk_df.style.apply(
        lambda row: [
            "background-color: #fee2e2; color: #991b1b"
            if row["Attendance (30 days)"] < AT_RISK_THRESHOLD
            else ""
            for _ in row
        ],
        axis=1,
    ).format(
        {
            "Attendance (30 days)": "{:.1f}%",
            "Predicted month-end %": "{:.1f}%",
        }
    )
    st.dataframe(styled, width="stretch", hide_index=True)


def _render_student_search(
    students: list[dict[str, Any]],
    attendance: pd.DataFrame,
    today: date,
) -> None:
    st.subheader("Student attendance")
    if not students:
        st.info("No students are enrolled yet.")
        return

    student_by_label = {
        f"{student['name']} ({student['roll_no']})": student for student in students
    }
    selected_label = st.selectbox("Choose a student", list(student_by_label))
    selected_student = student_by_label[selected_label]
    month_choice = st.date_input(
        "Calendar month",
        value=today.replace(day=1),
        key="student_calendar_month",
    )
    selected_month = month_choice.replace(day=1)
    month_start = selected_month
    month_end = min(
        today,
        date(
            selected_month.year,
            selected_month.month,
            calendar.monthrange(selected_month.year, selected_month.month)[1],
        ),
    )
    month_days = _weekdays(month_start, month_end) if month_start <= today else []
    month_matrix = _attendance_matrix(students, attendance, month_days)
    selected_id = selected_student["id"]
    percentage = (
        float(_attendance_percentages(month_matrix).get(selected_id, 0.0))
        if month_days
        else 0.0
    )
    st.metric(f"Attendance in {selected_month:%B %Y}", f"{percentage:.1f}%")
    _render_calendar(selected_month, selected_id, attendance)


def _render_export(attendance: pd.DataFrame) -> None:
    st.subheader("Export attendance report")
    st.caption("Download the attendance records currently in the database.")
    csv_data = attendance.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download attendance CSV",
        data=csv_data,
        file_name=f"attendance_report_{date.today().isoformat()}.csv",
        mime="text/csv",
        width="stretch",
    )
    st.dataframe(attendance, width="stretch", hide_index=True)


def _render_selected_view(view: str) -> None:
    today = date.today()
    students = get_all_students()
    attendance = get_attendance_df()
    recent_days = _weekdays(today - timedelta(days=WINDOW_DAYS - 1), today)
    recent_matrix = _attendance_matrix(students, attendance, recent_days)

    if view == "Overview":
        _render_overview(students, attendance, today, recent_matrix)
    elif view == "Trends":
        _render_trends(students, recent_days, recent_matrix)
    elif view == "At-Risk Students":
        _render_at_risk(students, attendance, recent_matrix, today)
    elif view == "Student Search":
        _render_student_search(students, attendance, today)
    else:
        _render_export(attendance)


def main() -> None:
    st.set_page_config(
        page_title="Classroom Attendance",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        .block-container {padding-top: 2rem; padding-bottom: 2rem;}
        [data-testid="stMetric"] {
            background: linear-gradient(135deg, #f8fafc, #eef2ff);
            border: 1px solid #e2e8f0;
            padding: 1rem;
            border-radius: 14px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    init_db()

    st.sidebar.title("Classroom Analytics")
    selected_view = st.sidebar.radio(
        "Navigate",
        ("Overview", "Trends", "At-Risk Students", "Student Search", "Export"),
    )
    st.sidebar.divider()
    auto_refresh = st.sidebar.checkbox("Auto-refresh dashboard", value=False)
    refresh_seconds = st.sidebar.selectbox(
        "Refresh interval",
        (5, 10, 30, 60),
        index=1,
        format_func=lambda seconds: f"Every {seconds} seconds",
        disabled=not auto_refresh,
    )
    if auto_refresh:
        st.sidebar.caption("Live updates are useful while recognition is running.")

    st.title("Smart Classroom Attendance")
    st.caption("Attendance insights, trends, and early support indicators.")

    content = st.fragment(
        run_every=refresh_seconds if auto_refresh else None
    )(_render_selected_view)
    content(selected_view)


if __name__ == "__main__":
    main()
