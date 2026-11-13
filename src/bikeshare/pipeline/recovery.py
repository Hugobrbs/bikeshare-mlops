from prefect import get_client
from prefect.client.schemas.filters import (
    FlowFilter,
    FlowFilterName,
    FlowRunFilter,
    FlowRunFilterState,
    FlowRunFilterStateType,
)
from prefect.client.schemas.objects import StateType
from prefect.client.schemas.sorting import FlowRunSort
from prefect.states import Crashed

from bikeshare.db.ops import OpsStore
from bikeshare.pipeline.flow import FLOW_NAME

MESSAGE = "exécutant arrêté pendant l'exécution"


def mark_orphaned_runs(ops: OpsStore) -> list[str]:
    running = FlowRunFilterState(type=FlowRunFilterStateType(any_=[StateType.RUNNING]))
    with get_client(sync_client=True) as client:
        runs = client.read_flow_runs(
            flow_filter=FlowFilter(name=FlowFilterName(any_=[FLOW_NAME])),
            flow_run_filter=FlowRunFilter(state=running),
            sort=FlowRunSort.START_TIME_DESC,
            limit=50,
        )
        for run in runs:
            client.set_flow_run_state(run.id, Crashed(message=MESSAGE), force=True)
            ops.run_finished(str(run.id), status="failed", outcome="crashed", error=MESSAGE)
    return [str(run.id) for run in runs]
