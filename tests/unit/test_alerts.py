import httpx
import pytest
from fastapi.testclient import TestClient

from bikeshare.alerts import create_app
from bikeshare.settings import Settings


class FakeOps:
    def __init__(self) -> None:
        self.alerts: list[tuple[str, str, bool]] = []

    def alert_received(self, subject: str, body: str, forwarded: bool) -> None:
        self.alerts.append((subject, body, forwarded))


ALERT = {"subject": "Pipeline bikeshare-training : Failed", "body": "lot 2011-07 rejeté"}


def make_client(ops: FakeOps, handler: object = None, forward_url: str | None = None) -> TestClient:
    transport = httpx.MockTransport(handler) if handler else None
    return TestClient(create_app(Settings(alert_forward_url=forward_url), ops, transport))


def test_alert_is_stored() -> None:
    ops = FakeOps()

    response = make_client(ops).post("/alerts", json=ALERT)

    assert response.status_code == 202
    assert response.json() == {"forwarded": False}
    assert ops.alerts == [(ALERT["subject"], ALERT["body"], False)]


def test_alert_is_forwarded_when_a_webhook_is_configured() -> None:
    ops, received = FakeOps(), []

    def webhook(request: httpx.Request) -> httpx.Response:
        received.append(request.read().decode())
        return httpx.Response(200)

    response = make_client(ops, webhook, "https://hooks.test/alertes").post("/alerts", json=ALERT)

    assert response.json() == {"forwarded": True}
    assert "lot 2011-07 rejeté" in received[0]
    assert ops.alerts[0][2] is True


def test_forward_failure_does_not_lose_the_alert(caplog: pytest.LogCaptureFixture) -> None:
    ops = FakeOps()

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = make_client(ops, broken, "https://hooks.test/services/JETON-SECRET")
    response = client.post("/alerts", json=ALERT)

    assert response.status_code == 202
    assert ops.alerts == [(ALERT["subject"], ALERT["body"], False)]
    assert "HTTP 500" in caplog.text
    assert "JETON-SECRET" not in caplog.text


@pytest.mark.parametrize("payload", [{}, {"subject": "sans corps"}])
def test_invalid_alert(payload: dict) -> None:
    assert make_client(FakeOps()).post("/alerts", json=payload).status_code == 422


def test_health() -> None:
    assert make_client(FakeOps()).get("/health").json() == {"status": "ok"}
