// Offscreen-документ: реальный захват звука вкладки, авто-стоп по тишине,
// опрос бота для автозапуска и отправка записи.
//
// Почему здесь, а не в service worker: getUserMedia/MediaRecorder/AudioContext
// требуют DOM, которого в SW нет. Offscreen-документ живёт долго (в отличие от
// SW, который Chrome усыпляет), поэтому здесь же крутится опрос /meeting/command.
//
// Поток: SW даёт streamId активной вкладки → getUserMedia(chromeMediaSource:tab)
// → MediaRecorder(webm/opus). Звук вкладки возвращаем в колонки через
// AudioContext, иначе собеседник «оглохнет». Параллельно AnalyserNode считает
// громкость: если тишина дольше silence_seconds — сами останавливаемся.

const POLL_MS = 4000; // как часто опрашиваем бота (автозапуск/стоп)

let mediaRecorder = null;
let chunks = [];
let stream = null;       // поток вкладки (tabCapture)
let micStream = null;    // поток микрофона (если включён captureMic)
let audioCtx = null;
let analyser = null;
let startedAt = 0;

let config = {};                 // {apiBase, chatId, token}
let silenceSeconds = 180;        // порог авто-стопа по тишине (с сервера)
let silenceRms = 0.004;          // порог RMS «тишины» (с сервера)
let silenceStreakMs = 0;         // сколько подряд длится тишина
let lastTick = 0;
let startPending = false;        // ждём ответа SW на REQUEST_START (не спамим)

function recording() {
  return mediaRecorder !== null;
}

// ── Захват и запись ──────────────────────────────────────────────────────────
async function start(streamId, cfg) {
  if (recording()) return { state: "recording", startedAt };
  if (cfg) config = cfg;

  stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });

  audioCtx = new AudioContext();
  // Без жеста пользователя контекст может стартовать «suspended» → не будет ни
  // воспроизведения в колонки, ни данных в анализаторе. Будим его явно.
  try { await audioCtx.resume(); } catch {}

  const tabSource = audioCtx.createMediaStreamSource(stream);
  // Возвращаем звук вкладки в колонки (tabCapture его «перехватывает»).
  tabSource.connect(audioCtx.destination);

  // Анализатор громкости для авто-стопа по тишине.
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 2048;
  tabSource.connect(analyser);

  // По умолчанию пишем только звук вкладки (голоса других участников). Если
  // включён микрофон — подмешиваем его, чтобы в записи был и голос того, у кого
  // расширение (его реплики в звонок уходят, но в самой вкладке не звучат).
  let recordStream = stream;
  if (config.captureMic) {
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
      const mixDest = audioCtx.createMediaStreamDestination();
      tabSource.connect(mixDest);
      const micSource = audioCtx.createMediaStreamSource(micStream);
      micSource.connect(mixDest);     // микрофон → в запись
      micSource.connect(analyser);    // и в детектор тишины
      // ВАЖНО: микрофон НЕ подключаем к audioCtx.destination — иначе эхо в колонки.
      recordStream = mixDest.stream;
    } catch (e) {
      // Нет доступа к микрофону — пишем только вкладку, не падаем.
      micStream = null;
      console.warn("Микрофон недоступен, пишу только звук вкладки:", e && e.message);
    }
  }

  silenceStreakMs = 0;
  lastTick = Date.now();

  chunks = [];
  mediaRecorder = new MediaRecorder(recordStream, { mimeType: "audio/webm;codecs=opus" });
  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) chunks.push(e.data);
  };
  mediaRecorder.start(1000); // нарезаем чанки раз в секунду
  startedAt = Date.now();
  startPending = false;
  return { state: "recording", startedAt };
}

async function stop(reason) {
  if (!recording()) return { state: "idle" };

  const blob = await new Promise((resolve) => {
    mediaRecorder.onstop = () => resolve(new Blob(chunks, { type: "audio/webm" }));
    mediaRecorder.stop();
  });

  try { stream.getTracks().forEach((t) => t.stop()); } catch {}
  try { if (micStream) micStream.getTracks().forEach((t) => t.stop()); } catch {}
  try { if (audioCtx) await audioCtx.close(); } catch {}
  mediaRecorder = null;
  stream = null;
  micStream = null;
  audioCtx = null;
  analyser = null;

  const result = await upload(blob);
  // Сообщаем SW, что запись закончилась (снять подсветку иконки).
  try { chrome.runtime.sendMessage({ target: "background", type: "RECORDING_ENDED" }); } catch {}
  return { ...result, stoppedBy: reason || "manual" };
}

