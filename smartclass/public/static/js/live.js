(() => {
  const page = document.getElementById("live-attendance");
  if (!page) return;

  const video = document.getElementById("live-video");
  const overlay = document.getElementById("face-overlay");
  const placeholder = document.getElementById("camera-placeholder");
  const indicator = document.getElementById("camera-indicator");
  const startButton = document.getElementById("start-camera");
  const stopButton = document.getElementById("stop-camera");
  const status = document.getElementById("live-status");
  const presentList = document.getElementById("present-list");
  const presentCount = document.getElementById("present-count");
  const toast = document.getElementById("attendance-toast");
  const enrolledCount = Number(page.dataset.enrolledCount);
  const canvas = document.createElement("canvas");
  const frameContext = canvas.getContext("2d", { willReadFrequently: true });
  const overlayContext = overlay.getContext("2d");
  let stream = null;
  let timer = null;
  let sendingFrame = false;
  let toastTimer = null;

  const setStatus = (message, isError = false) => {
    status.textContent = message;
    status.classList.toggle("text-red-600", isError);
    status.classList.toggle("dark:text-red-400", isError);
    status.classList.toggle("text-slate-500", !isError);
  };

  const setCameraState = (running) => {
    startButton.disabled = running || enrolledCount === 0;
    stopButton.disabled = !running;
    indicator.textContent = running ? "Camera running" : "Camera stopped";
    indicator.className = running
      ? "w-fit rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
      : "w-fit rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300";
  };

  const drawFaces = (faces) => {
    overlay.width = video.videoWidth;
    overlay.height = video.videoHeight;
    overlayContext.clearRect(0, 0, overlay.width, overlay.height);
    overlayContext.lineWidth = Math.max(2, overlay.width / 320);
    overlayContext.font = `${Math.max(14, overlay.width / 32)}px sans-serif`;

    faces.forEach((face) => {
      const color = face.known ? "#16a34a" : "#dc2626";
      const label = !face.known
        ? "Unknown"
        : face.please_blink
          ? `${face.name} · Please blink`
        : face.newly_marked
          ? `${face.name} · Attendance marked`
          : `${face.name} · Present`;
      const boxWidth = face.right - face.left;
      const boxHeight = face.bottom - face.top;
      overlayContext.strokeStyle = color;
      overlayContext.strokeRect(face.left, face.top, boxWidth, boxHeight);
      const labelWidth = overlayContext.measureText(label).width + 16;
      const labelTop = Math.max(0, face.top - 30);
      overlayContext.fillStyle = face.please_blink ? "#d97706" : color;
      overlayContext.fillRect(face.left, labelTop, labelWidth, 28);
      overlayContext.fillStyle = "#ffffff";
      overlayContext.fillText(label, face.left + 8, labelTop + 20);
    });
  };

  const renderPresent = (students) => {
    presentCount.textContent = String(students.length);
    presentList.replaceChildren();
    if (students.length === 0) {
      const empty = document.createElement("li");
      empty.className = "rounded-xl border border-dashed border-slate-300 px-4 py-5 text-center text-sm text-slate-500 dark:border-slate-700 dark:text-slate-400";
      empty.textContent = "No students marked yet. Start the camera to begin.";
      presentList.append(empty);
      return;
    }

    students.forEach((student) => {
      const item = document.createElement("li");
      item.className = "flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800";
      const details = document.createElement("div");
      const name = document.createElement("p");
      name.className = "font-semibold";
      name.textContent = student.name;
      const roll = document.createElement("p");
      roll.className = "mt-1 text-xs text-slate-500 dark:text-slate-400";
      roll.textContent = student.roll_no;
      details.append(name, roll);
      const badge = document.createElement("span");
      badge.className = "rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-semibold text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300";
      badge.textContent = "Present";
      item.append(details, badge);
      presentList.append(item);
    });
  };

  const showToast = (message) => {
    toast.textContent = message;
    toast.classList.remove("hidden");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.add("hidden"), 3500);
  };

  const sendFrame = async () => {
    if (!stream || sendingFrame || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
    sendingFrame = true;
    try {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      frameContext.drawImage(video, 0, 0, canvas.width, canvas.height);
      const response = await fetch(page.dataset.frameUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          csrf_token: page.dataset.csrfToken,
          frame: canvas.toDataURL("image/jpeg", 0.75),
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not process the camera frame.");

      drawFaces(result.faces);
      renderPresent(result.present);
      const marked = result.faces.find((face) => face.newly_marked);
      if (marked) showToast(`${marked.name} marked present.`);
      if (result.faces.some((face) => face.please_blink)) {
        setStatus("Please blink to verify before attendance is marked.");
      } else if (result.faces.length === 0) {
        setStatus("No face detected. Look toward the camera in a well-lit area.");
      } else {
        const unknownCount = result.faces.filter((face) => !face.known).length;
        setStatus(unknownCount
          ? `${result.faces.length} face${result.faces.length === 1 ? "" : "s"} detected; ${unknownCount} not recognized.`
          : `${result.faces.length} enrolled face${result.faces.length === 1 ? "" : "s"} recognized.`);
      }
    } catch (error) {
      setStatus(error.message || "Could not process the webcam frame. Please retry.", true);
    } finally {
      sendingFrame = false;
    }
  };

  const stopCamera = () => {
    if (timer !== null) {
      window.clearInterval(timer);
      timer = null;
    }
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    video.srcObject = null;
    overlayContext.clearRect(0, 0, overlay.width, overlay.height);
    placeholder.classList.remove("hidden");
    setCameraState(false);
    if (enrolledCount > 0) setStatus("Camera stopped. Today's attendance is saved.");
  };

  startButton.addEventListener("click", async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setStatus("This browser cannot access a webcam. Use localhost or a supported browser.", true);
      return;
    }
    if (enrolledCount === 0) {
      setStatus("Enroll at least one student before starting attendance.", true);
      return;
    }
    startButton.disabled = true;
    setStatus("Requesting webcam permission…");
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
      });
      video.srcObject = stream;
      await video.play();
      placeholder.classList.add("hidden");
      setCameraState(true);
      setStatus("Camera ready. Recognized students will be marked automatically.");
      await sendFrame();
      timer = window.setInterval(sendFrame, 250);
    } catch (error) {
      stopCamera();
      const message = error && error.name === "NotAllowedError"
        ? "Camera permission was denied. Allow camera access in your browser, then try again."
        : "Could not start the webcam. Check that it is connected and not being used by another app.";
      setStatus(message, true);
    }
  });

  stopButton.addEventListener("click", stopCamera);
  window.addEventListener("pagehide", stopCamera, { once: true });
  setCameraState(false);
  if (enrolledCount === 0) {
    setStatus("Enroll a student before taking attendance.");
  }
})();
