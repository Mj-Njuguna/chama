import json
import logging

import pytest
from django.test import Client

from core import celery_signals
from core.correlation import HEADER, accept_or_new, bind_correlation_id, get_correlation_id
from core.logging import clear_context


def test_response_carries_generated_correlation_id(client: Client):
    response = client.get("/healthz")
    assert len(response[HEADER]) == 32


def test_valid_incoming_correlation_id_is_reused(client: Client):
    response = client.get("/healthz", headers={HEADER: "mobile-abc123-def"})
    assert response[HEADER] == "mobile-abc123-def"


@pytest.mark.parametrize(
    "bad",
    ["short", "has space in it", "x" * 65, 'inject"}{"level":"error', "line\nbreak-1234"],
)
def test_invalid_incoming_correlation_id_is_replaced(bad: str):
    assert accept_or_new(bad) != bad


def test_request_log_line_has_correlation_id_and_no_query_string(client: Client, caplog):
    caplog.set_level(logging.INFO, logger="request")
    response = client.get(
        "/api/v1/nothing-here?phone=0712345678", headers={HEADER: "corr-12345678"}
    )
    assert response.status_code == 404
    records = [r for r in caplog.records if r.name == "request"]
    assert records, "expected one access-log line"
    payload = records[-1].msg
    assert isinstance(payload, dict)
    assert payload["correlation_id"] == "corr-12345678"
    assert payload["path"] == "/api/v1/nothing-here"
    assert "0712345678" not in json.dumps(payload)


def test_context_is_cleared_after_request(client: Client):
    client.get("/healthz")
    assert get_correlation_id() is None


class _Req:
    def __init__(self, **attrs: object) -> None:
        self.__dict__.update(attrs)


class _Task:
    def __init__(self, request: object) -> None:
        self.request = request


def test_celery_publish_adds_current_correlation_id():
    clear_context()
    bind_correlation_id("corr-publish-1")
    headers: dict[str, str] = {}
    celery_signals.add_correlation_header(headers=headers)
    assert headers == {"correlation_id": "corr-publish-1"}
    clear_context()


@pytest.mark.parametrize(
    "request_obj",
    [_Req(correlation_id="corr-worker-1"), _Req(headers={"correlation_id": "corr-worker-1"})],
)
def test_celery_worker_binds_incoming_correlation_id(request_obj):
    celery_signals.bind_task_correlation(task=_Task(request_obj))
    assert get_correlation_id() == "corr-worker-1"
    celery_signals.clear_task_context()
    assert get_correlation_id() is None


def test_celery_worker_mints_id_when_none_sent():
    celery_signals.bind_task_correlation(task=_Task(_Req()))
    value = get_correlation_id()
    assert value is not None
    assert len(value) == 32
    celery_signals.clear_task_context()
