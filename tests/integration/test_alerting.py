import pytest
from prefect.automations import Automation
from prefect.blocks.notifications import CustomWebhookNotificationBlock

from bikeshare.pipeline.alerting import AUTOMATION_NAME, BLOCK_NAME, setup_alerts

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("prefect_server")]


def test_setup_creates_the_webhook_and_the_automation() -> None:
    automation_id = setup_alerts("http://alert-sink:8002/alerts")

    automation = Automation.read(id=automation_id)
    assert automation.name == AUTOMATION_NAME
    assert automation.enabled
    assert set(automation.trigger.expect) == {
        "prefect.flow-run.Failed",
        "prefect.flow-run.Crashed",
        "prefect.flow-run.TimedOut",
    }
    assert automation.trigger.match_related.model_dump() == {
        "prefect.resource.role": "flow",
        "prefect.resource.name": "bikeshare-training",
    }
    assert CustomWebhookNotificationBlock.load(BLOCK_NAME).url == "http://alert-sink:8002/alerts"


def test_setup_can_be_replayed_without_duplicates() -> None:
    first = setup_alerts("http://alert-sink:8002/alerts")

    second = setup_alerts("http://autre-recepteur:8002/alerts")

    assert second == first
    block = CustomWebhookNotificationBlock.load(BLOCK_NAME)
    assert block.url == "http://autre-recepteur:8002/alerts"
