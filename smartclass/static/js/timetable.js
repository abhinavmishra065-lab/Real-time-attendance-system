(() => {
  const locateButton = document.getElementById("locate-classroom");
  if (!locateButton) return;

  const latitude = document.getElementById("schedule-latitude");
  const longitude = document.getElementById("schedule-longitude");
  const status = document.getElementById("location-status");
  locateButton.addEventListener("click", () => {
    if (!navigator.geolocation) {
      status.textContent = "This browser does not support location access.";
      return;
    }
    locateButton.disabled = true;
    status.textContent = "Finding the classroom location…";
    navigator.geolocation.getCurrentPosition(
      (position) => {
        latitude.value = position.coords.latitude.toFixed(7);
        longitude.value = position.coords.longitude.toFixed(7);
        status.textContent = `Location set (about ${Math.round(position.coords.accuracy)} m accuracy). Adjust it if needed.`;
        locateButton.disabled = false;
      },
      (error) => {
        status.textContent = error.code === error.PERMISSION_DENIED
          ? "Location permission was denied. Allow it or enter the classroom coordinates manually."
          : "Could not determine location. Try again or enter coordinates manually.";
        locateButton.disabled = false;
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
    );
  });
})();
