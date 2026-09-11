import json
import logging

import pytest

from service.logging import configure_logging
from service.service import logger as service_logger
from service.service import request_id_middleware
from service.utils import REQUEST_ID_HEADER


def test_request_logs_are_structured_and_correlated(test_client, caplog) -> None:
    caplog.set_level(logging.INFO, logger=service_logger.name)

    first = test_client.get("/health")
    second = test_client.get("/health")

    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.headers[REQUEST_ID_HEADER]
    second_id = second.headers[REQUEST_ID_HEADER]
    assert first_id != second_id

    request_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) in {"request.started", "request.completed"}
        and getattr(record, "path", None) == "/health"
    ]
    assert len(request_records) >= 4
    assert all(record.request_id in {first_id, second_id} for record in request_records)
    assert {record.event for record in request_records if record.request_id == first_id} == {
        "request.started",
        "request.completed",
    }
    assert {record.event for record in request_records if record.request_id == second_id} == {
        "request.started",
        "request.completed",
    }

    structured_lines = [
        json.loads(line) for line in caplog.text.splitlines() if line.startswith("{")
    ]
    assert any(
        line["event"] == "request.completed" and line["request_id"] == first_id
        for line in structured_lines
    )


def test_redaction_removes_secrets_from_captured_logs(caplog) -> None:
    secret_api_key = "sk-test-api-key-123"
    secret_password = "db-password-456"
    bearer_token = "bearer-token-789"
    opaque_configured_secret = "opaque-configured-secret"
    configure_logging(secret_values=[opaque_configured_secret])

    test_logger = logging.getLogger("service.test.redaction")
    with caplog.at_level(logging.INFO, logger=test_logger.name):
        test_logger.info(
            "provider payload=%s",
            {
                "api_key": secret_api_key,
                "nested": [{"password": secret_password, "host": "localhost"}],
                "model": "gpt-test",
            },
        )
        test_logger.info("Authorization: Bearer %s", bearer_token)
        test_logger.info("postgresql://user:%s@localhost:5432/app", secret_password)
        test_logger.info("configured=%s", opaque_configured_secret)
        try:
            raise RuntimeError(f"provider failed password={secret_password}")
        except RuntimeError:
            test_logger.exception("provider request failed")

    output = caplog.text
    assert secret_api_key not in output
    assert secret_password not in output
    assert bearer_token not in output
    assert opaque_configured_secret not in output
    assert "localhost" in output
    assert "gpt-test" in output
    assert "RuntimeError" in output
    assert "[REDACTED]" in output


@pytest.mark.asyncio
async def test_request_context_is_reset_after_middleware() -> None:
    from fastapi import Request
    from starlette.responses import Response

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/health",
            "raw_path": b"/health",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )

    async def call_next(_: Request) -> Response:
        return Response(status_code=204)

    await request_id_middleware(request, call_next)

    from service.logging import current_request_id

    assert current_request_id() is None
