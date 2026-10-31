(() => {
  const form = document.getElementById("enrollment-form");
  if (!form) return;

  const video = document.getElementById("enrollment-video");
  const placeholder = document.getElementById("camera-placeholder");
  const cameraIndicator = document.getElementById("camera-indicator");
  const captureButton = document.getElementById("capture-sample");
  const cancelButton = document.getElementById("cancel-enrollment");
  const captureLabel = document.getElementById("capture-label");
  const spinner = document.getElementById("capture-spinner");
  const status = document.getElementById("enrollment-status");
  const progress = document.getElementById("sample-progress");
  const counter = document.getElementById("sample-count");
  const progressBar = form.querySelector('[role="progressbar"]');
  const required = Number(form.dataset.requiredSamples);
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", { willReadFrequently: true });
  let stream = null;
  let captured = 0;
  let busy = false;

  const field = (name) => form.elements.namedItem(name).value.trim();
  const setStatus = (message, isError = false) => {
    status.textContent = message;
    status.classList.toggle("text-red-600", isError);
    status.classList.toggle("dark:text-red-400", isError);
    status.classList.toggle("text-slate-500", !isError);
  };
  const fieldsAreValid = () =>
    Boolean(
      field("roll_no") &&
      field("name") &&
      field("email") &&
      field("course") &&
      field("branch") &&
      field("semester")
    ) &&
    form.elements.namedItem("email").validity.valid &&
    (!field("parent_email") || form.elements.namedItem("parent_email").validity.valid);
  const updateProgress = () => {
    counter.textContent = `${captured} / ${required}`;
    progress.style.width = `${(captured / required) * 100}%`;
    progressBar.setAttribute("aria-valuenow", String(captured));
    captureButton.disabled = busy || !stream || !fieldsAreValid() || captured >= required;
  };
  const stopCamera = () => {
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    video.srcObject = null;
  };
  const cancelSamples = async (notifyServer = true) => {
    if (notifyServer) {
      try {
        await fetch(form.dataset.cancelUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ csrf_token: form.dataset.csrfToken }),
        });
      } catch {
        setStatus("Local samples cleared. Reload the page if the session needs resetting.", true);
      }
    }
    captured = 0;
    updateProgress();
  };

  const startCamera = async () => {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      cameraIndicator.textContent = "Camera unavailable";
      setStatus("This browser cannot access a webcam. Use localhost or HTTPS and a supported browser.", true);
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
      });
      video.srcObject = stream;
      await video.play();
      placeholder.classList.add("hidden");
      cameraIndicator.textContent = "Camera ready";
      cameraIndicator.className = "rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300";
      setStatus("Camera ready. Capture ten samples with exactly one face in the frame.");
      updateProgress();
    } catch (error) {
      cameraIndicator.textContent = "Camera unavailable";
      const message = error && error.name === "NotAllowedError"
        ? "Camera permission was denied. Allow camera access in your browser, then reload."
        : "Could not start the webcam. Check that it is connected and not being used by another app.";
      setStatus(message, true);
    }
  };

  captureButton.addEventListener("click", async () => {
    if (busy || !stream || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
    if (!fieldsAreValid()) {
      setStatus("Enter the student's roll number, name, email, course, branch, and semester first.", true);
      return;
    }

    busy = true;
    updateProgress();
    spinner.classList.remove("hidden");
    captureLabel.textContent = "Checking face…";
    setStatus("Checking that exactly one face is visible…");

    try {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const response = await fetch(form.dataset.sampleUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          csrf_token: form.dataset.csrfToken,
          roll_no: field("roll_no"),
          name: field("name"),
          email: field("email"),
          course: field("course"),
          branch: field("branch"),
          semester: field("semester"),
          parent_email: field("parent_email"),
          frame: canvas.toDataURL("image/jpeg", 0.82),
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Sample could not be captured.");

      captured = result.captured;
      updateProgress();
      if (result.complete) {
        setStatus(result.message || "Student enrolled successfully.");
        captureLabel.textContent = "Enrollment complete";
        captureButton.disabled = true;
        form.querySelectorAll("input").forEach((input) => { input.disabled = true; });
        window.location.reload();
        return;
      }
      setStatus(`Sample ${captured} of ${required} accepted. Keep one face centered and capture the next sample.`);
    } catch (error) {
      setStatus(error.message || "Could not send the sample. Check your connection and retry.", true);
    } finally {
      busy = false;
      spinner.classList.add("hidden");
      if (captured < required) captureLabel.textContent = "Capture sample";
      updateProgress();
    }
  });

  cancelButton.addEventListener("click", async () => {
    await cancelSamples();
    setStatus("Samples cleared. You can start again when ready.");
  });

  form.querySelectorAll("input").forEach((input) => {
    input.addEventListener("input", updateProgress);
  });
  window.addEventListener("pagehide", stopCamera, { once: true });
  startCamera();
})();
