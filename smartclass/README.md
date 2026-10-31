# SmartClass

SmartClass is a college exhibition project for classroom attendance and engagement analytics. Phase 1 includes a Flask attendance blueprint, SQLAlchemy database models, teacher authentication, and the shared responsive layout. Phase 2 adds consent-based student enrollment using the browser webcam and server-side face encoding. The scheduled attendance flow supports weekly classes, geofenced student check-in, and teacher attendance review. Face/blink checks gate check-in; frames are processed live and not retained.

## Windows setup (Python 3.10 or 3.11)

Open the `smartclass` folder in VS Code, then use its integrated PowerShell terminal:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

If PowerShell blocks activation, run the interpreter and pip directly:

```powershell
py -3.11 -m pip install -r requirements.txt
py -3.11 app.py
```

Open <http://127.0.0.1:5000/attendance/login> and sign in with the local demo administrator account. The built-in demo credentials are for local development only.

The SQLite database is created automatically at `instance/smartclass.db`. The default password is hashed before it is checked. For a local demo, set optional environment variables before starting the app:

```powershell
$env:SMARTCLASS_SECRET_KEY = "replace-with-a-long-random-secret"
$env:SMARTCLASS_ADMIN_USERNAME = "admin"
$env:SMARTCLASS_ADMIN_PASSWORD = "choose-a-demo-password"
python app.py
```

The sign-in page and footer explain the face-data privacy policy: only face encodings are stored, not photos, and enrollment must be consent-based.

## Phase 1 smoke test

1. Start the server using `python app.py`.
2. Visit `/attendance/`; it should redirect to the sign-in page.
3. Try an incorrect password and confirm access is denied.
4. Sign in with the demo credentials and confirm the dashboard shell appears.
5. Sign out, then verify the dashboard redirects back to sign-in.

## Phase 2 enrollment test

1. Sign in and open **Enroll students** from the sidebar.
2. Allow webcam access. If the browser blocks it, use `http://127.0.0.1:5000` and allow camera access in the browser settings.
3. Enter a unique roll number, name, valid email, course, branch, and semester.
4. Capture ten samples with exactly one face visible each time. The page shows progress and the server rejects frames with zero or multiple faces.
5. After the tenth valid sample, confirm the student appears in the list. The averaged 128-value encoding is stored in SQLite; webcam images are not retained.
6. Delete the test student and confirm its database row and dependent records are removed.

The enrollment API processes only user-triggered captures and keeps partial encodings in memory for up to 15 minutes. A restart clears unfinished sample sessions.

## Legacy daily camera attendance

1. Sign in, enroll one or more students, then open **Live attendance** from the sidebar or dashboard.
2. Select **Start camera** and allow browser camera permission. Use a well-lit room and have an enrolled student face the camera.
3. Wait for the **Please blink** prompt, then blink. Confirm the student receives a green face box and appears in **Marked present**. Unknown faces receive a red box.
4. Keep a recognized face in view for several frames and confirm it is still listed only once for the day.
5. Select **Stop camera** to end the session. Refresh the page and verify today's list still shows the attendance entries.
6. To test duplicate prevention, show the same student again later that day; the database retains one attendance row for that student and date.

The browser sends a JPEG frame about every 250 ms to the server. Face matching runs at quarter-size with a 0.5 tolerance. Frames are processed in memory and are not stored. Browser camera access requires localhost or HTTPS.

## Phase 4 blink verification test

1. Install the updated requirements and restart the app so MediaPipe is available.
2. Open **Live attendance**, start the camera, and have an enrolled student look toward it.
3. Confirm the face label and status say **Please blink** and that no attendance is added yet.
4. Have the student blink naturally while facing the camera. The label should change and attendance should be marked once.
5. Keep the student in view after five seconds: their attendance stays recorded for the day, and no duplicate row is added.

The server estimates Eye Aspect Ratio (EAR) from MediaPipe Face Mesh landmarks. A closed-eye reading followed by an open-eye reading counts as a blink; the blink must be within the preceding five seconds to create a new attendance record. Frames are checked at roughly 250 ms intervals to avoid missing ordinary blinks. Liveness state is temporary and kept in server memory for the active page session.

## Teacher accounts

1. Select **Request an account** on the teacher sign-in page and submit your name, email, username, and a password of at least 10 characters.
2. Sign in with the configured administrator account. The built-in demo credentials are for local development only.
3. Open **Teachers**, then approve or reject pending teacher account requests.
4. An approved teacher can sign in with their own username and password. Passwords are stored as hashes.

## Scheduled classes and geofenced attendance

1. Enroll each student with their course, branch, and semester. Existing students can have those academic details filled in from the **Students** table without deleting their attendance.
2. Open **Timetable** as a teacher, create each subject for its course, branch, and semester, then schedule weekly classes under that subject.
3. Set the classroom center using the teacher device's current location or enter coordinates manually. The allowed radius defaults to 100 m and can be set from 25–500 m.
4. Students sign in to the student portal, select **Check in** during their class window, allow location and camera access, and look toward the camera and blink. The server verifies that the face matches their enrolled record and checks that reported location is inside the class geofence.
5. Teachers can open **View attendance** from the timetable to monitor the class and use the explicit **Mark present** or **Mark absent** action for each student.
6. At the scheduled end time, unmarked students are automatically marked Absent. A maintenance thread creates class occurrences and finalizes absences while the single-process demo server is running.
7. The student portal shows overall and subject-wise attendance percentages, along with present classes out of scheduled classes held since enrollment. Ongoing and future classes are not included in the denominator.
8. A teacher can delete a recurring class from its subject section. This permanently deletes its generated sessions and all attendance records for those sessions; legacy daily attendance records are not affected.

Camera frames and check-in coordinates are processed for the live request and are not saved. Attendance status, class date, and check-in time are stored. Indoor GPS accuracy can vary; check-in uses the reported coordinates against the teacher-configured radius rather than rejecting a device just because its accuracy estimate exceeds 100 m. Browser geolocation and blink checks are deterrents, not a guarantee against location spoofing or every form of proxy attendance. Browsers require localhost or HTTPS for camera/location access, so phones connecting over a local network need an HTTPS deployment.

The attendance maintenance thread is intended for the single-process local exhibition server. A multi-worker production deployment should use a single shared scheduler/worker rather than starting one maintenance thread per web worker.

## Student portal

1. Open **Student portal** on the teacher sign-in page or teacher dashboard.
2. Sign in with the student's enrolled roll number and student email address.
3. Review the course, branch, semester, today's class schedule/status, attendance percentage, and recent records. Ask your teacher to complete any missing course, branch, or semester details and add a matching weekly timetable entry if your classes do not appear.
4. During a scheduled class window, select **Check in** beside that class to start your own camera, blink, and geofence verification. The button appears only for a matching scheduled class while it is in session.
5. Sign out when finished.

The percentage uses completed sessions on the matching timetable since the student enrolled. Access is session-based, and each portal session is scoped to the matching student record. Since the exhibition login uses roll number plus email rather than a password, keep the demo on localhost and do not expose it as a public production service.

Existing SQLite databases are updated at startup to add course, branch, and semester columns, teacher account tables, and per-class attendance support without removing existing student or daily attendance records. Legacy attendance remains available in student history; the new percentage is calculated from scheduled classes.

The website uses a warm paper, forest, and clay palette with responsive student and teacher layouts. A light/dark toggle is available on signed-in pages.

On Windows, `face-recognition` depends on `dlib`; if pip cannot install it in your Python environment, use Python 3.10/3.11 and follow the package's Windows installation guidance. No face image is written to disk.

Later project phases will add analytics, engagement scores, and report features.