// Текущая громкость (RMS, шкала 0..1) — для детекта тишины.
// Если контекст не «running» — считаем НЕ тишиной (возвращаем 1), чтобы не
// оборвать запись по ложной тишине из-за приостановленного AudioContext.
function currentRms() {
  if (!analyser || !audioCtx || audioCtx.state !== "running") return 1;
  const buf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
  return Math.sqrt(sum / buf.length);
}

// ── Отправка боту ────────────────────────────────────────────────────────────
async function upload(blob) {
  const apiBase = (config.apiBase || "").replace(/\/+$/, "");
  if (!apiBase || !config.chatId) {
    return { state: "error", error: "Не заданы API base и chat_id (настройки расширения)." };
  }
  const form = new FormData();
  form.append("file", blob, "meeting.webm");
  form.append("chat_id", String(config.chatId));
  form.append("reason", "extension");

  const headers = {};
  if (config.token) headers["X-Dirizher-Token"] = config.token;

  try {
    const resp = await fetch(apiBase + "/meeting/audio", { method: "POST", body: form, headers });
    if (!resp.ok) return { state: "error", error: `Сервер ответил ${resp.status}` };
    const data = await resp.json().catch(() => ({}));
    return { state: "sent", bytes: data.bytes || blob.size };
  } catch (e) {
    return { state: "error", error: String(e && e.message ? e.message : e) };
  }
}

// ── Опрос бота: автозапуск/стоп + авто-стоп по тишине ────────────────────────
async function poll() {
  // Авто-стоп по тишине считаем независимо от связи с ботом.
  if (recording()) {
    const now = Date.now();
    const dt = now - lastTick;
    lastTick = now;
    if (currentRms() < silenceRms) {
      silenceStreakMs += dt;
      if (silenceStreakMs >= silenceSeconds * 1000) {
        await stop("silence");
        return;
      }
    } else {
      silenceStreakMs = 0;
    }
  }

  const apiBase = (config.apiBase || "").replace(/\/+$/, "");
  if (!apiBase || !config.chatId) return; // ещё не настроено — нечего опрашивать

  try {
    const headers = {};
    if (config.token) headers["X-Dirizher-Token"] = config.token;
    const url = apiBase + "/meeting/command?chat_id=" + encodeURIComponent(config.chatId);
    const resp = await fetch(url, { headers });
    if (!resp.ok) return;
    const cmd = await resp.json();
    if (typeof cmd.silence_seconds === "number") silenceSeconds = cmd.silence_seconds;
    if (typeof cmd.silence_rms === "number") silenceRms = cmd.silence_rms;

    if (cmd.desired === "recording" && !recording() && !startPending) {
      // SW сам возьмёт streamId активной вкладки и пришлёт нам START.
      startPending = true;
      chrome.runtime.sendMessage({ target: "background", type: "REQUEST_START" }, () => {
        // Ответ (или ошибку) обработает onMessage(START); снимаем флаг на всякий.
        startPending = false;
      });
    } else if (cmd.desired === "idle" && recording()) {
      await stop("command");
    }
  } catch {
    // Бот недоступен — молча ждём следующего тика.
  }
}

// ── Сообщения от service worker ──────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || msg.target !== "offscreen") return false;
  (async () => {
    try {
      if (msg.type === "START") sendResponse(await start(msg.streamId, msg.config));
      else if (msg.type === "STOP") sendResponse(await stop("manual"));
      else if (msg.type === "GET_STATE") {
        sendResponse(recording() ? { state: "recording", startedAt } : { state: "idle" });
      } else if (msg.type === "CONFIG") {
        config = msg.config || {};
        sendResponse({ ok: true });
      } else sendResponse({ error: "unknown message" });
    } catch (e) {
      startPending = false;
      sendResponse({ state: "error", error: String(e && e.message ? e.message : e) });
    }
  })();
  return true;
});

// Подтягиваем конфиг у SW и запускаем опрос.
chrome.runtime.sendMessage({ target: "background", type: "REQUEST_CONFIG" }, (cfg) => {
  if (cfg && !chrome.runtime.lastError) config = cfg;
});
setInterval(poll, POLL_MS);
