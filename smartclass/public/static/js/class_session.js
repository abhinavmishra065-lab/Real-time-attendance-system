(() => {
  const monitor = document.getElementById("class-monitor");
  if (!monitor) return;

  const rows = document.getElementById("attendance-rows");
  const state = document.getElementById("session-state");
  const feedback = document.getElementById("manual-attendance-feedback");
  const syncAttendanceButtons = (row, status) => {
    row.querySelectorAll("[data-manual-attendance] button[name='status']").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.value === status));
    });
  };

  rows.querySelectorAll("[data-student-id]").forEach((row) => {
    syncAttendanceButtons(row, row.querySelector("[data-status]").textContent);
  });

  const update = async () => {
    try {
      const response = await fetch(monitor.dataset.statusUrl, {
        headers: { Accept: "application/json" },
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not refresh attendance.");

      result.rows.forEach((student) => {
        const row = rows.querySelector(`[data-student-id="${student.student_id}"]`);
        if (!row) return;
        row.querySelector("[data-status]").textContent = student.status;
        row.querySelector("[data-time]").textContent = student.time || "—";
        syncAttendanceButtons(row, student.status);
      });
      if (result.ended) {
        state.textContent = "Class ended · absences finalized";
      } else {
        state.textContent = `${result.present} present · ${result.absent} absent · ${result.pending} pending`;
      }
    } catch (error) {
      state.textContent = error.message || "Attendance refresh failed.";
    }
  };

  rows.addEventListener("submit", async (event) => {
    const form = event.target;
    const submitter = event.submitter;
    if (
      !(form instanceof HTMLFormElement) ||
      !form.matches("[data-manual-attendance]") ||
      !(submitter instanceof HTMLButtonElement)
    ) {
      return;
    }

    event.preventDefault();
    const formData = new FormData(form);
    formData.set(submitter.name, submitter.value);
    const buttons = form.querySelectorAll("button");
    buttons.forEach((button) => {
      button.disabled = true;
    });
    feedback.textContent = "Updating attendance…";
    feedback.classList.remove("text-red-700");

    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { Accept: "application/json" },
        body: formData,
      });
      if (response.redirected) throw new Error("Your session expired. Please sign in again.");
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Attendance could not be updated.");

      const row = form.closest("[data-student-id]");
      row.querySelector("[data-status]").textContent = result.status;
      row.querySelector("[data-time]").textContent = result.time || "—";
      syncAttendanceButtons(row, result.status);
      feedback.textContent = `${result.name} marked ${result.status.toLowerCase()}.`;
      update();
    } catch (error) {
      feedback.textContent = error.message || "Attendance could not be updated.";
      feedback.classList.add("text-red-700");
    } finally {
      buttons.forEach((button) => {
        button.disabled = false;
      });
    }
  });

  window.setInterval(update, 5000);
})();
