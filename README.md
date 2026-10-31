# Real-time Attendance System

A college exhibition project with two Python attendance applications:

- **SmartClass** — a Flask web app for teacher and student accounts, scheduled
  classes, geofenced student check-in, attendance review, and attendance
  analytics. Setup and usage instructions are in [smartclass/README.md](smartclass/README.md).
- **Attendance System** — a standalone OpenCV webcam attendance app with
  student enrollment, face recognition, and a Streamlit dashboard. Install its
  dependencies from [attendance_system/requirements.txt](attendance_system/requirements.txt).

## Windows quick start: SmartClass

```powershell
cd smartclass
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000/attendance/login>. For the complete setup, account,
and feature instructions, see [smartclass/README.md](smartclass/README.md).

## Privacy and local files

SQLite databases, environment files, Python environments, and saved face
encodings are excluded from Git. Do not commit real student information or
biometric data. SmartClass's built-in administrator defaults are for local
development only; configure credentials and a secret key using environment
variables before any deployment, and do not expose the development server to a
public network.
