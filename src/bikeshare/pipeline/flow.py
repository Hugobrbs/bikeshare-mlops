import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from mlflow.tracking import MlflowClient
from prefect import flow, get_run_logger, task
from prefect.cache_policies import NO_CACHE
from prefect.runtime import flow_run

from bikeshare.data.acquisition import AcquisitionError, fetch_source
from bikeshare.data.ingestion import ingest_pending
from bikeshare.data.replay import publish_batches
from bikeshare.data.store import BatchRecord, BatchStore
from bikeshare.data.validation import DataValidationError, following_month
from bikeshare.db.ops import OpsStore
from bikeshare.evaluation import evaluate
from bikeshare.features import FEATURE_VERSION
from bikeshare.pipeline_config import PipelineConfig, load_pipeline_config
from bikeshare.registry.models import ModelRegistry
from bikeshare.registry.policy import Decision, Evidence, PromotionPolicy, decide, load_policy
from bikeshare.registry.tracking import (
    RegisteredVersion,
    TrainingRun,
    flatten,
    load_model,
    reload_delta,
)
from bikeshare.reporting import (
    close_figure,
    forecast_figure,
    importance_figure,
    input_importance,
)
from bikeshare.settings import Settings, get_settings
from bikeshare.training import Split, TrainingResult, temporal_split, train

FLOW_NAME = "bikeshare-training"

# pas de cache ni de retry par défaut : chaque étape doit vraiment s'exécuter,
# et rejouer une étape déterministe qui a planté ne sert à rien
NO_RETRY = {"retries": 0, "cache_policy": NO_CACHE, "persist_result": False}


def retry_if_transient(task: object, task_run: object, state: object) -> bool:
    try:
        state.result()
    except AcquisitionError as e:
        return e.transient
    except Exception:
        return False
    return False


@task(
    name="acquisition",
    retries=3,
    retry_delay_seconds=[5, 20, 60],
    retry_condition_fn=retry_if_transient,
    timeout_seconds=180,
    cache_policy=NO_CACHE,
    persist_result=False,
)
def acquire(config: PipelineConfig, store: BatchStore) -> list[str]:
    """Récupère la source puis, si rien n'attend déjà, le ou les prochains lots."""
    logger = get_run_logger()
    snapshot = fetch_source(config.dataset, store.source_dir)
    pending, accepted = store.pending(), store.accepted()
    # un lot rejeté laisse un trou : on le relivre avant de toucher aux lots suivants déjà
    # en attente, sinon ils seraient rejetés à leur tour pour rupture de continuité
    gap = bool(accepted and pending and pending[0].stem != following_month(accepted[-1].batch_id))
    if not pending or gap:
        count = 1 if accepted else config.replay.bootstrap_batches
        publish_batches(
            snapshot.path, store.incoming_dir, delivered=store.delivered_ids(), count=count
        )
    pending = [p.stem for p in store.pending()]
    logger.info(
        "source %s (%s), lots en attente : %s", snapshot.sha256[:12], snapshot.origin, pending
    )
    return pending


@task(name="validation", **NO_RETRY)
def validate(
    store: BatchStore, config: PipelineConfig, ops: OpsStore, flow_run_id: str
) -> list[BatchRecord]:
    logger = get_run_logger()

    def trace(report: dict) -> None:
        ops.batch_validated(report, flow_run_id)
        failed = sorted({f["check"] for f in report["failures"]})
        logger.info(
            "lot %s : %d lignes, %d contrôles, échecs : %s",
            report["batch_id"],
            report["rows"],
            report["checks"],
            failed or "aucun",
        )

    return ingest_pending(store, config.validation, on_report=trace)


@task(name="preparation", **NO_RETRY)
def prepare(store: BatchStore, config: PipelineConfig) -> Split:
    logger = get_run_logger()
    frame = store.load(last=config.training.window_months)
    split = temporal_split(frame, config.training.test_days)
    logger.info(
        "train %d lignes, test %d lignes à partir du %s",
        len(split.train),
        len(split.test),
        split.cutoff.date(),
    )
    return split


