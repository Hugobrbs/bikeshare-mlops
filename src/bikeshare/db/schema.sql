-- Rejouable : tout est en IF NOT EXISTS. Les rôles sont créés avant par db/migrate.py.

CREATE SCHEMA IF NOT EXISTS tracing;
CREATE SCHEMA IF NOT EXISTS ops;

-- version exacte chargée depuis MLflow (jamais seulement l'alias, qui peut bouger)
CREATE TABLE IF NOT EXISTS tracing.model_versions (
    model_id uuid PRIMARY KEY,
    tracking_uri text NOT NULL,
    registered_name text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    mlflow_run_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tracking_uri, registered_name, version)
);

-- une ligne par appel HTTP sur un endpoint métier, erreurs comprises
CREATE TABLE IF NOT EXISTS tracing.api_requests (
    request_id uuid PRIMARY KEY,
    started_at timestamptz NOT NULL,
    endpoint text NOT NULL CHECK (endpoint IN ('/predict', '/predict/batch', '/feedback')),
    http_status smallint NOT NULL CHECK (http_status BETWEEN 200 AND 599),
    item_count integer CHECK (item_count >= 0),
    -- du début du traitement jusqu'avant l'écriture en base
    processing_duration_ms double precision NOT NULL CHECK (processing_duration_ms >= 0),
    -- temps passé dans model.predict, NULL si aucune inférence
    inference_duration_ms double precision CHECK (inference_duration_ms >= 0),
    worker_pid integer NOT NULL,
    -- change à chaque démarrage de worker (un PID peut être réutilisé)
    instance_id uuid NOT NULL,
    api_revision text NOT NULL,
    campaign_id text NOT NULL DEFAULT 'manual' CHECK (length(campaign_id) BETWEEN 1 AND 128),
    error_code text,
    CHECK ((http_status < 400) = (error_code IS NULL))
);

CREATE TABLE IF NOT EXISTS tracing.predictions (
    prediction_id uuid PRIMARY KEY,
    request_id uuid NOT NULL REFERENCES tracing.api_requests (request_id),
    item_index integer NOT NULL CHECK (item_index BETWEEN 0 AND 99),
    model_id uuid NOT NULL REFERENCES tracing.model_versions (model_id),
    input_features jsonb NOT NULL,
    predicted_rentals double precision NOT NULL CHECK (predicted_rentals >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (request_id, item_index)
);

-- un seul feedback par prédiction, jamais modifié
CREATE TABLE IF NOT EXISTS tracing.feedbacks (
    feedback_id uuid PRIMARY KEY,
    -- vérifiée au commit : le feedback est inséré avant la ligne de requête,
    -- dont le statut (201 ou 200) dépend du résultat de cette insertion
    request_id uuid NOT NULL UNIQUE REFERENCES tracing.api_requests (request_id)
        DEFERRABLE INITIALLY DEFERRED,
    prediction_id uuid NOT NULL UNIQUE REFERENCES tracing.predictions (prediction_id),
    useful boolean,
    observed_rentals integer CHECK (observed_rentals >= 0),
    observed_at timestamptz,
    label_source text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (useful IS NOT NULL OR observed_rentals IS NOT NULL),
    CHECK (
        (observed_rentals IS NULL AND observed_at IS NULL AND label_source IS NULL)
        OR (observed_rentals IS NOT NULL AND observed_at IS NOT NULL AND label_source IS NOT NULL)
    )
);

-- CPU et mémoire de chaque worker, échantillonnés régulièrement
CREATE TABLE IF NOT EXISTS tracing.worker_heartbeats (
    instance_id uuid NOT NULL,
    worker_pid integer NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    cpu_percent real NOT NULL,
    rss_mb real NOT NULL,
    model_version integer,
    PRIMARY KEY (instance_id, recorded_at)
);

CREATE INDEX IF NOT EXISTS api_requests_started_idx ON tracing.api_requests (started_at);
CREATE INDEX IF NOT EXISTS api_requests_campaign_idx ON tracing.api_requests (campaign_id, started_at);
CREATE INDEX IF NOT EXISTS predictions_created_idx ON tracing.predictions (created_at);
CREATE INDEX IF NOT EXISTS heartbeats_recorded_idx ON tracing.worker_heartbeats (recorded_at);

CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    flow_run_id uuid PRIMARY KEY,
    flow_name text NOT NULL,
    started_at timestamptz NOT NULL,
    ended_at timestamptz,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    -- promoted, champion_kept, rejected, no_new_data, invalid_data, error, crashed
    outcome text,
    batch_ids text[] NOT NULL DEFAULT '{}',
    mlflow_run_id text,
    model_version integer,
    metrics jsonb,
    step_durations jsonb,
    error text,
    app_revision text NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.data_batches (
    id bigserial PRIMARY KEY,
    batch_id text NOT NULL,
    flow_run_id uuid,
    validated_at timestamptz NOT NULL DEFAULT now(),
    accepted boolean NOT NULL,
    row_count integer NOT NULL,
    failed_checks text[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ops.model_promotions (
    id bigserial PRIMARY KEY,
    changed_at timestamptz NOT NULL,
    action text NOT NULL CHECK (action IN ('promote', 'rollback')),
    model_name text NOT NULL,
    alias text NOT NULL,
    from_version integer,
    to_version integer NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.alerts (
    id bigserial PRIMARY KEY,
    received_at timestamptz NOT NULL DEFAULT now(),
    subject text NOT NULL,
    body text NOT NULL,
    forwarded boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS pipeline_runs_started_idx ON ops.pipeline_runs (started_at);

REVOKE ALL ON SCHEMA tracing, ops FROM PUBLIC;
GRANT USAGE ON SCHEMA tracing TO api_writer, grafana_reader;
GRANT USAGE ON SCHEMA ops TO pipeline_writer, grafana_reader;

-- l'API ajoute des traces mais ne peut ni les modifier ni les supprimer
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA tracing TO api_writer;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ops TO pipeline_writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA ops TO pipeline_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA tracing, ops TO grafana_reader;
