// Runs before first paint so the saved Swoop/Dooley theme never flashes. It is a
// file, not an inline script, because the production policy forbids inline script.
(function () {
  var theme = null;
  try {
    theme = localStorage.getItem('advisor-theme');
  } catch (error) {
    theme = null;
  }
  if (theme !== 'light' && theme !== 'dark') {
    theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.dataset.theme = theme;
})();
