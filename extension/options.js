// Настройки расширения: API base, chat_id, токен → chrome.storage.sync.
// Кнопка «Проверить связь» дёргает GET /meeting/audio/health.

const apiBaseEl = document.getElementById("apiBase");
const chatIdEl = document.getElementById("chatId");
const tokenEl = document.getElementById("token");
const msgEl = document.getElementById("msg");

function show(text, ok) {
  msgEl.textContent = text;
  msgEl.style.color = ok ? "#16a34a" : "#dc2626";
}

// Загрузить сохранённые значения.
chrome.storage.sync.get(["apiBase", "chatId", "token"], (cfg) => {
  apiBaseEl.value = cfg.apiBase || "";
  chatIdEl.value = cfg.chatId != null ? cfg.chatId : "";
  tokenEl.value = cfg.token || "";
});

document.getElementById("save").addEventListener("click", () => {
  const apiBase = apiBaseEl.value.trim().replace(/\/+$/, "");
  const chatId = chatIdEl.value.trim();
  const token = tokenEl.value.trim();
  if (!apiBase || !chatId) {
    show("Заполните API base и chat_id.", false);
    return;
  }
  chrome.storage.sync.set({ apiBase, chatId, token }, () => {
    show("✅ Сохранено.", true);
  });
});

document.getElementById("ping").addEventListener("click", async () => {
  const apiBase = apiBaseEl.value.trim().replace(/\/+$/, "");
  const token = tokenEl.value.trim();
  if (!apiBase) { show("Укажите API base URL.", false); return; }
  show("Проверяю связь…", true);
  try {
    const headers = {};
    if (token) headers["X-Dirizher-Token"] = token;
    const resp = await fetch(apiBase + "/meeting/audio/health", { headers });
    if (resp.status === 401) { show("❌ Неверный токен (401).", false); return; }
    if (!resp.ok) { show(`❌ Сервер ответил ${resp.status}.`, false); return; }
    const data = await resp.json();
    if (!data.bot_running) {
      show("⚠️ Связь есть, но бот не запущен (bot_running=false).", false);
    } else {
      show(`✅ Связь есть. Распознавание: ${data.transcriber}.`, true);
    }
  } catch (e) {
    show("❌ Нет связи: " + (e && e.message ? e.message : e), false);
  }
});
