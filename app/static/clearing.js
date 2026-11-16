async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.message || data.detail || "请求失败");
  }
  return data;
}

function flash(message, kind) {
  const box = document.querySelector("#flash");
  if (!box) return;
  box.textContent = message;
  box.className = kind === "ok" ? "flash ok" : "flash";
  box.hidden = false;
}

async function confirmAccount(key) {
  const select = document.querySelector('[data-account-select="' + key + '"]');
  if (!select) return;
  try {
    await request("/api/clearing/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: key, bank_account: select.value }),
    });
    window.location.reload();
  } catch (error) {
    flash(error.message);
  }
}

async function unconfirmAccount(key) {
  try {
    await request("/api/clearing/confirm?key=" + encodeURIComponent(key), { method: "DELETE" });
    window.location.reload();
  } catch (error) {
    flash(error.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-confirm]").forEach((button) => {
    button.addEventListener("click", () => confirmAccount(button.dataset.confirm));
  });
  document.querySelectorAll("[data-unconfirm]").forEach((button) => {
    button.addEventListener("click", () => unconfirmAccount(button.dataset.unconfirm));
  });
});
