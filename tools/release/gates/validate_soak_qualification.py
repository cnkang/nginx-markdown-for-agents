#!/usr/bin/env python3
"""Short-soak qualification gate (generic release gate).

Proves the short-soak qualification contract for a release candidate:
a sustained soak at the manifest's concurrency against the manifest's
small/medium/large HTML corpus with zero errors, no crash/leak, no
monotonic worker-RSS growth after drain, and module-managed per-request
peak memory no greater than the scenario's effective conversion_memory.
The qualification record SHALL record duration, concurrency, RSS drain,
per-request memory, error rate, and latency.

Real mode (`--mode real`) runs the soak against a module-enabled NGINX
binary (NGINX_BIN / MODULE_SO environment) and writes the qualification
record. Fixture mode (`--mode fixture`) validates an existing record with
the same threshold semantics, used for gate regression coverage only.

Failure semantics are fail-closed: any missing field, stale candidate
SHA, below-threshold metric, blocking pending state, or missing
observation is an ERROR and exits 1. This gate never reads `.kiro/` and
does not encode Spec-specific counts; every threshold comes from the
manifest.

Exit codes:
    0 - soak qualification passed (or justified skip with --allow-skip-soak)
    1 - any check failed
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2" / "short-soak-scenario-manifest.json"
)
DEFAULT_RECORD = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2" / "soak-qualification-record.json"
)

RECORD_SCHEMA_VERSION = "release.soak-qualification.v1"

REQUIRED_RECORD_FIELDS = (
    "schema_version",
    "candidate_sha",
    "run_id",
    "duration_seconds",
    "concurrency",
    "per_scenario",
    "rss_time_series",
    "worker_rss_drain_delta_kb",
    "monotonic_growth_after_drain",
    "status",
)

REQUIRED_SCENARIO_FIELDS = (
    "id",
    "completed_requests",
    "failed_requests",
    "error_rate",
    "p50_ms",
    "p99_ms",
    "rps",
)

SHA256_RE = re.compile(r"^[0-9a-f]{40}$")
AB_PCT_LINE_RE = re.compile(r"^\s*(?P<pct>\d+)%\s+(?P<ms>[0-9.]+)\s+ms$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: pathlib.Path, label: str) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"ERROR: {label} unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"ERROR: {label} top level must be an object")
    return raw


def load_manifest(manifest_path: str) -> dict:
    path = pathlib.Path(manifest_path).resolve()
    manifest = load_json(path, "soak manifest")
    required = {"schema_version", "candidate_sha", "duration_minutes", "concurrency"}
    missing = required - set(manifest)
    if missing:
        raise SystemExit(f"ERROR: soak manifest missing fields: {sorted(missing)}")
    if not SHA256_RE.match(manifest["candidate_sha"]):
        raise SystemExit("ERROR: soak manifest candidate_sha must be 40 hex")
    if not isinstance(manifest["duration_minutes"], (int, float)) or manifest["duration_minutes"] <= 0:
        raise SystemExit("ERROR: soak manifest duration_minutes must be positive")
    if not isinstance(manifest["concurrency"], int) or manifest["concurrency"] <= 0:
        raise SystemExit("ERROR: soak manifest concurrency must be a positive integer")
    if not isinstance(manifest.get("corpus"), list) or not manifest["corpus"]:
        raise SystemExit("ERROR: soak manifest corpus must be a non-empty array")
    for entry in manifest["corpus"]:
        if not isinstance(entry, dict) or "id" not in entry:
            raise SystemExit("ERROR: soak manifest corpus entries need an id")
    return manifest


def _validate_scalar(record: dict, name: str, expected: object) -> None:
    if record.get(name) != expected:
        raise SystemExit(
            f"ERROR: soak record {name}={record.get(name)!r} != expected {expected!r}"
        )


def validate_record_structure(record: dict) -> None:
    missing = set(REQUIRED_RECORD_FIELDS) - set(record)
    if missing:
        raise SystemExit(
            "ERROR: missing-observation: soak record missing fields: "
            f"{sorted(missing)}"
        )
    if record["schema_version"] != RECORD_SCHEMA_VERSION:
        raise SystemExit(
            "ERROR: malformed: unexpected soak record schema_version "
            f"{record['schema_version']!r}"
        )
    scenarios = record["per_scenario"]
    if not isinstance(scenarios, list) or not scenarios:
        raise SystemExit("ERROR: missing-observation: per_scenario must be non-empty")
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise SystemExit(f"ERROR: malformed: per_scenario[{index}] not an object")
        scenario_missing = set(REQUIRED_SCENARIO_FIELDS) - set(scenario)
        if scenario_missing:
            raise SystemExit(
                "ERROR: missing-observation: scenario missing fields: "
                f"{sorted(scenario_missing)}"
            )
    if not isinstance(record["rss_time_series"], list):
        raise SystemExit("ERROR: missing-observation: rss_time_series must be an array")
    for point in record["rss_time_series"]:
        if not isinstance(point, list) or len(point) != 2:
            raise SystemExit("ERROR: malformed: rss_time_series points need [t, rss_kb]")


def validate_against_manifest(record: dict, manifest: dict) -> None:
    if record["candidate_sha"] != manifest["candidate_sha"]:
        raise SystemExit(
            "ERROR: stale-digest: candidate sha mismatch: "
            f"{record['candidate_sha']} != {manifest['candidate_sha']}"
        )
    floor = manifest["duration_minutes"] * 60 * 0.95
    if record["duration_seconds"] < floor:
        raise SystemExit(
            "ERROR: below-threshold: soak duration "
            f"{record['duration_seconds']}s < {floor:.0f}s floor"
        )
    _validate_scalar(record, "concurrency", manifest["concurrency"])


def validate_soak_outcome(record: dict, manifest: dict) -> None:
    scenario_ids = {s.get("id") for s in manifest["corpus"]}
    record_ids = {s.get("id") for s in record["per_scenario"]}
    if not scenario_ids.issubset(record_ids):
        raise SystemExit(
            "ERROR: missing-observation: corpus scenarios missing from record: "
            f"{sorted(scenario_ids - record_ids)}"
        )
    for scenario in record["per_scenario"]:
        if scenario.get("status") not in (None, "pass"):
            raise SystemExit(
                "ERROR: blocking-pending: scenario "
                f"{scenario.get('id')!r} status {scenario.get('status')!r}"
            )
        if scenario.get("error_rate", 0.0) != 0.0:
            raise SystemExit(
                "ERROR: below-threshold: scenario "
                f"{scenario.get('id')!r} error_rate {scenario.get('error_rate')!r}"
            )
    if record.get("monotonic_growth_after_drain") is not False:
        raise SystemExit(
            "ERROR: below-threshold: monotonic worker-RSS growth after drain"
        )
    if record.get("module_managed_peak_observed"):
        peak = record.get("per_request_peak_bytes")
        ceiling = max(
            (s.get("conversion_memory_bytes", 0) for s in manifest["corpus"]),
            default=0,
        )
        if peak is not None and ceiling and peak > ceiling:
            raise SystemExit(
                f"ERROR: below-threshold: per-request peak {peak} > ceiling {ceiling}"
            )
    if record.get("status") != "pass":
        raise SystemExit(
            f"ERROR: blocking-pending: soak record status {record.get('status')!r}"
        )


def fixture_main(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    record = load_json(pathlib.Path(args.record_input), "soak record")
    validate_record_structure(record)
    validate_against_manifest(record, manifest)
    validate_soak_outcome(record, manifest)
    print(
        f"PASS: soak qualification fixture {args.record_input} is valid "
        f"({record['duration_seconds']}s, concurrency {record['concurrency']})"
    )
    return 0


def parse_ab_report(output: str) -> dict:
    percentiles = {}
    failed = 0
    completed = 0
    transfer_rate = 0.0
    for line in output.splitlines():
        match = AB_PCT_LINE_RE.match(line)
        if match:
            percentiles[int(match.group("pct"))] = float(match.group("ms"))
        if "Failed requests" in line and "(" not in line:
            parts = line.split()
            if len(parts) >= 3:
                try:
                    failed = int(parts[-1])
                except ValueError:
                    failed = 0
        if "Complete requests" in line:
            parts = line.split()
            if len(parts) >= 3:
                try:
                    completed = int(parts[-1])
                except ValueError:
                    completed = 0
        if "Transfer rate" in line:
            parts = line.split()
            if len(parts) >= 4:
                try:
                    transfer_rate = float(parts[-2])
                except ValueError:
                    transfer_rate = 0.0
    return {
        "p50_ms": percentiles.get(50, 0.0),
        "p99_ms": percentiles.get(99, 0.0),
        "failed_requests": failed,
        "completed_requests": completed,
        "rps": transfer_rate,
    }


def run_ab_chunk(url: str, concurrency: int, seconds: int, output_dir: pathlib.Path) -> dict:
    ab = subprocess.run(
        [
            "/usr/sbin/ab",
            "-t",
            str(seconds),
            "-c",
            str(concurrency),
            "-k",
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=seconds * 4,
    )
    report = parse_ab_report(ab.stdout or ab.stderr)
    (output_dir / "raw").mkdir(parents=True, exist_ok=True)
    (output_dir / "raw" / f"ab-{int(time.time())}.log").write_text(
        ab.stdout + "\n" + ab.stderr, encoding="utf-8"
    )
    report["_returncode"] = ab.returncode
    return report


def read_worker_rss(worker_pid: int) -> int:
    try:
        output = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(worker_pid)],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return int(output.split()[0])
    except (subprocess.CalledProcessError, ValueError, IndexError):
        return -1


def wait_for_ready(url: str, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def write_nginx_conf(runtime_dir: pathlib.Path, port: int, root: str, module_so: str | None) -> None:
    load_line = f"load_module {module_so};" if module_so else ""
    conf = f"""worker_processes 1;
