(() => {
  const locateButton = document.getElementById("locate-classroom");
  if (!locateButton) return;

  const latitude = document.getElementById("schedule-latitude");
  const longitude = document.getElementById("schedule-longitude");
  const status = document.getElementById("location-status");
  const requestLocation = (options) => new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, options);
  });
  const getLocation = async () => {
    if (window.isSecureContext === false) {
      throw new Error("Location access requires HTTPS or localhost. Enter the classroom coordinates manually or open this site securely.");
    }
    if (!navigator.geolocation) {
      throw new Error("This browser does not support location access. Enter the classroom coordinates manually.");
    }
    try {
      return await requestLocation({
        enableHighAccuracy: true,
        timeout: 15000,
        maximumAge: 0,
      });
    } catch (error) {
      if (error.code === error.PERMISSION_DENIED) {
        throw new Error("Location permission was denied. Allow location access for this site or enter the coordinates manually.");
      }
      try {
        return await requestLocation({
          enableHighAccuracy: false,
          timeout: 20000,
          maximumAge: 30000,
        });
      } catch (retryError) {
        throw new Error(retryError.code === retryError.TIMEOUT
          ? "Location request timed out. Move near a window, try again, or enter the coordinates manually."
          : "Your location could not be determined. Check device location services or enter the coordinates manually.");
      }
    }
  };

  locateButton.addEventListener("click", async () => {
    locateButton.disabled = true;
    status.textContent = "Finding the classroom location…";
    try {
      const position = await getLocation();
      latitude.value = position.coords.latitude.toFixed(7);
      longitude.value = position.coords.longitude.toFixed(7);
      status.textContent = `Location set (about ${Math.round(position.coords.accuracy)} m accuracy). Adjust it if needed.`;
    } catch (error) {
      status.textContent = error.message || "Could not determine location. Enter the coordinates manually.";
    } finally {
      locateButton.disabled = false;
    }
  });
})();
