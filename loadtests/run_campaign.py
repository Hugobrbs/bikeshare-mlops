import argparse
import csv
import os
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_URL = "http://localhost:8001"

CONFIGS = [
    {"name": "S", "cpus": "1.0", "memory": "768M", "workers": 1, "omp_threads": 1},
    {"name": "M", "cpus": "2.0", "memory": "1280M", "workers": 2, "omp_threads": 1},
    {"name": "L", "cpus": "4.0", "memory": "2304M", "workers": 4, "omp_threads": 1},
    {"name": "M0", "cpus": "2.0", "memory": "1280M", "workers": 2, "omp_threads": 2},
]
DEFAULT_CONFIGS = ["S", "M", "L"]
USER_LEVELS = [5, 10, 20, 40, 80, 160]
LOCUST_PROCESSES = 4


def compose(*args: str, env: dict | None = None) -> None:
    subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        check=True,
        capture_output=True,
    )


def wait_ready(timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{API_URL}/health/ready", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise TimeoutError("l'API n'est pas prête")


def start_api(config: dict) -> None:
    env = {
        "API_CPUS": config["cpus"],
        "API_MEMORY": config["memory"],
        "API_WORKERS": str(config["workers"]),
        "API_OMP_THREADS": str(config["omp_threads"]),
    }
    compose("up", "-d", "--force-recreate", "--no-deps", "api", env=env)
    wait_ready()
    time.sleep(5 + 2 * config["workers"])


class StatsSampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[tuple[float, float]] = []
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.is_set():
            output = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.CPUPerc}};{{.MemUsage}}",
                    "bikeshare-api-1",
                ],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if output:
                cpu, memory = output.split(";")
                self.samples.append((float(cpu.rstrip("%")), to_megabytes(memory.split("/")[0])))

    def stop(self) -> tuple[float, float, float]:
        self.stop_event.set()
        self.join()
        if not self.samples:
            return 0.0, 0.0, 0.0
        cpus = [s[0] for s in self.samples]
        return sum(cpus) / len(cpus), max(cpus), max(s[1] for s in self.samples)


def to_megabytes(value: str) -> float:
    value = value.strip()
    for unit, factor in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024)):
        if value.endswith(unit):
            return float(value[: -len(unit)]) * factor
    return 0.0


def locust(
    user_class: str, users: int, duration: int, campaign: str, csv_prefix: Path | None
) -> None:
    command = (
        f"uv run --no-sync locust -f loadtests/locustfile.py {user_class} --host {API_URL} "
        f"--headless -u {users} -r {users} -t {duration}s --only-summary "
        f"--processes {LOCUST_PROCESSES}"
    ).split()
    stats = Path(f"{csv_prefix}_stats.csv") if csv_prefix else None
    if stats:
        stats.unlink(missing_ok=True)
        command += ["--csv", str(csv_prefix)]
    # code de sortie 1 = des requêtes en échec, pas une erreur du script
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "CAMPAIGN_ID": campaign},
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1) or (stats and not stats.exists()):
        raise RuntimeError(f"Locust n'a pas tourné : {result.stderr.strip()[-300:]}")


def read_aggregate(csv_prefix: Path) -> dict:
    with Path(f"{csv_prefix}_stats.csv").open() as f:
        rows = list(csv.DictReader(f))
    return next(row for row in rows if row["Name"] == "Aggregated")


def percentile(stats: dict, name: str) -> int | None:
    return int(stats[name]) if stats[name].isdigit() else None


def append_row(path: Path, row: dict) -> None:
    is_new = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="nom du dossier de résultats")
    parser.add_argument("--user-class", default="OnlineUser")
    parser.add_argument("--duration", type=int, default=40)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--levels", type=int, nargs="+", default=USER_LEVELS)
    parser.add_argument("--configs", nargs="+", default=DEFAULT_CONFIGS)
    args = parser.parse_args()

    out = ROOT / "loadtests" / "results" / args.name
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    for config in (c for c in CONFIGS if c["name"] in args.configs):
        start_api(config)
        locust(args.user_class, 5, args.warmup, f"warmup-{config['name']}", None)
        for users in args.levels:
            for repeat in range(1, args.repeats + 1):
                campaign = f"{args.name}-{config['name']}-u{users}-r{repeat}"
                sampler = StatsSampler()
                sampler.start()
                locust(args.user_class, users, args.duration, campaign, raw / campaign)
                cpu_mean, cpu_max, memory_max = sampler.stop()
                stats = read_aggregate(raw / campaign)
                requests, failures = int(stats["Request Count"]), int(stats["Failure Count"])
                row = {
                    "config": config["name"],
                    "cpus": config["cpus"],
                    "workers": config["workers"],
                    "scenario": args.user_class,
                    "users": users,
                    "repeat": repeat,
                    "requests": requests,
                    "rps": round(float(stats["Requests/s"]), 1),
                    "failure_pct": round(100 * failures / max(requests, 1), 2),
                    "p50_ms": percentile(stats, "50%"),
                    "p95_ms": percentile(stats, "95%"),
                    "p99_ms": percentile(stats, "99%"),
                    "cpu_mean_pct": round(cpu_mean),
                    "cpu_max_pct": round(cpu_max),
                    "memory_max_mb": round(memory_max),
                    "campaign_id": campaign,
                }
                append_row(out / "summary.csv", row)
                print(row, flush=True)
                time.sleep(3)


if __name__ == "__main__":
    main()