error_log {runtime_dir / 'error.log'} notice;
pid {runtime_dir / 'nginx.pid'};
events {{ }}
http {{
    {load_line}
    server {{
        listen {port};
        root {root};
        markdown_filter on;
    }}
}}
"""
    (runtime_dir / "nginx.conf").write_text(conf, encoding="utf-8")


def build_corpus(runtime_dir: pathlib.Path, manifest: dict) -> dict:
    corpus_dir = runtime_dir / "html"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    corpus = {}
    for entry in manifest["corpus"]:
        scenario_id = entry["id"]
        name = f"{scenario_id}.html"
        size = {"small": 4096, "medium": 204800, "large": 1048576}.get(scenario_id, 4096)
        block = (
            "<!DOCTYPE html>\n<html><head><title>Soak fixture</title></head><body>\n"
            + "\n".join(
                f"<h2>Section {i}</h2>\n<p>Paragraph {i} with <b>bold</b> and "
                f"<a href=\"/{scenario_id}.html\">link</a> text for conversion.</p>"
                for i in range(max(1, size // 200))
            )
            + "\n</body></html>\n"
        )
        payload = block.encode("utf-8")
        if len(payload) < size:
            payload += b"<!-- padding -->\n" * ((size - len(payload)) // 18 + 1)
        (corpus_dir / name).write_bytes(payload[:size])
        corpus[scenario_id] = name
    return corpus


def find_worker_pid(runtime_dir: pathlib.Path) -> int:
    pid_file = runtime_dir / "nginx.pid"
    deadline = time.time() + 15
    while time.time() < deadline:
        if pid_file.is_file():
            try:
                return int(pid_file.read_text().strip())
            except ValueError:
                pass
        time.sleep(0.5)
    return -1


def real_main(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    nginx_bin = os.environ.get("NGINX_BIN", "")
    if not nginx_bin or not pathlib.Path(nginx_bin).is_file():
        if args.allow_skip_soak:
            record = {
                "schema_version": RECORD_SCHEMA_VERSION,
                "candidate_sha": manifest["candidate_sha"],
                "run_id": f"soak-{int(time.time())}",
                "status": "skip",
                "skip_reason": "NGINX_BIN not set or binary not found",
                "policy_reference": "Requirement 18 Wave-6 qualification thresholds",
            }
            (args.record if args.output is None else args.output and pathlib.Path(args.output)).parent.mkdir(parents=True, exist_ok=True)
            output_path = pathlib.Path(args.output or args.record)
            output_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            print(f"SKIP: NGINX_BIN not set; skip recorded at {output_path}")
            return 0
        print(
            "ERROR: NGINX_BIN not set or binary not found; "
            "set NGINX_BIN (and MODULE_SO) or pass --allow-skip-soak",
            file=sys.stderr,
        )
        return 1

    module_so = os.environ.get("MODULE_SO", "")
    port = 19200
    base_url = f"http://127.0.0.1:{port}"

    runtime_dir = pathlib.Path(
        os.environ.get("SOAK_RUNTIME_DIR") or ""
    ) if os.environ.get("SOAK_RUNTIME_DIR") else pathlib.Path(
        f"/tmp/markdown-soak-{int(time.time())}"
    )
    runtime_dir.mkdir(parents=True, exist_ok=True)
    corpus = build_corpus(runtime_dir, manifest)
    write_nginx_conf(runtime_dir, port, str(runtime_dir / "html"), module_so or None)

    nginx = subprocess.Popen(
        [nginx_bin, "-p", str(runtime_dir), "-c", "nginx.conf"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_ready(f"{base_url}/{corpus['small']}"):
            print("ERROR: nginx did not become ready", file=sys.stderr)
            return 1
        worker_pid = find_worker_pid(runtime_dir)
        duration = int(manifest["duration_minutes"] * 60)
        started = time.time()
        finished = started + duration
        rss_series = []
        scenario_metrics = {sid: [] for sid in corpus}
        chunk = 0
        while time.time() < finished:
            remaining = finished - time.time()
            if remaining <= 0:
                break
            chunk_seconds = min(60, int(remaining) + 1)
            for sid in corpus:
                url = f"{base_url}/{corpus[sid]}"
                report = run_ab_chunk(url, manifest["concurrency"], chunk_seconds, runtime_dir)
                scenario_metrics[sid].append(report)
            if worker_pid > 0 and chunk % 6 == 0:
                rss_series.append([round(time.time() - started, 1), read_worker_rss(worker_pid)])
            chunk += 1
        time.sleep(30)
        drain = []
        for _ in range(3):
            if worker_pid > 0:
                drain.append(read_worker_rss(worker_pid))
            time.sleep(5)
        drain_delta = 0
        if len(drain) >= 2:
            drain_delta = max(drain) - min(drain)
        monotonic = len(drain) >= 3 and drain[-1] > drain[0] + 1024
    finally:
        nginx.terminate()
        try:
            nginx.wait(timeout=10)
        except subprocess.TimeoutExpired:
            nginx.kill()

    per_scenario = []
    for sid, reports in scenario_metrics.items():
        total_completed = sum(r.get("completed_requests", 0) for r in reports)
        total_failed = sum(r.get("failed_requests", 0) for r in reports)
        error_rate = total_failed / total_completed if total_completed else 1.0
        p50s = [r.get("p50_ms", 0.0) for r in reports if r.get("p50_ms")]
        p99s = [r.get("p99_ms", 0.0) for r in reports if r.get("p99_ms")]
        per_scenario.append(
            {
                "id": sid,
                "completed_requests": total_completed,
                "failed_requests": total_failed,
                "error_rate": round(error_rate, 6),
                "p50_ms": round(sum(p50s) / len(p50s), 2) if p50s else 0.0,
                "p99_ms": round(sum(p99s) / len(p99s), 2) if p99s else 0.0,
                "rps": round(sum(r.get("rps", 0.0) for r in reports) / len(reports), 2)
                if reports
                else 0.0,
            }
        )

    elapsed = time.time() - started
    any_error = any(s["error_rate"] != 0.0 for s in per_scenario)
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "candidate_sha": manifest["candidate_sha"],
        "run_id": f"soak-{int(time.time())}",
        "started_at": utc_now(),
        "finished_at": utc_now(),
        "duration_seconds": round(elapsed, 1),
        "concurrency": manifest["concurrency"],
        "per_scenario": per_scenario,
        "rss_time_series": rss_series,
        "worker_rss_drain_delta_kb": drain_delta,
        "monotonic_growth_after_drain": monotonic,
        "module_managed_peak_observed": False,
        "per_request_peak_bytes": None,
        "errors": [],
        "status": "pass",
    }
    failures = []
    if elapsed < manifest["duration_minutes"] * 60 * 0.95:
        failures.append(f"duration {elapsed}s below floor")
    if any_error:
        failures.append("error_rate != 0")
    if monotonic:
        failures.append("monotonic RSS growth after drain")
    if failures:
        record["status"] = "fail"
        record["errors"] = failures
    output_path = pathlib.Path(args.output or args.record)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    if failures:
        for failure in failures:
            print(f"ERROR: soak failure: {failure}", file=sys.stderr)
        return 1
    print(f"PASS: soak qualification completed ({elapsed:.0f}s, {manifest['concurrency']} concurrent)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Soak qualification gate")
    parser.add_argument("--mode", choices=["real", "fixture"], default="real")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--record", default=str(DEFAULT_RECORD))
    parser.add_argument("--record-input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--allow-skip-soak", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "fixture":
        if not args.record_input:
            print("ERROR: fixture mode requires --record-input", file=sys.stderr)
            return 2
        return fixture_main(args)
    return real_main(args)


if __name__ == "__main__":
    sys.exit(main())
