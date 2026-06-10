// Настройки расширения: API base, chat_id, токен → chrome.storage.sync.
// Кнопка «Проверить связь» дёргает GET /meeting/audio/health.

const apiBaseEl = document.getElementById("apiBase");
const chatIdEl = document.getElementById("chatId");
const tokenEl = document.getElementById("token");
const captureMicEl = document.getElementById("captureMic");
const msgEl = document.getElementById("msg");
const micMsgEl = document.getElementById("micMsg");

function show(text, ok) {
  msgEl.textContent = text;
  msgEl.style.color = ok ? "#16a34a" : "#dc2626";
}

function showMic(text, ok) {
  micMsgEl.textContent = text;
  micMsgEl.style.color = ok ? "#16a34a" : "#dc2626";
}

// Загрузить сохранённые значения.
chrome.storage.sync.get(["apiBase", "chatId", "token", "captureMic"], (cfg) => {
  apiBaseEl.value = cfg.apiBase || "";
  chatIdEl.value = cfg.chatId != null ? cfg.chatId : "";
  tokenEl.value = cfg.token || "";
  captureMicEl.checked = !!cfg.captureMic;
});

document.getElementById("save").addEventListener("click", () => {
  const apiBase = apiBaseEl.value.trim().replace(/\/+$/, "");
  const chatId = chatIdEl.value.trim();
  const token = tokenEl.value.trim();
  const captureMic = captureMicEl.checked;
  if (!apiBase || !chatId) {
    show("Заполните API base и chat_id.", false);
    return;
  }
  chrome.storage.sync.set({ apiBase, chatId, token, captureMic }, () => {
    show("✅ Сохранено.", true);
  });
});

// Запросить разрешение на микрофон. Промпт можно показать только из видимой
// страницы (options), offscreen-документ — невидимый. Грант сохраняется для
// origin расширения, дальше offscreen использует микрофон без повторных запросов.
document.getElementById("mic").addEventListener("click", async () => {
  showMic("Запрашиваю доступ к микрофону…", true);
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    s.getTracks().forEach((t) => t.stop()); // нам нужен только грант
    captureMicEl.checked = true;
    showMic("✅ Доступ к микрофону разрешён. Не забудьте «Сохранить».", true);
  } catch (e) {
    captureMicEl.checked = false;
    showMic("❌ Доступ к микрофону не выдан: " + (e && e.message ? e.message : e), false);
  }
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
