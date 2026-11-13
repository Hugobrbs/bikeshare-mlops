import pytest
from prefect import get_client
from prefect.states import Completed, Running

from bikeshare.db.ops import OpsStore
from bikeshare.pipeline.flow import training_pipeline
from bikeshare.pipeline.recovery import mark_orphaned_runs

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("prefect_server")]


def test_running_runs_left_by_a_dead_process_become_crashed() -> None:
    with get_client(sync_client=True) as client:
        orphan = client.create_flow_run(training_pipeline, state=Running())
        finished = client.create_flow_run(training_pipeline, state=Completed())

        marked = mark_orphaned_runs(OpsStore(None))

        assert marked == [str(orphan.id)]
        assert client.read_flow_run(orphan.id).state.is_crashed()
        assert client.read_flow_run(finished.id).state.is_completed()
        assert mark_orphaned_runs(OpsStore(None)) == []
