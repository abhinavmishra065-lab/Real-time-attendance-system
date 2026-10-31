# SmartClass Attendance System

SmartClass is a Flask web app for teacher and student accounts, scheduled
classes, geofenced student check-in, attendance review, and attendance
analytics. It is the only application in this repository.

## Windows setup (Python 3.10 or 3.11)

```powershell
cd smartclass
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open <http://127.0.0.1:5000/attendance/login>. For the complete setup and usage
instructions, see [smartclass/README.md](smartclass/README.md).

## Privacy and local files

SQLite databases, environment files, Python environments, and saved face
encodings are excluded from Git. Do not commit real student information or
biometric data. SmartClass's built-in administrator defaults are for local
development only; configure credentials and a secret key using environment
variables before any deployment, and do not expose the development server to a
public network.
