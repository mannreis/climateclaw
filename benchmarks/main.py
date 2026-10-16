"""Benchmark base vs head and write the Markdown report.

    uv run python benchmarks/main.py                         # main vs working tree
    uv run python benchmarks/main.py --base v1.2 --head my-branch --rounds 5
    uv run python benchmarks/main.py --base-root .bench-base --head-root .bench-head

Refs (--base/--head) are checked out into temporary git worktrees; without
--head the current working tree is benchmarked, uncommitted changes included.
CI passes its own checkouts with --base-root/--head-root instead.

Base and head run in alternating rounds (base, head, head, base, ...) with a
fresh server each time, so drift in machine speed hits both sides alike; each
side's result per concurrency level is the median over its rounds.

Components: each ``--component NAME`` is a directory ``benchmarks/NAME/`` with
a ``run.sh`` that benchmarks the code at ``$BENCH_PROJECT_ROOT`` and accepts
``-c USERS,... -d SECONDS --warmup SECONDS --json FILE``, writing a list of
``{concurrency, ok, errors, rps, ttfb_p50_ms, p50_ms, p95_ms, p99_ms}``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import report

BENCH_DIR = Path(__file__).resolve().parent
REPO = BENCH_DIR.parent
LATENCIES = ("ttfb_p50_ms", "p50_ms", "p95_ms", "p99_ms")


@dataclass(frozen=True)
class Side:
    label: str  # "base" or "head"
    name: str  # branch, tag or commit as given (shown in the report)
    root: Path  # checkout whose code is benchmarked
    sha: str


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def resolve(
    label: str,
    ref: str | None,
    root: str | None,
    name: str | None,
    tmp: Path,
    stack: ExitStack,
) -> Side:
    if root:  # checkout prepared by the caller (CI)
        path = Path(root).resolve()
        return Side(
            label, name or ref or path.name, path, git("rev-parse", "HEAD", cwd=path)
        )
    if ref:
        path = tmp / label
        git("worktree", "add", "--quiet", "--detach", str(path), ref)
        stack.callback(git, "worktree", "remove", "--force", str(path))
        subprocess.run(["uv", "sync", "--quiet", "--project", str(path)], check=True)
        return Side(label, name or ref, path, git("rev-parse", "HEAD", cwd=path))
    # The working tree as it is, uncommitted changes included.
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if git("status", "--porcelain", "--untracked-files=no"):
        branch += " (uncommitted changes)"
    return Side(label, name or branch, REPO, git("rev-parse", "HEAD"))


def run_round(
    component: str, side: Side, n: int, rounds: int, out_dir: Path, args: list[str]
) -> Path:
    out = out_dir / f"{component}-{side.label}-round{n}.json"
    print(f"== {component} round {n}/{rounds}: {side.label} ({side.name})", flush=True)
    subprocess.run(
        [str(BENCH_DIR / component / "run.sh"), *args, "--json", str(out)],
        env={**os.environ, "BENCH_PROJECT_ROOT": str(side.root)},
        check=True,
    )
    return out


def _median(xs: list[float]) -> float:
    xs = [x for x in xs if not math.isnan(x)]
    return statistics.median(xs) if xs else float("nan")


def merge(rounds: list[list[dict]]) -> dict[int, dict]:
    """Per level: median rate and latencies, summed counts, min/max spread."""
    by_level: dict[int, list[dict]] = {}
    for results in rounds:
        for r in results:
            by_level.setdefault(r["concurrency"], []).append(r)

    merged = {}
    for level, rs in sorted(by_level.items()):
        errors: dict[str, int] = {}
        for r in rs:
            for k, v in r["errors"].items():
                errors[k] = errors.get(k, 0) + v
        # Latencies only exist for rounds that completed requests.
        done = [r for r in rs if r["ok"] > 0]
        rps = [r["rps"] for r in rs]
        merged[level] = {
            "concurrency": level,
            "rounds": len(rs),
            "ok": sum(r["ok"] for r in rs),
            "errors": errors,
            "rps": _median(rps),
            "rps_min": min(rps),
            "rps_max": max(rps),
            **{k: _median([r[k] for r in done]) for k in LATENCIES},
            "p95_ms_min": min((r["p95_ms"] for r in done), default=math.nan),
            "p95_ms_max": max((r["p95_ms"] for r in done), default=math.nan),
        }
    return merged


def origin_url() -> str:
    """https://github.com/<owner>/<repo> from the origin remote, for links."""
    try:
        url = git("remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        return ""
    if url.startswith("git@"):
        host, _, path = url[4:].partition(":")
        url = f"https://{host}/{path}"
    return url.removesuffix(".git")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    side = p.add_argument_group("base and head")
    side.add_argument("--base", default="main", help="ref to compare against")
    side.add_argument("--head", help="ref to benchmark (default: working tree)")
    side.add_argument("--base-root", help="use this checkout as base (CI)")
    side.add_argument("--head-root", help="use this checkout as head (CI)")
    side.add_argument("--base-name", help="name shown for base (default: the ref)")
    side.add_argument("--head-name", help="name shown for head (default: the ref)")
    run = p.add_argument_group("benchmark")
    run.add_argument(
        "--component",
        action="append",
        help="benchmarks/<NAME>/ to run; repeatable (default: backend)",
    )
    run.add_argument("-c", "--concurrency", default="1,4,16,64")
    run.add_argument("-d", "--duration", type=float, default=10.0)
    run.add_argument("--warmup", type=float, default=3.0)
    run.add_argument("--rounds", type=int, default=3)
    out = p.add_argument_group("output")
    out.add_argument("-o", "--out", default="benchmark-report.md")
    out.add_argument(
        "--results-dir", help="keep per-round and merged JSON here (default: discard)"
    )
    out.add_argument("--repo-url", help="for commit links (default: from origin)")
    args = p.parse_args()

    components = args.component or ["backend"]
    for c in components:
        if not (BENCH_DIR / c / "run.sh").is_file():
            p.error(f"unknown component {c!r}: benchmarks/{c}/run.sh not found")
    loadgen_args = [
        "-c", args.concurrency, "-d", str(args.duration), "--warmup", str(args.warmup)
    ]  # fmt: skip

    with ExitStack() as stack:
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        base = resolve("base", args.base, args.base_root, args.base_name, tmp, stack)
        head = resolve("head", args.head, args.head_root, args.head_name, tmp, stack)
        results_dir = Path(args.results_dir) if args.results_dir else tmp / "results"
        results_dir.mkdir(parents=True, exist_ok=True)

        started = datetime.now(UTC)
        sections = []
        for component in components:
            files: dict[str, list[Path]] = {"base": [], "head": []}
            for n in range(1, args.rounds + 1):
                order = (base, head) if n % 2 else (head, base)
                for s in order:
                    files[s.label].append(
                        run_round(
                            component, s, n, args.rounds, results_dir, loadgen_args
                        )
                    )
            merged = {}
            for label, paths in files.items():
                merged[label] = merge([json.loads(f.read_text()) for f in paths])
                (results_dir / f"{component}-{label}.json").write_text(
                    json.dumps(list(merged[label].values()), indent=2)
                )
            sections.append((component, merged["base"], merged["head"]))

        repo_url = args.repo_url if args.repo_url is not None else origin_url()
        Path(args.out).write_text(
            report.render(
                sections,
                report.label(base.name, base.sha, repo_url),
                report.label(head.name, head.sha, repo_url),
                started,
            )
        )
    print(f"Report written to {args.out}")


if __name__ == "__main__":
    main()
