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
  let checkinLocation = null;

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
    placeholder.classList.remove("hidden");
    startButton.disabled = false;
    stopButton.disabled = true;
  };

  const requestLocation = (options) => new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, options);
  });

  const locationErrorMessage = (error) => {
    if (error.code === error.PERMISSION_DENIED) {
      return "Location permission was denied. Allow location access for this site, then try again.";
    }
    if (error.code === error.TIMEOUT) {
      return "Your location request timed out. Move near a window and try again.";
    }
    return "Your location could not be determined. Check device location services and try again.";
  };

  const getLocation = async () => {
    if (window.isSecureContext === false) {
      throw new Error("Location and camera access require HTTPS or localhost. Open this site securely and try again.");
    }
    if (!navigator.geolocation) {
      throw new Error("This browser does not support location access.");
    }
    try {
      return await requestLocation({
        enableHighAccuracy: true,
        timeout: 15000,
        maximumAge: 0,
      });
    } catch (error) {
      if (error.code === error.PERMISSION_DENIED) {
        throw new Error(locationErrorMessage(error));
      }
      try {
        return await requestLocation({
          enableHighAccuracy: false,
          timeout: 20000,
          maximumAge: 30000,
        });
      } catch (retryError) {
        throw new Error(locationErrorMessage(retryError));
      }
    }
  };

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
          latitude: checkinLocation.latitude,
          longitude: checkinLocation.longitude,
          accuracy: checkinLocation.accuracy,
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
      const position = await getLocation();
      checkinLocation = {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy: position.coords.accuracy,
      };
      setStatus(`Location found (about ${Math.round(checkinLocation.accuracy)} m accuracy). Requesting camera permission…`);
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error("This browser cannot access a webcam. Use localhost or HTTPS.");
      }
      stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } },
      });
      video.srcObject = stream;
      await video.play();
      placeholder.classList.add("hidden");
      stopButton.disabled = false;
      setStatus("Look at the camera. Your face and location are checked during this check-in only.");
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
    setStatus("Camera stopped. Attendance was not marked unless your face was verified.");
  });
  window.addEventListener("pagehide", stopCamera, { once: true });
})();
