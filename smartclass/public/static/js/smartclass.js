(() => {
  const root = document.documentElement;
  const toggle = document.getElementById("theme-toggle");
  const label = document.getElementById("theme-toggle-label");
  if (!toggle || !label) return;

  const storedTheme = localStorage.getItem("smartclass-theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const useDark = storedTheme ? storedTheme === "dark" : prefersDark;
  root.classList.toggle("dark", useDark);

  const updateLabel = () => {
    label.textContent = root.classList.contains("dark") ? "Light mode" : "Dark mode";
  };
  updateLabel();

  toggle.addEventListener("click", () => {
    const nowDark = root.classList.toggle("dark");
    localStorage.setItem("smartclass-theme", nowDark ? "dark" : "light");
    updateLabel();
  });
})();