@task(name="entrainement", timeout_seconds=900, **NO_RETRY)
def fit(split: Split, config: PipelineConfig) -> TrainingResult:
    result = train(split.train, config.training)
    get_run_logger().info("modèle entraîné en %.1f s", result.duration_seconds)
    return result


@task(name="evaluation", **NO_RETRY)
def assess(result: TrainingResult, split: Split, registry: ModelRegistry) -> Evidence:
    """Candidat, baseline et champion en place sont mesurés sur le même jeu de test."""
    logger = get_run_logger()
    evidence = Evidence(
        candidate=evaluate(result.model, split.test),
        baseline=evaluate(result.baseline, split.test),
    )
    champion = registry.champion()
    if champion is not None:
        model = load_model(f"models:/{registry.name}/{champion.version}")
        evidence.champion = evaluate(model, split.test)
        evidence.champion["version"] = int(champion.version)
    logger.info(
        "MAE candidat %.1f, baseline %.1f, champion %s",
        evidence.candidate["mae"],
        evidence.baseline["mae"],
        f"{evidence.champion['mae']:.1f}" if evidence.champion else "aucun",
    )
    return evidence


# publier deux fois ne crée pas deux versions (voir TrainingRun.log_model),
# donc on peut réessayer si MLflow a un passage à vide
@task(
    name="publication",
    retries=2,
    retry_delay_seconds=10,
    cache_policy=NO_CACHE,
    persist_result=False,
)
def publish(
    run: TrainingRun,
    result: TrainingResult,
    split: Split,
    evidence: Evidence,
    model_name: str,
    seed: int,
) -> RegisteredVersion:
    logger = get_run_logger()
    run.log_dataset(split.train, "train", "training")
    run.log_dataset(split.test, "test", "testing")
    metrics = {
        **flatten(evidence.candidate, "test"),
        **flatten(evidence.baseline, "baseline"),
        "training_duration_seconds": result.duration_seconds,
    }
    if evidence.champion:
        metrics.update(flatten(evidence.champion, "champion"))
    run.log_metrics(metrics)

    importance = input_importance(result.model, split.test, seed)
    run.log_json(importance, "evaluation/importance.json")
    figures = {
        "evaluation/importance.png": importance_figure(importance),
        "evaluation/previsions.png": forecast_figure(result.model, split.test),
    }
    for path, figure in figures.items():
        run.log_figure(figure, path)
        # le processus vit longtemps : sans ça les figures s'accumulent en mémoire
        close_figure(figure)

    version = run.log_model(
        result.model, split.test, model_name, {"feature_version": FEATURE_VERSION}
    )
    evidence.reload_delta = reload_delta(version, result.model, split.test)
    logger.info(
        "version %s enregistrée, écart après rechargement : %g",
        version.version,
        evidence.reload_delta,
    )
    return version


@task(name="decision", **NO_RETRY)
def decide_promotion(
    version: RegisteredVersion,
    evidence: Evidence,
    policy: PromotionPolicy,
    registry: ModelRegistry,
    ops: OpsStore,
) -> Decision:
    logger = get_run_logger()
    decision = decide(evidence, policy)
    registry.record_decision(version.version, decision, policy)
    if decision.promote:
        change = registry.promote(version.version, actor="pipeline", reason=decision.reasons[0])
        ops.alias_changed(change)
    logger.info(
        "version %s : %s, promue : %s (%s)",
        version.version,
        decision.status,
        decision.promote,
        " ; ".join(decision.reasons),
    )
    return decision


class Stopwatch:
    def __init__(self) -> None:
        self.durations: dict[str, float] = {}

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.durations[name] = round(time.perf_counter() - start, 3)


def outcome_of(decision: Decision) -> str:
    if decision.status == "rejected":
        return "rejected"
    return "promoted" if decision.promote else "champion_kept"


