// Service worker (MV3): координирует захват вкладки.
//
// В MV3 tabCapture/getUserMedia/MediaRecorder недоступны прямо в service worker
// (нет DOM). Поэтому реальная запись и опрос бота живут в offscreen-документе, а
// SW делает то, что умеет только он: получает MediaStreamId активной вкладки
// (по запросу) и поднимает/держит offscreen-документ.
//
// Автозапуск: offscreen опрашивает бота (GET /meeting/command). Когда в чате
// кидают ссылку Телемоста, бот поднимает желаемое состояние «recording» —
// offscreen видит его и просит SW начать захват (REQUEST_START).

const OFFSCREEN_PATH = "offscreen.html";

async function ensureOffscreen() {
  if (await chrome.offscreen.hasDocument()) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_PATH,
    reasons: ["USER_MEDIA"],
    justification: "Захват звука вкладки созвона и опрос бота Дирижёр для автозаписи.",
  });
}

async function getConfig() {
  return await chrome.storage.sync.get(["apiBase", "chatId", "token"]);
}

// Получить streamId активной вкладки и запустить запись в offscreen.
// Требует «жеста пользователя» либо activeTab; при автозапуске из опроса Chrome
// может отказать — тогда подсвечиваем иконку и просим кликнуть вручную.
async function startCapture() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error("Не нашёл активную вкладку.");

  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  await ensureOffscreen();
  const config = await getConfig();
  const resp = await sendToOffscreen({ type: "START", streamId, config });
  clearActionHint();
  return resp;
}

async function stopCapture() {
  if (!(await chrome.offscreen.hasDocument())) return { state: "idle" };
  return await sendToOffscreen({ type: "STOP" });
}

async function getState() {
  if (!(await chrome.offscreen.hasDocument())) return { state: "idle" };
  try {
    return await sendToOffscreen({ type: "GET_STATE" });
  } catch {
    return { state: "idle" };
  }
}

function sendToOffscreen(msg) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({ target: "offscreen", ...msg }, (resp) => {
      const err = chrome.runtime.lastError;
      if (err) reject(new Error(err.message));
      else resolve(resp);
    });
  });
}

function setActionHint() {
  // Подсветка иконки: бот хочет запись, но автозахват не прошёл — нужен клик.
  try {
    chrome.action.setBadgeText({ text: "▶" });
    chrome.action.setBadgeBackgroundColor({ color: "#e5352b" });
    chrome.action.setTitle({ title: "Дирижёр: бот ждёт запись — нажмите «Записать»" });
  } catch {}
}

function clearActionHint() {
  try {
    chrome.action.setBadgeText({ text: "" });
    chrome.action.setTitle({ title: "Дирижёр — захват созвона" });
  } catch {}
}

// Роутер сообщений: msg.target === "background".
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.target !== "background") return false;
  (async () => {
    try {
      if (msg.type === "START") {
        sendResponse(await startCapture());
      } else if (msg.type === "STOP") {
        sendResponse(await stopCapture());
      } else if (msg.type === "GET_STATE") {
        sendResponse(await getState());
      } else if (msg.type === "REQUEST_CONFIG") {
        sendResponse(await getConfig());
      } else if (msg.type === "REQUEST_START") {
        // Автозапуск из опроса offscreen. При отказе Chrome (нет жеста) —
        // подсвечиваем иконку, чтобы пользователь нажал «Записать» вручную.
        try {
          sendResponse(await startCapture());
        } catch (e) {
          setActionHint();
          sendResponse({ error: String(e && e.message ? e.message : e) });
        }
      } else if (msg.type === "RECORDING_ENDED") {
        clearActionHint();
        sendResponse({ ok: true });
      } else {
        sendResponse({ error: "unknown message" });
      }
    } catch (e) {
      sendResponse({ error: String(e && e.message ? e.message : e) });
    }
  })();
  return true;
});

// Конфиг изменился в настройках → отдадим offscreen свежий, чтобы опрос не отстал.
chrome.storage.onChanged.addListener(async (changes, area) => {
  if (area !== "sync") return;
  if (!(await chrome.offscreen.hasDocument())) return;
  try {
    await sendToOffscreen({ type: "CONFIG", config: await getConfig() });
  } catch {}
});

// Держим offscreen-поллер живым: создаём на старте и раз в минуту проверяем.
chrome.runtime.onStartup.addListener(() => ensureOffscreen());
chrome.runtime.onInstalled.addListener(() => ensureOffscreen());
chrome.alarms.create("keepalive", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "keepalive") ensureOffscreen();
});
ensureOffscreen();
