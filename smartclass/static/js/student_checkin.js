(() => {
  const page = document.getElementById("student-checkin");
  if (!page) return;

  const video = document.getElementById("checkin-video");
  const placeholder = document.getElementById("checkin-placeholder");
  const startButton = document.getElementById("checkin-start");
  const stopButton = document.getElementById("checkin-stop");
  const status = document.getElementById("checkin-status");
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", { willReadFrequently: true });
  let stream = null;
  let timer = null;
  let sending = false;
  let location = null;

  const setStatus = (message, isError = false) => {
    status.textContent = message;
    status.classList.toggle("text-red-600", isError);
    status.classList.toggle("text-slate-500", !isError);
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
    startButton.disabled = false;
    stopButton.disabled = true;
  };

  const getLocation = () => new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("This browser does not support location access."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy: position.coords.accuracy,
      }),
      (error) => reject(new Error(error.code === error.PERMISSION_DENIED
        ? "Location permission was denied. Allow it to check in."
        : "Could not determine your location. Move to a clearer area and retry.")),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  });

  const sendFrame = async () => {
    if (!stream || sending || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
    sending = true;
    try {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const response = await fetch(page.dataset.frameUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          csrf_token: page.dataset.csrfToken,
          checkin_token: page.dataset.checkinToken,
          session_id: page.dataset.sessionId,
          latitude: location.latitude,
          longitude: location.longitude,
          accuracy: location.accuracy,
          frame: canvas.toDataURL("image/jpeg", 0.75),
        }),
      });
      const result = await response.json();
      if (!response.ok) {
        stopCamera();
        throw new Error(result.error || "Check-in could not be completed.");
      }
      if (result.newly_marked || result.already_recorded) {
        setStatus(`Attendance: ${result.status}.`);
        stopCamera();
      } else {
        setStatus(result.status || "Checking your face and location…");
      }
    } catch (error) {
      setStatus(error.message || "Check-in failed. Please try again.", true);
    } finally {
      sending = false;
    }
  };

  startButton.addEventListener("click", async () => {
    startButton.disabled = true;
    setStatus("Requesting location permission…");
    try {
      location = await getLocation();
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error("This browser cannot access a webcam. Use localhost or HTTPS.");
      }
      setStatus("Requesting camera permission…");
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
      });
      video.srcObject = stream;
      await video.play();
      placeholder.classList.add("hidden");
      stopButton.disabled = false;
      setStatus("Look at the camera and blink once. Your location is checked during this check-in only.");
      await sendFrame();
      if (stream) timer = window.setInterval(sendFrame, 600);
    } catch (error) {
      stopCamera();
      startButton.disabled = false;
      setStatus(error.message || "Camera or location could not be started.", true);
    }
  });
  stopButton.addEventListener("click", () => {
    stopCamera();
    setStatus("Camera stopped. Attendance was not marked unless a face and blink were verified.");
  });
  window.addEventListener("pagehide", stopCamera, { once: true });
})();