def run_steps(
    settings: Settings,
    config: PipelineConfig,
    policy: PromotionPolicy,
    store: BatchStore,
    ops: OpsStore,
    registry: ModelRegistry,
    flow_run_id: str,
    watch: Stopwatch,
) -> dict:
    with watch.step("acquisition"):
        acquire(config, store)
    with watch.step("validation"):
        accepted = validate(store, config, ops, flow_run_id)
    if not accepted:
        return {"outcome": "no_new_data"}

    batch_ids = [r.batch_id for r in accepted]
    with watch.step("preparation"):
        split = prepare(store, config)

    training = config.training
    run = TrainingRun.start(
        settings.mlflow_tracking_uri,
        settings.mlflow_experiment_name,
        f"train-{batch_ids[-1]}",
        {
            "flow_run_id": flow_run_id,
            # batch_ids : lots arrivés pendant ce run. training_batches : toute la fenêtre
            # d'entraînement, qui contient aussi des lots acceptés lors de runs précédents
            "batch_ids": ",".join(batch_ids),
            "training_batches": ",".join(
                r.batch_id for r in store.accepted()[-training.window_months :]
            ),
            "data_fingerprint": store.fingerprint(last=training.window_months),
            "git_commit": settings.app_revision,
            "feature_version": FEATURE_VERSION,
            "policy": policy.label,
        },
    )
    try:
        run.log_params(
            {
                "seed": training.seed,
                "window_months": training.window_months,
                "test_days": training.test_days,
                "test_from": split.cutoff.isoformat(),
                "train_rows": len(split.train),
                "test_rows": len(split.test),
                **{f"model.{k}": v for k, v in training.model.model_dump().items()},
            }
        )
        for batch_id in batch_ids:
            report = (store.reports_dir / f"{batch_id}.json").read_text(encoding="utf-8")
            run.client.log_text(run.run_id, report, f"validation/{batch_id}.json")

        with watch.step("entrainement"):
            result = fit(split, config)
        with watch.step("evaluation"):
            evidence = assess(result, split, registry)
        with watch.step("publication"):
            version = publish(run, result, split, evidence, registry.name, training.seed)
        with watch.step("decision"):
            decision = decide_promotion(version, evidence, policy, registry, ops)
    except Exception:
        run.finish("FAILED")
        raise

    run.set_tags({"decision": decision.status, "promoted": decision.promote})
    run.finish()
    return {
        "outcome": outcome_of(decision),
        "batch_ids": batch_ids,
        "mlflow_run_id": run.run_id,
        "model_version": version.version,
        "metrics": {
            "mae": evidence.candidate["mae"],
            "r2": evidence.candidate["r2"],
            "baseline_mae": evidence.baseline["mae"],
            "champion_mae": evidence.champion["mae"] if evidence.champion else None,
        },
    }


@flow(name=FLOW_NAME, retries=0)
def training_pipeline(config_path: str | None = None) -> dict:
    """Un cycle complet : nouveau lot, contrôles, entraînement, publication, décision."""
    settings = get_settings()
    config = load_pipeline_config(Path(config_path) if config_path else settings.pipeline_config)
    policy = load_policy(settings.promotion_policy)
    store = BatchStore(settings.data_dir)
    ops = OpsStore(
        settings.ops_database_url.get_secret_value() if settings.ops_database_url else None
    )
    registry = ModelRegistry(
        MlflowClient(settings.mlflow_tracking_uri), settings.model_name, settings.model_alias
    )
    # hors d'un run Prefect (appel direct de la fonction), il n'y a pas d'identifiant
    flow_run_id = str(flow_run.id or uuid4())
    watch = Stopwatch()

    ops.run_started(flow_run_id, FLOW_NAME, settings.app_revision)
    try:
        summary = run_steps(settings, config, policy, store, ops, registry, flow_run_id, watch)
    except Exception as e:
        outcome = "invalid_data" if isinstance(e, DataValidationError) else "error"
        ops.run_finished(
            flow_run_id,
            status="failed",
            outcome=outcome,
            step_durations=watch.durations,
            error=str(e),
        )
        raise
    ops.run_finished(flow_run_id, status="completed", step_durations=watch.durations, **summary)
    get_run_logger().info("résultat : %s", summary)
    return summary
