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
observation is an ERROR and exits 1. This gate never reads private
planning inputs; every threshold comes from the manifest.

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
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import (  # noqa: E402
    validate_filename_strict,
    validate_read_path,
    validate_write_path_within_root,
)
DEFAULT_MANIFEST = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2" / "short-soak-scenario-manifest.json"
)
DEFAULT_RECORD = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2" / "soak-qualification-record.json"
)
RECORD_OUTPUT_ROOT = pathlib.Path("artifacts/release/0.9.2")
RECORD_OUTPUT_LABEL = "soak qualification record"
SOAK_RUNTIME_ROOT = REPO_ROOT / "build" / "soak-runtime"
SOAK_PORT = 19200
SOAK_SCENARIO_FILES = {
    "small": "small.html",
    "medium": "medium.html",
    "large": "large.html",
}
SOAK_METRICS_PATH = "/markdown-metrics"
METRICS_RESPONSE_MAX_BYTES = 64 * 1024
FLOAT_EPSILON = 1e-9
PEAK_MEMORY_MISSING_ERROR = (
    "insufficient-data: module-managed per-request peak memory was not observed"
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
    "module_managed_peak_observed",
    "per_request_peak_bytes",
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
AB_PCT_LINE_RE = re.compile(r"^\s*(?P<pct>\d+)%\s+(?P<ms>[0-9.]+)\s*(?:ms)?$")
PEAK_MEMORY_METRIC_RE = re.compile(
    r"^nginx_markdown_streaming_peak_memory_bytes\s+(?P<bytes>\d+)$",
    re.ASCII,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: str | pathlib.Path, label: str) -> dict:
    validated_path = validate_read_path(path, purpose=label)
    try:
        raw = json.loads(validated_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"ERROR: {label} unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"ERROR: {label} top level must be an object")
    return raw


def load_manifest(manifest_path: str) -> dict:
    manifest = load_json(manifest_path, "soak manifest")
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
    _validate_corpus_entries(manifest["corpus"])
    return manifest


def _validate_corpus_entries(entries: list[dict]) -> None:
    """Validate the fixed scenario IDs used by corpus files and URLs."""
    seen_ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or "id" not in entry:
            raise SystemExit("ERROR: soak manifest corpus entries need an id")
        scenario_id = entry["id"]
        if (not isinstance(scenario_id, str)
                or scenario_id not in SOAK_SCENARIO_FILES):
            raise SystemExit(
                "ERROR: soak manifest corpus id must be one of "
                f"{sorted(SOAK_SCENARIO_FILES)}"
            )
        if scenario_id in seen_ids:
            raise SystemExit(
                f"ERROR: soak manifest corpus contains duplicate id {scenario_id!r}"
            )
        seen_ids.add(scenario_id)


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


def _check_scenario_rows(record: dict, manifest: dict) -> None:
    """Every manifest corpus scenario must appear with zero errors."""
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
        error_rate = scenario.get("error_rate", 0.0)
        if (not isinstance(error_rate, (int, float))
                or abs(error_rate) > FLOAT_EPSILON):
            raise SystemExit(
                "ERROR: below-threshold: scenario "
                f"{scenario.get('id')!r} error_rate {scenario.get('error_rate')!r}"
            )


def _check_peak_memory(record: dict, manifest: dict) -> None:
    """Per-request peak must stay within the scenario memory ceiling."""
    issue = _peak_memory_issue(record, manifest)
    if issue:
        raise SystemExit(f"ERROR: {issue}")


def _peak_memory_issue(record: dict, manifest: dict) -> str | None:
    """Return a fail-closed peak-memory issue, if the evidence is invalid."""
    if record.get("module_managed_peak_observed") is not True:
        return PEAK_MEMORY_MISSING_ERROR
    peak = record.get("per_request_peak_bytes")
    if not isinstance(peak, int) or isinstance(peak, bool) or peak <= 0:
        return PEAK_MEMORY_MISSING_ERROR
    ceiling = max(
        (s.get("conversion_memory_bytes", 0) for s in manifest["corpus"]),
        default=0,
    )
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling <= 0:
        return "insufficient-data: scenario memory ceiling is missing"
    if peak > ceiling:
        return f"below-threshold: per-request peak {peak} > ceiling {ceiling}"
    return None


def validate_soak_outcome(record: dict, manifest: dict) -> None:
    _check_scenario_rows(record, manifest)
    if record.get("monotonic_growth_after_drain") is not False:
        raise SystemExit(
            "ERROR: below-threshold: monotonic worker-RSS growth after drain"
        )
    _check_peak_memory(record, manifest)
    if record.get("status") != "pass":
        raise SystemExit(
            f"ERROR: blocking-pending: soak record status {record.get('status')!r}"
        )


def fixture_main(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    record = load_json(args.record_input, "soak record")
    validate_record_structure(record)
    validate_against_manifest(record, manifest)
    validate_soak_outcome(record, manifest)
    print(
        f"PASS: soak qualification fixture {args.record_input} is valid "
        f"({record['duration_seconds']}s, concurrency {record['concurrency']})"
    )
    return 0


def _ab_int(line: str, keyword: str) -> int:
    """Parse an integer value from an ab summary line, or 0."""
    if keyword not in line or "(" in line:
        return 0
    parts = line.split()
    if len(parts) < 3:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


def _ab_float(line: str, keyword: str, offset: int = 0) -> float:
    """Parse a float value from an ab summary line, or 0.0."""
    if keyword not in line:
        return 0.0
    parts = line.split()
    index = len(parts) - 2 - offset
    if index < 0:
        return 0.0
    try:
        return float(parts[index])
    except ValueError:
        return 0.0


def _validated_local_url(url: str) -> str:
    """Allow only the local soak server and its fixed scenario files."""
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != SOAK_PORT
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"invalid local soak URL: {url!r}")

    path = urllib.parse.unquote(parsed.path.lstrip("/"))
    validated_path = validate_filename_strict(path, purpose="soak URL path")
    if validated_path not in SOAK_SCENARIO_FILES.values():
        raise ValueError(f"invalid local soak URL path: {path!r}")
    return f"http://127.0.0.1:{SOAK_PORT}/{validated_path}"


def _validated_metrics_url(base_url: str) -> str:
    """Build the fixed local metrics URL used for module observations."""
    parsed = urllib.parse.urlsplit(base_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid soak metrics URL port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port != SOAK_PORT
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("invalid soak metrics URL")
    return f"http://127.0.0.1:{SOAK_PORT}{SOAK_METRICS_PATH}"


def _parse_peak_memory_metric(text: str) -> int | None:
    """Parse one positive module-managed peak gauge from Prometheus text."""
    for line in text.splitlines():
        match = PEAK_MEMORY_METRIC_RE.fullmatch(line.strip())
        if match:
            peak = int(match.group("bytes"))
            return peak if peak > 0 else None
    return None


def read_module_peak_memory(base_url: str) -> int | None:
    """Read the bounded v1 Prometheus peak-memory gauge from local NGINX."""
    url = _validated_metrics_url(base_url)
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status != 200:
                return None
            payload = response.read(METRICS_RESPONSE_MAX_BYTES + 1)
    except (OSError, UnicodeError, urllib.error.URLError):
        return None
    if len(payload) > METRICS_RESPONSE_MAX_BYTES:
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return _parse_peak_memory_metric(text)


def parse_ab_report(output: str) -> dict:
    percentiles = {}
    failed = 0
    completed = 0
    transfer_rate = 0.0
    for line in output.splitlines():
        match = AB_PCT_LINE_RE.match(line)
        if match:
            percentiles[int(match.group("pct"))] = float(match.group("ms"))
            continue
        if "Failed requests" in line:
            failed = _ab_int(line, "Failed requests")
        elif "Complete requests" in line:
            completed = _ab_int(line, "Complete requests")
        elif "Transfer rate" in line:
            transfer_rate = _ab_float(line, "Transfer rate")
    return {
        "p50_ms": percentiles.get(50, 0.0),
        "p99_ms": percentiles.get(99, 0.0),
        "failed_requests": failed,
        "completed_requests": completed,
        "rps": transfer_rate,
    }


def run_ab_chunk(url: str, concurrency: int, seconds: int, output_dir: pathlib.Path) -> dict:
    validated_url = _validated_local_url(url)
    validated_output_dir = validate_write_path_within_root(
        output_dir, REPO_ROOT, purpose="soak raw logs"
    )
    ab_path = shutil.which("ab")
    if not ab_path:
        return {
            "p50_ms": 0.0,
            "p99_ms": 0.0,
            "failed_requests": 0,
            "completed_requests": 0,
            "rps": 0.0,
            "_returncode": -1,
            "_error": "ab executable not found",
        }
    try:
        ab = subprocess.run(
            [
                ab_path,
                "-t",
                str(seconds),
                "-c",
                str(concurrency),
                "-k",
                validated_url,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=max(1, seconds * 4),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "p50_ms": 0.0,
            "p99_ms": 0.0,
            "failed_requests": 0,
            "completed_requests": 0,
            "rps": 0.0,
            "_returncode": -1,
            "_error": f"ab timed out: {exc}",
        }
    report = parse_ab_report(ab.stdout or ab.stderr)
    raw_dir = validated_output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"ab-{int(time.time())}.log").write_text(
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
    validated_url = _validated_local_url(url)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(validated_url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def _nginx_config_path(path: pathlib.Path, label: str) -> str:
    """Reject path bytes that could change the generated NGINX config."""
    text = str(path)
    if any(char in text for char in "\r\n;{}"):
        raise ValueError(f"invalid {label} for NGINX configuration")
    return text


def write_nginx_conf(runtime_dir: pathlib.Path, port: int, root: str, module_so: str | None) -> None:
    validated_runtime_dir = validate_write_path_within_root(
        runtime_dir, REPO_ROOT, purpose="soak runtime directory"
    )
    validated_root = validate_write_path_within_root(
        root, REPO_ROOT, purpose="soak document root"
    )
    runtime_text = _nginx_config_path(validated_runtime_dir, "runtime directory")
    root_text = _nginx_config_path(validated_root, "document root")
    load_line = ""
    if module_so:
        validated_module = validate_read_path(module_so, purpose="MODULE_SO")
        if not validated_module.is_file() or not os.access(validated_module, os.R_OK):
            raise ValueError(f"MODULE_SO is not a readable file: {validated_module}")
        load_line = (
            f"load_module {_nginx_config_path(validated_module, 'MODULE_SO')};"
        )
    (validated_runtime_dir / "logs").mkdir(parents=True, exist_ok=True)
    conf = f"""worker_processes 1;
error_log {runtime_text}/logs/error.log notice;
pid {runtime_text}/nginx.pid;
{load_line}
events {{ }}
http {{
    server {{
        listen {port};
        root {root_text};
        markdown_filter on;
        location = /markdown-metrics {{
            markdown_metrics;
            allow 127.0.0.1;
            allow ::1;
            deny all;
        }}
    }}
}}
"""
    (validated_runtime_dir / "nginx.conf").write_text(conf, encoding="utf-8")


def build_corpus(runtime_dir: pathlib.Path, manifest: dict) -> dict:
    corpus_dir = runtime_dir / "html"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    corpus = {}
    for entry in manifest["corpus"]:
        scenario_id = entry["id"]
        name = SOAK_SCENARIO_FILES[scenario_id]
        size = {"small": 4096, "medium": 204800, "large": 1048576}.get(scenario_id, 4096)
        block = (
            "<!DOCTYPE html>\n<html><head><title>Soak fixture</title></head><body>\n"
            + "\n".join(
                f"<h2>Section {i}</h2>\n<p>Paragraph {i} with <b>bold</b> and "
                f"<a href=\"/{name}\">link</a> text for conversion.</p>"
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


def _read_master_pid(pid_file: pathlib.Path) -> int | None:
    """Read a valid NGINX master PID, or return None while it starts."""
    if not pid_file.is_file():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _find_worker_child(ps_output: str, master_pid: int) -> int:
    """Find the first process whose parent is the NGINX master."""
    for line in ps_output.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[1] == str(master_pid):
            return int(fields[0])
    return -1


def _query_worker_pid(master_pid: int) -> int:
    """Query the process table for a worker of the supplied master PID."""
    try:
        ps = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,comm="],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return -1
    if ps.returncode != 0:
        return -1
    return _find_worker_child(ps.stdout, master_pid)


def find_worker_pid(runtime_dir: pathlib.Path) -> int:
    """Wait briefly for the NGINX master and return one worker PID."""
    pid_file = runtime_dir / "nginx.pid"
    deadline = time.time() + 15
    while time.time() < deadline:
        master_pid = _read_master_pid(pid_file)
        if master_pid is not None:
            worker_pid = _query_worker_pid(master_pid)
            if worker_pid > 0:
                return worker_pid
        time.sleep(0.5)
    return -1


def run_load_loop(
    corpus: dict,
    worker_pid: int,
    duration: int,
    started: float,
    concurrency: int,
    runtime_dir: pathlib.Path,
) -> tuple:
    """Run the sustained load loop, returning (rss_series, scenario_metrics)."""
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
            url = f"http://127.0.0.1:{SOAK_PORT}/{corpus[sid]}"
            report = run_ab_chunk(url, concurrency, chunk_seconds, runtime_dir)
            scenario_metrics[sid].append(report)
        if worker_pid > 0 and chunk % 6 == 0:
            rss_series.append([round(time.time() - started, 1),
                               read_worker_rss(worker_pid)])
        chunk += 1
    return rss_series, scenario_metrics


def measure_drain(worker_pid: int) -> tuple:
    """Sample worker RSS after load; return (drain_delta_kb, monotonic)."""
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
    return drain_delta, monotonic


def _avg(values: list, default: float = 0.0) -> float:
    """Average a list of floats, or the default when empty."""
    if not values:
        return default
    return round(sum(values) / len(values), 2)


def _scenario_row(sid: str, reports: list) -> dict:
    """Aggregate one scenario's ab reports into a record row."""
    total_completed = sum(r.get("completed_requests", 0) for r in reports)
    total_failed = sum(r.get("failed_requests", 0) for r in reports)
    error_rate = total_failed / total_completed if total_completed else 1.0
    p50s = [r.get("p50_ms", 0.0) for r in reports if r.get("p50_ms")]
    p99s = [r.get("p99_ms", 0.0) for r in reports if r.get("p99_ms")]
    rps = [r.get("rps", 0.0) for r in reports if r.get("rps")]
    return {
        "id": sid,
        "completed_requests": total_completed,
        "failed_requests": total_failed,
        "error_rate": round(error_rate, 6),
        "p50_ms": _avg(p50s),
        "p99_ms": _avg(p99s),
        "rps": _avg(rps),
    }


def build_scenario_metrics(scenario_metrics: dict) -> list:
    """Aggregate ab reports per scenario into the qualification record rows."""
    return [_scenario_row(sid, reports)
            for sid, reports in scenario_metrics.items()]


def _write_record(record: dict, args: argparse.Namespace) -> pathlib.Path:
    """Persist a record below the fixed generated release-output directory."""
    raw_output = pathlib.Path(args.output or args.record)
    raw_name = raw_output.name
    output_parts = raw_output.parts
    if raw_name in {"", ".", ".."}:
        raise ValueError(f"Invalid output filename for {RECORD_OUTPUT_LABEL}")
    if not (len(output_parts) == 1
            or (len(output_parts) == 4
                and output_parts[:3] == RECORD_OUTPUT_ROOT.parts)):
        raise ValueError(
            "Output path must be '<filename>' or "
            "'artifacts/release/0.9.2/<filename>'"
        )
    safe_name = validate_filename_strict(
        raw_name, purpose=RECORD_OUTPUT_LABEL
    )
    candidate_output = REPO_ROOT / RECORD_OUTPUT_ROOT / safe_name
    resolved_candidate = candidate_output.resolve(strict=False)
    resolved_root = REPO_ROOT.resolve(strict=False)
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(
            f"Refusing to write outside repository root: {resolved_candidate}"
        )
    validated_path = validate_write_path_within_root(
        resolved_candidate, REPO_ROOT, purpose=RECORD_OUTPUT_LABEL
    )
    validated_path.parent.mkdir(parents=True, exist_ok=True)
    # NOSONAR suppression for pythonsecurity:S2083: the fixed output root and
    # strict filename validation prevent CLI-selected targets.
    validated_path.write_text(  # NOSONAR
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
    # SONAR_NOTE(S2083): Filename is allowlisted and the path is built from
    # the trusted generated-output root, so CLI input cannot select a target.
    return validated_path


def _validated_nginx_binary() -> pathlib.Path | None:
    """Resolve NGINX_BIN and reject traversal or non-executable files."""
    raw_path = os.environ.get("NGINX_BIN", "")
    if not raw_path:
        return None
    try:
        resolved = validate_read_path(raw_path, purpose="NGINX_BIN")
    except FileNotFoundError:
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return resolved


def _validated_module() -> pathlib.Path | None:
    """Resolve MODULE_SO and reject missing or unreadable module files."""
    raw_path = os.environ.get("MODULE_SO", "")
    if not raw_path:
        return None
    try:
        resolved = validate_read_path(raw_path, purpose="MODULE_SO")
    except FileNotFoundError:
        return None
    if not resolved.is_file() or not os.access(resolved, os.R_OK):
        return None
    return resolved


def _runtime_directory() -> pathlib.Path:
    """Return a private runtime directory under the repository build tree."""
    configured = os.environ.get("SOAK_RUNTIME_DIR")
    if configured:
        runtime_dir = validate_write_path_within_root(
            configured, REPO_ROOT, purpose="SOAK_RUNTIME_DIR"
        )
        runtime_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.chmod(0o700)
        return runtime_dir

    runtime_root = validate_write_path_within_root(
        SOAK_RUNTIME_ROOT, REPO_ROOT, purpose="soak temporary root"
    )
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_root.chmod(0o700)
    return pathlib.Path(
        tempfile.mkdtemp(prefix="markdown-soak-", dir=runtime_root)
    )


def handle_missing_nginx(args: argparse.Namespace, manifest: dict) -> int | None:
    """Require both NGINX_BIN and MODULE_SO, or record an explicit skip."""
    missing = []
    if _validated_nginx_binary() is None:
        missing.append("NGINX_BIN")
    if _validated_module() is None:
        missing.append("MODULE_SO")
    if not missing:
        return None
    missing_text = " and ".join(missing)
    if args.allow_skip_soak:
        record = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "candidate_sha": manifest["candidate_sha"],
            "run_id": f"soak-{int(time.time())}",
            "status": "skip",
            "skip_reason": f"{missing_text} not set or unavailable",
            "policy_reference": "release short-soak qualification thresholds",
        }
        output_path = _write_record(record, args)
        print(f"SKIP: {missing_text} unavailable; skip recorded at {output_path}")
        return 0
    print(
        f"ERROR: {missing_text} not set or unavailable; "
        "set NGINX_BIN (and MODULE_SO) or pass --allow-skip-soak",
        file=sys.stderr,
    )
    return 1


def prepare_runtime(base_url: str, manifest: dict, module_so: str) -> tuple:
    """Create the runtime dir, corpus, nginx config, and start nginx."""
    runtime_dir = _runtime_directory()
    corpus = build_corpus(runtime_dir, manifest)
    port = int(base_url.rsplit(":", 1)[1])
    write_nginx_conf(runtime_dir, port, str(runtime_dir / "html"), module_so or None)
    nginx_bin = _validated_nginx_binary()
    if nginx_bin is None:
        raise ValueError("NGINX_BIN is not a readable executable")
    nginx = subprocess.Popen(
        [str(nginx_bin), "-p", str(runtime_dir), "-c", "nginx.conf"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return runtime_dir, corpus, nginx


def real_main(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    skip_result = handle_missing_nginx(args, manifest)
    if skip_result is not None:
        return skip_result

    module_so = os.environ.get("MODULE_SO", "")
    port = SOAK_PORT
    base_url = f"http://127.0.0.1:{port}"
    runtime_dir, corpus, nginx = prepare_runtime(base_url, manifest, module_so)
    try:
        ready_fixture = next(iter(corpus.values()), None)
        if not ready_fixture or not wait_for_ready(
                f"{base_url}/{ready_fixture}"):
            print("ERROR: nginx did not become ready", file=sys.stderr)
            return 1
        worker_pid = find_worker_pid(runtime_dir)
        started = time.time()
        duration = int(manifest["duration_minutes"] * 60)
        rss_series, scenario_metrics = run_load_loop(
            corpus, worker_pid, duration, started,
            manifest["concurrency"], runtime_dir)
        drain_delta, monotonic = measure_drain(worker_pid)
        peak_memory_bytes = read_module_peak_memory(base_url)
    finally:
        nginx.terminate()
        try:
            nginx.wait(timeout=10)
        except subprocess.TimeoutExpired:
            nginx.kill()

    per_scenario = build_scenario_metrics(scenario_metrics)

    elapsed = time.time() - started
    any_error = any(
        s.get("error_rate", 0.0) > 0.0 for s in per_scenario)
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
        "module_managed_peak_observed": peak_memory_bytes is not None,
        "per_request_peak_bytes": peak_memory_bytes,
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
    peak_issue = _peak_memory_issue(record, manifest)
    if peak_issue:
        failures.append(peak_issue)
    if failures:
        record["status"] = "fail"
        record["errors"] = failures
    _write_record(record, args)
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
    parser.add_argument(
        "--record", default=str(DEFAULT_RECORD.relative_to(REPO_ROOT))
    )
    parser.add_argument("--record-input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--allow-skip-soak", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "fixture":
            if not args.record_input:
                print("ERROR: fixture mode requires --record-input", file=sys.stderr)
                return 2
            return fixture_main(args)
        return real_main(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
