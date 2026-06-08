import asyncio
import inspect
import os
import sys
from pathlib import Path

# Делаем пакет dirizher импортируемым из src без установки
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Изоляция тестов от боевого .env: принудительно mock-режим, без сети.
# Переменные окружения имеют приоритет над файлом .env в pydantic-settings.
os.environ["DIRIZHER_LLM__PROVIDER"] = "mock"
os.environ["DIRIZHER_LLM__GROQ_API_KEY"] = ""
os.environ["DIRIZHER_LLM__GIGACHAT_CREDENTIALS"] = ""
os.environ["DIRIZHER_YOUGILE__API_KEY"] = ""
os.environ["DIRIZHER_TELEGRAM__BOT_TOKEN"] = ""
os.environ["DIRIZHER_AUDIO__ENABLED"] = "false"
os.environ["DIRIZHER_MEMORY__BACKEND"] = "lexical"
os.environ["DIRIZHER_LLM__CONFIDENCE_THRESHOLD"] = "0.7"


# В окружении хакатона может не быть pytest-asyncio.
# Небольшой локальный адаптер запускает coroutine-тесты и async fixtures
# через стандартный asyncio, чтобы тесты оставались самодостаточными.


def pytest_pyfunc_call(pyfuncitem):
    test_func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_func):
        return None

    kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(test_func(**kwargs))
    return True


def pytest_fixture_setup(fixturedef, request):
    fixture_func = fixturedef.func
    if not inspect.iscoroutinefunction(fixture_func):
        return None

    kwargs = {name: request.getfixturevalue(name) for name in fixturedef.argnames}
    result = asyncio.run(fixture_func(**kwargs))
    fixturedef.cached_result = (result, fixturedef.cache_key(request), None)
    return result
