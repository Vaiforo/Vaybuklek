"""Автоподключение бота к Яндекс.Телемосту через браузер (Playwright).

У Телемоста нет публичного API «войти в звонок», поэтому бот открывает ссылку в
реальном Chromium, вводит имя, жмёт «Подключиться» и глушит свой микрофон/камеру.
Звук самого звонка пишется отдельно — через системный loopback (браузер играет
его в колонки). Этот модуль отвечает ТОЛЬКО за вход и удержание вкладки звонка.

Playwright импортируется лениво: без него режим telemost недоступен, но остальной
бот (и loopback-запись) работает. Сессия живёт в собственном потоке, потому что
sync-API Playwright нельзя дёргать из другого потока, чем тот, где он создан.
"""

from __future__ import annotations

import threading

from ..config import AudioSettings
from ..logging_setup import get_logger

log = get_logger("dirizher.audio.telemost")

# Аргументы Chromium: авто-разрешение доступа к микрофону/камере и фейковые
# устройства — чтобы вход не упирался в отсутствие железа и не спрашивал прав.
_CHROMIUM_ARGS = [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]

# Эвристики поиска поля имени и кнопок (UI Телемоста меняется — поэтому
# несколько вариантов; точные значения можно задать в .env через *_selector).
# Поток входа: «Продолжить в браузере» → поле имени → «Подключиться» → в звонке.
_CONTINUE_TEXTS = ["Продолжить в браузере", "Continue in browser"]
_NAME_SELECTORS = [
    'input[name="name"]',
    'input[placeholder*="мя"]',  # «Ваше имя»
    'input[placeholder*="name" i]',
    'input[type="text"]',
]
_JOIN_TEXTS = ["Подключиться", "Присоединиться", "Войти в встречу", "Join"]
# Признаки того, что мы УЖЕ в звонке (для подтверждения входа).
_IN_CALL_TEXTS = ["Выйти из встречи", "Выключить микрофон", "Leave"]
# Заглушить себя после входа (бот не должен фонить в звонок).
_MUTE_TEXTS = ["Выключить микрофон", "Выключить камеру"]


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


