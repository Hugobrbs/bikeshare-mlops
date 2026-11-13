from prefect.automations import Automation
from prefect.blocks.notifications import CustomWebhookNotificationBlock
from prefect.events.actions import SendNotification
from prefect.events.schemas.automations import EventTrigger, Posture
from prefect.exceptions import ObjectNotFound

from bikeshare.pipeline.flow import FLOW_NAME

BLOCK_NAME = "bikeshare-alert-sink"
AUTOMATION_NAME = "bikeshare-flow-failure"

DESCRIPTION = "Alerte quand le pipeline d'entraînement échoue ou plante (v1)"

SUBJECT = "Pipeline {{ flow.name }} : {{ flow_run.state.name }}"
BODY = """Flow run : {{ flow_run.name }} ({{ flow_run.id }})
Etat : {{ flow_run.state.name }}
Message : {{ flow_run.state.message }}
Date : {{ flow_run.state.timestamp }}
Interface : {{ flow_run|ui_url }}"""


def setup_alerts(sink_url: str) -> str:
    block = CustomWebhookNotificationBlock(
        name=BLOCK_NAME,
        url=sink_url,
        method="POST",
        json_data={"subject": "{{subject}}", "body": "{{body}}"},
        allow_private_urls=True,
    )
    block_id = block.save(BLOCK_NAME, overwrite=True)

    automation = Automation(
        name=AUTOMATION_NAME,
        description=DESCRIPTION,
        trigger=EventTrigger(
            expect={
                "prefect.flow-run.Failed",
                "prefect.flow-run.Crashed",
                "prefect.flow-run.TimedOut",
            },
            match={"prefect.resource.id": "prefect.flow-run.*"},
            match_related={"prefect.resource.role": "flow", "prefect.resource.name": FLOW_NAME},
            posture=Posture.Reactive,
            threshold=1,
            within=0,
        ),
        actions=[SendNotification(block_document_id=block_id, subject=SUBJECT, body=BODY)],
    )
    try:
        existing = Automation.read(name=AUTOMATION_NAME)
    except (ObjectNotFound, ValueError):
        existing = None
    if existing is not None:
        if existing.description == DESCRIPTION:
            return str(existing.id)
        # une automation mise à jour ne se déclenche plus (prefect 3.8.8) : on la recrée
        existing.delete()
    return str(automation.create().id)
