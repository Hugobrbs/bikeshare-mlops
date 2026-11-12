import pytest

from bikeshare.data.acquisition import AcquisitionError, IntegrityError
from bikeshare.pipeline.flow import Stopwatch, outcome_of, retry_if_transient
from bikeshare.registry.policy import Decision


class FailedState:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def result(self) -> None:
        raise self.error


@pytest.mark.parametrize(
    ("error", "retried"),
    [
        (AcquisitionError("HTTP 503", transient=True), True),
        (AcquisitionError("HTTP 404", transient=False), False),
        (IntegrityError("mauvaise empreinte"), False),
        (ValueError("bug"), False),
    ],
    ids=["panne-passagere", "erreur-definitive", "fichier-corrompu", "bug"],
)
def test_only_transient_download_errors_are_retried(error: Exception, retried: bool) -> None:
    assert retry_if_transient(None, None, FailedState(error)) is retried


@pytest.mark.parametrize(
    ("decision", "outcome"),
    [
        (Decision("approved", True), "promoted"),
        (Decision("approved", False), "champion_kept"),
        (Decision("rejected", False), "rejected"),
    ],
)
def test_outcome_of(decision: Decision, outcome: str) -> None:
    assert outcome_of(decision) == outcome


def test_stopwatch_keeps_duration_even_when_the_step_fails() -> None:
    watch = Stopwatch()

    with watch.step("validation"):
        pass
    with pytest.raises(RuntimeError), watch.step("entrainement"):
        raise RuntimeError("échec")

    assert set(watch.durations) == {"validation", "entrainement"}
    assert all(duration >= 0 for duration in watch.durations.values())