class TelemostSession:
    """Браузерная вкладка с открытым звонком Телемоста.

    `join()` блокирующе запускает браузер в фоновом потоке и ждёт подтверждения
    входа (или таймаута). `close()` (или установка общего stop-события) гасит
    браузер. Вся работа с Playwright идёт в одном потоке `_run`.
    """

    def __init__(self, link: str, cfg: AudioSettings, stop: threading.Event) -> None:
        self._link = link
        self._cfg = cfg
        self._stop = stop
        self._thread: threading.Thread | None = None
        self._joined = threading.Event()
        self._ok = False
        self._error: str | None = None

    @property
    def error(self) -> str | None:
        return self._error

    def join(self) -> bool:
        """Открыть звонок и попытаться войти. True — браузер поднят и мы в звонке."""
        if not playwright_available():
            self._error = "playwright не установлен"
            return False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Ждём вход не дольше таймаута; браузер дальше живёт в своём потоке.
        self._joined.wait(timeout=max(5, self._cfg.telemost_join_timeout))
        return self._ok

    def close(self) -> None:
        """Попросить поток закрыть браузер (через общий stop-флаг)."""
        self._stop.set()

    def _launch_browser(self, p):
        """Запустить браузер, перебирая каналы: настроенный → Edge → Chrome → встроенный.

        Встроенный Chrome for Testing на части Windows-машин не стартует headful
        (битая SxS-сборка / нет свежего VC++ runtime), а системный Edge/Chrome
        работает всегда. Берём первый, который реально поднялся.
        """
        configured = (self._cfg.telemost_browser_channel or "").strip()
        # None = встроенный chromium (без channel). Порядок важен: сначала то, что
        # задано явно, иначе системные браузеры, и только потом встроенный.
        candidates: list[str | None] = [configured] if configured else ["msedge", "chrome", None]
        last_err: Exception | None = None
        for channel in candidates:
            try:
                kwargs = {"headless": self._cfg.telemost_headless, "args": _CHROMIUM_ARGS}
                if channel:
                    kwargs["channel"] = channel
                browser = p.chromium.launch(**kwargs)
                log.info("Telemost: браузер запущен (channel=%s)", channel or "chromium")
                return browser
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("Telemost: браузер channel=%s не стартовал: %s", channel or "chromium", e)
        raise last_err if last_err else RuntimeError("не удалось запустить браузер")

    # ── внутреннее ──────────────────────────────────────────────────────────
    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as p:
                browser = self._launch_browser(p)
                context = browser.new_context(permissions=["microphone", "camera"])
                page = context.new_page()
                # «commit» вместо «domcontentloaded»: тяжёлый SPA Телемоста на
                # первом headful-запуске (создание профиля) может не успеть за 30с;
                # дальше всё равно ждём элементы по селекторам.
                page.goto(self._link, wait_until="commit", timeout=60000)
                self._ok = self._try_join(page)
                self._joined.set()  # разблокируем join() независимо от исхода клика
                if self._ok:
                    log.info("🟢 Telemost: вошёл в звонок как «%s»", self._cfg.telemost_join_name)
                else:
                    log.warning("Telemost: автo-вход не подтверждён — окно открыто для ручного входа")
                # Держим вкладку открытой, пока идёт запись (общий stop-флаг).
                self._stop.wait()
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            log.exception("Telemost: сбой браузерной сессии")
            self._error = str(e)
            self._ok = False
            self._joined.set()

    def _try_join(self, page) -> bool:
        """Пройти все экраны входа и подтвердить, что мы реально в звонке."""
        self._click_continue(page)  # промежуточный экран «Продолжить в браузере»
        self._fill_name(page)
        self._click_join(page)
        ok = self._confirm_in_call(page)
        if ok:
            self._mute_self(page)  # бот не должен фонить в звонок
        return ok

    @staticmethod
    def _click_button(page, texts: list[str], timeout: int = 4000) -> bool:
        for text in texts:
            try:
                page.get_by_role("button", name=text).first.click(timeout=timeout)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _click_continue(self, page) -> None:
        # Экран-прокладка появляется не всегда — кликаем, если есть.
        self._click_button(page, _CONTINUE_TEXTS, timeout=8000)
        page.wait_for_timeout(2000)

    def _fill_name(self, page) -> None:
        selectors = [self._cfg.telemost_name_selector] if self._cfg.telemost_name_selector else _NAME_SELECTORS
        for sel in selectors:
            if not sel:
                continue
            try:
                el = page.wait_for_selector(sel, timeout=6000)
                if el:
                    el.fill(self._cfg.telemost_join_name)
                    return
            except Exception:  # noqa: BLE001
                continue

    def _click_join(self, page) -> bool:
        if self._cfg.telemost_join_selector:
            try:
                page.click(self._cfg.telemost_join_selector, timeout=8000)
                return True
            except Exception:  # noqa: BLE001
                return False
        return self._click_button(page, _JOIN_TEXTS, timeout=6000)

    def _confirm_in_call(self, page) -> bool:
        """Дождаться признака звонка («Выйти из встречи») в пределах таймаута."""
        # Делим бюджет между вариантами, чтобы суммарное ожидание не растягивалось.
        per = max(2000, max(5, self._cfg.telemost_join_timeout) * 1000 // len(_IN_CALL_TEXTS))
        for text in _IN_CALL_TEXTS:
            try:
                page.get_by_role("button", name=text).first.wait_for(timeout=per)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _mute_self(self, page) -> None:
        for text in _MUTE_TEXTS:
            try:
                page.get_by_role("button", name=text).first.click(timeout=3000)
            except Exception:  # noqa: BLE001
                continue
