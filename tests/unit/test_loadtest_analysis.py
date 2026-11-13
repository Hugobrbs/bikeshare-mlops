from loadtests.analyze import aggregate, capacities, to_markdown


def run(config: str, users: int, rps: float, p95: int, failure_pct: float = 0.0) -> dict:
    return {
        "config": config,
        "cpus": "1.0",
        "workers": "1",
        "users": str(users),
        "rps": str(rps),
        "failure_pct": str(failure_pct),
        "p50_ms": "10",
        "p95_ms": str(p95),
        "p99_ms": str(p95 * 2),
        "cpu_mean_pct": "50",
        "memory_max_mb": "200",
    }


ROWS = [
    run("S", 10, 88, 30),
    run("S", 10, 90, 40),
    run("S", 20, 130, 140),
    run("S", 20, 136, 180),
    run("S", 40, 120, 60, failure_pct=3.0),
]


def test_repeats_are_aggregated_with_the_median() -> None:
    [low, middle, high] = aggregate(ROWS)

    assert (low["users"], low["rps"], low["p95_ms"], low["repeats"]) == (10, 89, 35, 2)
    assert low["slo"]
    assert not middle["slo"]
    assert not high["slo"]


def test_capacity_is_the_highest_compliant_throughput() -> None:
    best = capacities(aggregate(ROWS))

    assert best["S"]["users"] == 10
    assert best["S"]["rps"] == 89


def test_no_compliant_level() -> None:
    levels = aggregate([run("XS", 10, 50, 900)])

    assert capacities(levels) == {"XS": None}
    assert "aucun palier conforme" in to_markdown(levels)


def test_markdown_has_one_line_per_level() -> None:
    report = to_markdown(aggregate(ROWS))

    assert report.count("| S | 1.0 | 1 |") == 3
    assert "| S | 89 | 10 utilisateurs | 35 | 89 |" in report
