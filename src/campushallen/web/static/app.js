// Copy actions use translated labels supplied by the server; no invitation is logged.
document.querySelectorAll('[data-copy]').forEach((button) => {
  button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copy);
    const status = button.nextElementSibling;
    try {
      await navigator.clipboard.writeText(input.value);
      status.textContent = button.dataset.success;
    } catch {
      input.focus();
      input.select();
      status.textContent = button.dataset.failure;
    }
  });
});
