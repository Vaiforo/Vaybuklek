"""YouGile: статус «Готово» должен менять и флаг, и колонку."""

from __future__ import annotations

import json

import httpx

from dirizher.config import YouGileSettings
from dirizher.domain.enums import TaskStatus
from dirizher.domain.models import Task
from dirizher.integrations.yougile import YouGileBoard


async def test_complete_card_moves_to_done_column():
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    board = YouGileBoard(
        YouGileSettings(api_key="token", column_todo="todo", column_in_progress="doing", column_done="done")
    )
    await board._http.aclose()
    board._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")

    try:
        await board.complete_card("card-1")
    finally:
        await board.close()

    assert requests == [{"completed": True, "columnId": "done"}]


async def test_update_card_syncs_done_status_payload():
    requests: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={})

    board = YouGileBoard(
        YouGileSettings(api_key="token", column_todo="todo", column_in_progress="doing", column_done="done")
    )
    await board._http.aclose()
    board._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")
    task = Task(title="Закрытая задача", status=TaskStatus.done)

    try:
        await board.update_card("card-1", task)
    finally:
        await board.close()

    assert requests == [{"title": "Закрытая задача", "completed": True, "columnId": "done"}]
