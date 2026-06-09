// Попап: кнопки «Записать»/«Стоп», индикатор состояния и таймер.
// Вся работа со звуком — в offscreen-документе; попап лишь шлёт команды в
// service worker и показывает состояние.

const recBtn = document.getElementById("rec");
const stopBtn = document.getElementById("stop");
const statusEl = document.getElementById("status");
const optsLink = document.getElementById("opts");

let timer = null;
let startedAt = 0;

function send(type) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ target: "background", type }, (resp) => {
      if (chrome.runtime.lastError) resolve({ error: chrome.runtime.lastError.message });
      else resolve(resp || {});
    });
  });
}

function fmt(ms) {
  const s = Math.floor(ms / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function showRecording() {
  recBtn.disabled = true;
  stopBtn.disabled = false;
  if (timer) clearInterval(timer);
  timer = setInterval(() => {
    statusEl.innerHTML = `<span class="dot">●</span> Идёт запись · <span id="timer">${fmt(Date.now() - startedAt)}</span>`;
  }, 250);
}

function showIdle(text) {
  recBtn.disabled = false;
  stopBtn.disabled = true;
  if (timer) { clearInterval(timer); timer = null; }
  statusEl.textContent = text || "Готово к записи.";
}

recBtn.addEventListener("click", async () => {
  statusEl.textContent = "Запускаю захват вкладки…";
  const resp = await send("START");
  if (resp.error) { showIdle("Ошибка: " + resp.error); return; }
  startedAt = resp.startedAt || Date.now();
  showRecording();
});

stopBtn.addEventListener("click", async () => {
  if (timer) { clearInterval(timer); timer = null; }
  statusEl.textContent = "Останавливаю и отправляю боту…";
  stopBtn.disabled = true;
  const resp = await send("STOP");
  if (resp.error || resp.state === "error") {
    showIdle("Ошибка отправки: " + (resp.error || "неизвестно"));
  } else if (resp.state === "sent") {
    showIdle(`✅ Отправлено боту (${Math.round((resp.bytes || 0) / 1024)} КБ). Задачи появятся в чате.`);
  } else {
    showIdle();
  }
});

optsLink.addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});

// Восстановить состояние при открытии попапа (запись могла идти и раньше).
(async () => {
  const resp = await send("GET_STATE");
  if (resp.state === "recording") {
    startedAt = resp.startedAt || Date.now();
    showRecording();
  } else {
    showIdle();
  }
})();
