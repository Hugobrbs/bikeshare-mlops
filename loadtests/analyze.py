import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

SLO_P95_MS = 150
SLO_FAILURE_PCT = 1.0

METRICS = ("rps", "failure_pct", "p50_ms", "p95_ms", "p99_ms", "cpu_mean_pct", "memory_max_mb")


def load(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def meets_slo(row: dict) -> bool:
    return float(row["p95_ms"]) < SLO_P95_MS and float(row["failure_pct"]) < SLO_FAILURE_PCT


def aggregate(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["config"], row["cpus"], row["workers"], int(row["users"]))].append(row)

    levels = []
    for (config, cpus, workers, users), repeats in sorted(
        groups.items(), key=lambda item: (float(item[0][1]), item[0][0], item[0][3])
    ):
        level = {"config": config, "cpus": cpus, "workers": workers, "users": users}
        for metric in METRICS:
            level[metric] = statistics.median(float(r[metric]) for r in repeats)
        level["repeats"] = len(repeats)
        level["slo"] = all(meets_slo(r) for r in repeats)
        levels.append(level)
    return levels


def capacities(levels: list[dict]) -> dict[str, dict | None]:
    best: dict[str, dict | None] = {}
    for level in levels:
        best.setdefault(level["config"], None)
        current = best[level["config"]]
        if level["slo"] and (current is None or level["rps"] > current["rps"]):
            best[level["config"]] = level
    return best


def to_markdown(levels: list[dict]) -> str:
    lines = [
        f"Objectif de service : p95 < {SLO_P95_MS} ms et moins de {SLO_FAILURE_PCT:g} % d'échecs.",
        "",
        "| Config | CPU | Workers | Utilisateurs | req/s | p50 ms | p95 ms | p99 ms | Echecs % "
        "| CPU moyen % | Mémoire max Mo | Objectif |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for level in levels:
        lines.append(
            f"| {level['config']} | {level['cpus']} | {level['workers']} | {level['users']} "
            f"| {level['rps']:.0f} | {level['p50_ms']:.0f} | {level['p95_ms']:.0f} "
            f"| {level['p99_ms']:.0f} | {level['failure_pct']:.2f} | {level['cpu_mean_pct']:.0f} "
            f"| {level['memory_max_mb']:.0f} | {'oui' if level['slo'] else 'non'} |"
        )

    lines += [
        "",
        "| Config | Capacité soutenable (req/s) | Palier | p95 ms | req/s par CPU |",
        "|---|---|---|---|---|",
    ]
    for config, level in capacities(levels).items():
        if level is None:
            lines.append(f"| {config} | aucun palier conforme | | | |")
            continue
        per_cpu = level["rps"] / float(level["cpus"])
        lines.append(
            f"| {config} | {level['rps']:.0f} | {level['users']} utilisateurs "
            f"| {level['p95_ms']:.0f} | {per_cpu:.0f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, help="dossier d'une campagne")
    args = parser.parse_args()

    report = to_markdown(aggregate(load(args.results / "summary.csv")))
    (args.results / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
