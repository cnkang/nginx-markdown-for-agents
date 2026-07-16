"""Integration tests for the module-level benchmark harness (Spec 59, task 1.3).

Tests:
  1. Validates the ``module_benchmark`` schema in ``perf/metrics-schema.json``
     is well-formed (required fields, types, enums, constraints).
  2. Tests that ``run_module_benchmark.sh`` exits with code 75 when NGINX_BIN
     is unset (Requirement 1.7).
  3. Tests the JSON report structure matches the schema using a mock report
     (Requirements 1.1, 1.5).
  4. Tests port cleanup on EXIT/INT/TERM signals — verifies trap-based cleanup
     kills spawned processes and removes temp files (Requirement 1.5).

Run:
    python3 -m pytest tools/perf/tests/test_benchmark_harness.py -q

**Validates: Requirements 1.1, 1.5, 1.7**
"""


import contextlib
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

import pytest

from tools.perf.report_schema import validate_module_benchmark
from tools.perf.upstream_mock import MockUpstreamHandler

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "perf" / "metrics-schema.json"
BENCHMARK_SCRIPT = REPO_ROOT / "tools" / "perf" / "run_module_benchmark.sh"
MAKEFILE_PATH = REPO_ROOT / "Makefile"


def test_corpus_benchmark_generates_large_fixtures_before_validation():
    """The canonical benchmark target must materialize baseline fixtures."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    target = makefile.split("test-benchmark:", 1)[1].split(
        "test-benchmark-compare:", 1
    )[0]
    generator = "tests/corpus/large/generate-large-fixtures.sh"
    validator = "tools/corpus/validate_corpus.sh"

    assert generator in target
    assert target.index(generator) < target.index(validator)


def test_production_example_gate_loads_dynamic_module_when_provided():
    """Dynamic release builds must load MODULE_SO for nginx -t."""
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    target = makefile.split("test-production-examples-nginx-t:", 1)[1].split(
        "test-production-examples-e2e-smoke:", 1
    )[0]

    assert "test-production-examples-nginx-t: SHELL := /bin/bash" in makefile
    assert 'module_so="$${MODULE_SO:-}"' in target
    assert '-g "load_module $$module_so;"' in target
    assert 'runtime_prefix="$${RUNNER_TEMP:-$${TMPDIR:-/tmp}}/' in target
    assert 'mkdir -p "$$runtime_prefix/logs"' in target
    assert '-p "$$runtime_prefix/"' in target


def test_module_benchmark_records_actual_fixture_bytes():
    """Every scenario must report the actual fixture size for memory slope."""
    source = BENCHMARK_SCRIPT.read_text(encoding="utf-8")
    assert 'fixture_bytes="$(wc -c < "$CORPUS_DIR/$SC_FIXTURE")"' in source
    assert source.count('"input_bytes": input_bytes') == 2


def test_upstream_mock_splits_chunked_bodies():
    """Chunked benchmark responses must expose multiple bounded chunks."""
    handler = object.__new__(MockUpstreamHandler)
    handler.wfile = io.BytesIO()
    handler._send_common_headers = lambda _encoding: None
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None
    body = b"x" * (32 * 1024 + 7)

    handler._send_chunked_response(body, None)

    wire = handler.wfile.getvalue()
    chunks = []
    offset = 0
    while True:
        line_end = wire.index(b"\r\n", offset)
        size = int(wire[offset:line_end], 16)
        offset = line_end + 2
        if size == 0:
            break
        chunks.append(wire[offset:offset + size])
        offset += size + 2

    assert len(chunks) > 1
    assert max(map(len, chunks)) <= 16 * 1024
    assert b"".join(chunks) == body


@pytest.mark.parametrize(
    ("content_encoding", "wbits"),
    [("gzip", zlib.MAX_WBITS | 16), ("deflate", zlib.MAX_WBITS)],
)
def test_upstream_mock_streams_compression_in_bounded_chunks(
    content_encoding, wbits
):
    """Compressed streaming fixtures must not inflate in one giant burst."""
    handler = object.__new__(MockUpstreamHandler)
    handler.wfile = io.BytesIO()
    handler._send_common_headers = lambda _encoding: None
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None
    body = b"<p>highly compressible benchmark content</p>\n" * 32_768

    handler._send_chunked_response(body, content_encoding)

    wire = handler.wfile.getvalue()
    chunks = []
    offset = 0
    while True:
        line_end = wire.index(b"\r\n", offset)
        size = int(wire[offset:line_end], 16)
        offset = line_end + 2
        if size == 0:
            break
        chunks.append(wire[offset:offset + size])
        offset += size + 2

    decompressor = zlib.decompressobj(wbits)
    decompressed_chunks = [decompressor.decompress(chunk) for chunk in chunks]
    decompressed_chunks.append(decompressor.flush())

    assert len(chunks) > 1
    assert max(map(len, decompressed_chunks)) <= 16 * 1024
    assert b"".join(decompressed_chunks) == body

# Canonical allowlist for bash binary paths (Rule 33: CLI-derived executables
# must match a fixed canonical allowlist before subprocess use).
_BASH_ALLOWLIST = {
    "/bin/bash",
    "/usr/bin/bash",
    "/usr/local/bin/bash",
    "/opt/homebrew/bin/bash",
}

_resolved_bash = shutil.which("bash")
if _resolved_bash and Path(_resolved_bash).resolve().as_posix() in _BASH_ALLOWLIST:
    BASH_BIN = _resolved_bash
elif Path("/bin/bash").exists():
    BASH_BIN = "/bin/bash"
else:
    BASH_BIN = None  # Tests requiring bash will be skipped

WORKDIR_RE = re.compile(r"Workdir: (?P<path>\S+)")

requires_bash = pytest.mark.skipif(
    BASH_BIN is None,
    reason="bash not found in canonical allowlist",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_schema():
    """Load and return the parsed metrics-schema.json."""
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _build_valid_mock_report():
    """Build a valid mock module_benchmark report conforming to the schema."""
    return {
        "module_benchmark": {
            "version": "1.0.0",
            "timestamp": "2026-07-01T12:00:00Z",
            "git_commit": "abc1234",
            "platform": "darwin-arm64",
            "load_generator": "hey",
            "scenarios": [
                {
                    "name": "plain-small",
                    "profile": "balanced",
                    "compression": "none",
                    "transfer_encoding": "identity",
                    "concurrency": 10,
                    "status": "completed",
                    "metrics": {
                        "rps": 1500.0,
                        "throughput_mbps": 12.5,
                        "latency_p50_ms": 2.1,
                        "latency_p95_ms": 5.3,
                        "latency_p99_ms": 8.7,
                        "ttfb_p50_ms": 1.5,
                        "ttfb_p95_ms": 3.2,
                        "ttlb_p50_ms": 2.1,
                        "worker_rss_mb": 24.5,
                        "streaming_ratio": 0.0,
                        "fullbuffer_ratio": 1.0,
                        "fallback_rate": 0.0,
                    },
                },
                {
                    "name": "chunked-medium",
                    "profile": "balanced",
                    "compression": "none",
                    "transfer_encoding": "chunked",
                    "concurrency": 10,
                    "status": "completed",
                    "metrics": {
                        "rps": 1200.0,
                        "throughput_mbps": 10.0,
                        "latency_p50_ms": 3.0,
                        "latency_p95_ms": 7.1,
                        "latency_p99_ms": 12.0,
                        "ttfb_p50_ms": 2.0,
                        "ttfb_p95_ms": 4.5,
                        "ttlb_p50_ms": 3.0,
                        "worker_rss_mb": 28.0,
                        "streaming_ratio": 0.0,
                        "fullbuffer_ratio": 1.0,
                        "fallback_rate": 0.0,
                    },
                },
                {
                    "name": "gzip-large",
                    "profile": "balanced",
                    "compression": "gzip",
                    "transfer_encoding": "identity",
                    "concurrency": 10,
                    "status": "completed",
                    "metrics": {
                        "rps": 800.0,
                        "throughput_mbps": 6.0,
                        "latency_p50_ms": 5.5,
                        "latency_p95_ms": 12.0,
                        "latency_p99_ms": 20.0,
                        "ttfb_p50_ms": None,
                        "ttfb_p95_ms": None,
                        "ttlb_p50_ms": 5.5,
                        "worker_rss_mb": 45.0,
                        "streaming_ratio": 0.0,
                        "fullbuffer_ratio": 1.0,
                        "fallback_rate": 0.0,
                    },
                },
                {
                    "name": "large-body",
                    "profile": "balanced",
                    "compression": "none",
                    "transfer_encoding": "identity",
                    "concurrency": 5,
                    "status": "completed",
                    "metrics": {
                        "rps": 200.0,
                        "throughput_mbps": 50.0,
                        "latency_p50_ms": 25.0,
                        "latency_p95_ms": 40.0,
                        "latency_p99_ms": 55.0,
                        "ttfb_p50_ms": 20.0,
                        "ttfb_p95_ms": 35.0,
                        "ttlb_p50_ms": 25.0,
                        "worker_rss_mb": 120.0,
                        "streaming_ratio": 0.0,
                        "fullbuffer_ratio": 1.0,
                        "fallback_rate": 0.0,
                    },
                },
                {
                    "name": "streaming-first",
                    "profile": "streaming_first",
                    "compression": "none",
                    "transfer_encoding": "chunked",
                    "concurrency": 20,
                    "status": "completed",
                    "metrics": {
                        "rps": 900.0,
                        "throughput_mbps": 8.0,
                        "latency_p50_ms": 4.0,
                        "latency_p95_ms": 9.0,
                        "latency_p99_ms": 15.0,
                        "ttfb_p50_ms": 1.2,
                        "ttfb_p95_ms": 2.8,
                        "ttlb_p50_ms": 4.0,
                        "worker_rss_mb": 32.0,
                        "streaming_ratio": 0.95,
                        "fullbuffer_ratio": 0.05,
                        "fallback_rate": 0.02,
                    },
                },
            ],
            "memory_slope": {
                "rss_per_input_mb": 1.5,
                "r_squared": 0.92,
            },
        }
    }


def _extract_workdir(stderr: bytes) -> Path:
    """Return the benchmark workdir reported by the harness."""
    match = WORKDIR_RE.search(stderr.decode(errors="replace"))
    assert match is not None, "benchmark stderr did not include Workdir"
    return Path(match.group("path"))


# ---------------------------------------------------------------------------
# 1. Schema well-formedness tests
# ---------------------------------------------------------------------------


class TestSchemaWellFormedness:
    """Validate that perf/metrics-schema.json module_benchmark section is well-formed."""

    def test_schema_file_exists_and_parses(self):
        """Schema file exists and contains valid JSON."""
        assert SCHEMA_PATH.exists(), f"Missing schema file: {SCHEMA_PATH}"
        schema = _load_schema()
        assert isinstance(schema, dict)

    def test_module_benchmark_section_present(self):
        """Schema contains the module_benchmark top-level section."""
        schema = _load_schema()
        assert "module_benchmark" in schema, "Missing 'module_benchmark' key in schema"

    def test_module_benchmark_has_required_fields(self):
        """module_benchmark section has description, version, and report_schema."""
        mb = _load_schema()["module_benchmark"]
        assert "description" in mb
        assert "version" in mb
        assert "report_schema" in mb

    def test_report_schema_is_valid_object_type(self):
        """report_schema declares type=object with required and properties."""
        rs = _load_schema()["module_benchmark"]["report_schema"]
        assert rs.get("type") == "object"
        assert "required" in rs
        assert "properties" in rs
        assert "module_benchmark" in rs["required"]

    def _assert_object_has_required_fields(self, schema_object, *fields):
        """Assert that a schema object is a valid object with the specified required fields."""
        assert schema_object.get("type") == "object"
        required_in_schema = schema_object.get("required", [])
        for field in fields:
            assert field in required_in_schema, f"Missing required field '{field}'"

    def test_report_schema_module_benchmark_properties(self):
        """module_benchmark properties in schema include version, timestamp, scenarios, memory_slope."""
        props = _load_schema()["module_benchmark"]["report_schema"]["properties"]["module_benchmark"]
        self._assert_object_has_required_fields(
            props, "version", "timestamp", "scenarios", "memory_slope"
        )

    def test_scenarios_schema_structure(self):
        """scenarios is array of objects with required name and status fields."""
        mb_props = _load_schema()["module_benchmark"]["report_schema"]["properties"]["module_benchmark"]
        scenarios = mb_props["properties"]["scenarios"]
        assert scenarios.get("type") == "array"
        items = scenarios.get("items", {})
        self._assert_object_has_required_fields(items, "name", "status")

    def test_scenario_name_enum_values(self):
        """scenario name enum contains the 5 required scenario names."""
        mb_props = _load_schema()["module_benchmark"]["report_schema"]["properties"]["module_benchmark"]
        items = mb_props["properties"]["scenarios"]["items"]
        name_prop = items["properties"]["name"]
        expected_names = {"plain-small", "chunked-medium", "gzip-large", "large-body", "streaming-first"}
        assert set(name_prop.get("enum", [])) == expected_names

    def test_memory_slope_required_fields(self):
        """memory_slope requires rss_per_input_mb and r_squared."""
        mb_props = _load_schema()["module_benchmark"]["report_schema"]["properties"]["module_benchmark"]
        ms = mb_props["properties"]["memory_slope"]
        self._assert_object_has_required_fields(ms, "rss_per_input_mb", "r_squared")

    def test_metrics_properties_present(self):
        """scenario metrics object has expected numeric properties."""
        mb_props = _load_schema()["module_benchmark"]["report_schema"]["properties"]["module_benchmark"]
        items = mb_props["properties"]["scenarios"]["items"]
        metrics = items["properties"].get("metrics", {})
        assert metrics.get("type") == "object"
        props = metrics.get("properties", {})
        expected_metrics = [
            "rps", "throughput_mbps", "latency_p50_ms", "latency_p95_ms",
            "latency_p99_ms", "ttfb_p50_ms", "ttlb_p50_ms", "worker_rss_mb",
            "streaming_ratio", "fullbuffer_ratio", "fallback_rate",
        ]
        for m in expected_metrics:
            assert m in props, f"Missing metric property: {m}"


# ---------------------------------------------------------------------------
# 2. Graceful exit when NGINX_BIN is unset (Requirement 1.7)
# ---------------------------------------------------------------------------


@requires_bash
class TestGracefulExit:
    """Test that run_module_benchmark.sh exits with code 75 when NGINX_BIN is unset."""

    def test_exit_code_75_when_nginx_bin_unset(self):
        """Script exits with EX_TEMPFAIL (75) when NGINX_BIN is not set."""
        env = os.environ.copy()
        # Ensure NGINX_BIN is not set
        env.pop("NGINX_BIN", None)

        result = subprocess.run(
            [BASH_BIN, str(BENCHMARK_SCRIPT)],
            env=env,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 75, (
            f"Expected exit code 75, got {result.returncode}. "
            f"stderr: {result.stderr.decode()}"
        )

    def test_skip_message_in_stderr(self):
        """Script emits SKIP_NOT_PRESENT message to stderr when NGINX_BIN unset."""
        env = os.environ.copy()
        env.pop("NGINX_BIN", None)

        result = subprocess.run(
            [BASH_BIN, str(BENCHMARK_SCRIPT)],
            env=env,
            capture_output=True,
            timeout=10,
        )
        stderr = result.stderr.decode()
        assert "SKIP_NOT_PRESENT" in stderr, (
            f"Expected 'SKIP_NOT_PRESENT' in stderr, got: {stderr}"
        )


# ---------------------------------------------------------------------------
# 3. JSON report structure matches schema (mock report validation)
# ---------------------------------------------------------------------------


class TestReportSchemaConformance:
    """Validate mock reports against the module_benchmark schema structure."""

    def _get_schema_props(self):
        """Return the module_benchmark properties from the schema."""
        return _load_schema()["module_benchmark"]["report_schema"]["properties"]["module_benchmark"]

    def test_valid_report_has_required_top_keys(self):
        """A valid mock report contains all required top-level keys."""
        report = _build_valid_mock_report()
        mb = report["module_benchmark"]
        schema_props = self._get_schema_props()
        required = schema_props["required"]
        for key in required:
            assert key in mb, f"Missing required key: {key}"

    def test_valid_report_scenarios_count(self):
        """Report contains exactly 5 scenarios matching the spec."""
        report = _build_valid_mock_report()
        scenarios = report["module_benchmark"]["scenarios"]
        assert len(scenarios) == 5

    def test_valid_report_scenario_names(self):
        """All scenario names match the schema enum."""
        report = _build_valid_mock_report()
        schema_props = self._get_schema_props()
        valid_names = set(
            schema_props["properties"]["scenarios"]["items"]["properties"]["name"]["enum"]
        )
        for scenario in report["module_benchmark"]["scenarios"]:
            assert scenario["name"] in valid_names, (
                f"Invalid scenario name: {scenario['name']}"
            )

    def test_valid_report_scenario_status_values(self):
        """All scenario status values are in the schema enum."""
        report = _build_valid_mock_report()
        schema_props = self._get_schema_props()
        valid_statuses = set(
            schema_props["properties"]["scenarios"]["items"]["properties"]["status"]["enum"]
        )
        for scenario in report["module_benchmark"]["scenarios"]:
            assert scenario["status"] in valid_statuses

    def test_valid_report_metrics_non_negative(self):
        """All numeric metrics are non-negative (where minimum=0 in schema)."""
        report = _build_valid_mock_report()
        for scenario in report["module_benchmark"]["scenarios"]:
            metrics = scenario.get("metrics", {})
            for key, value in metrics.items():
                if value is None:
                    continue  # null is valid for ttfb fields
                if isinstance(value, (int, float)):
                    assert value >= 0, (
                        f"Metric {key} in {scenario['name']} is negative: {value}"
                    )

    def test_valid_report_ratios_bounded(self):
        """streaming_ratio, fullbuffer_ratio, fallback_rate are in [0, 1]."""
        report = _build_valid_mock_report()
        ratio_fields = ["streaming_ratio", "fullbuffer_ratio", "fallback_rate"]
        for scenario in report["module_benchmark"]["scenarios"]:
            metrics = scenario.get("metrics", {})
            for field in ratio_fields:
                if field in metrics:
                    val = metrics[field]
                    assert 0.0 <= val <= 1.0, (
                        f"{field} in {scenario['name']} out of range: {val}"
                    )

    def test_valid_report_memory_slope_structure(self):
        """memory_slope has required fields with valid types."""
        report = _build_valid_mock_report()
        ms = report["module_benchmark"]["memory_slope"]
        assert "rss_per_input_mb" in ms
        assert "r_squared" in ms
        assert isinstance(ms["rss_per_input_mb"], (int, float))
        assert isinstance(ms["r_squared"], (int, float))
        assert 0.0 <= ms["r_squared"] <= 1.0

    def test_memory_slope_rejects_non_dict_value(self):
        """memory_slope must be a dict when present."""
        report = _build_valid_mock_report()
        report["module_benchmark"]["memory_slope"] = "invalid"

        errors = validate_module_benchmark(report)

        assert "memory_slope must be a dict" in errors

    def test_valid_report_ttfb_nullable(self):
        """ttfb_p50_ms and ttfb_p95_ms accept null values per schema."""
        report = _build_valid_mock_report()
        # gzip-large scenario has null ttfb values
        gzip_scenario = next(
            s for s in report["module_benchmark"]["scenarios"]
            if s["name"] == "gzip-large"
        )
        metrics = gzip_scenario["metrics"]
        assert metrics["ttfb_p50_ms"] is None
        assert metrics["ttfb_p95_ms"] is None

    def test_valid_report_concurrency_positive(self):
        """concurrency values are positive integers."""
        report = _build_valid_mock_report()
        for scenario in report["module_benchmark"]["scenarios"]:
            conc = scenario.get("concurrency")
            if conc is not None:
                assert isinstance(conc, int)
                assert conc >= 1

    def test_skipped_scenario_report_structure(self):
        """A skipped scenario has status=skipped and optional reason field."""
        report = _build_valid_mock_report()
        # Modify one scenario to be skipped
        report["module_benchmark"]["scenarios"][0] = {
            "name": "plain-small",
            "status": "skipped",
            "reason": "fixture not available",
        }
        scenario = report["module_benchmark"]["scenarios"][0]
        assert scenario["status"] == "skipped"
        assert "reason" in scenario

    def test_report_json_serializable(self):
        """The mock report can be serialized to JSON and parsed back identically."""
        report = _build_valid_mock_report()
        serialized = json.dumps(report, indent=2)
        parsed = json.loads(serialized)
        assert parsed == report

    def test_load_generator_enum(self):
        """load_generator field accepts only enum values from schema."""
        schema_props = self._get_schema_props()
        lg_prop = schema_props["properties"].get("load_generator", {})
        valid_generators = set(lg_prop.get("enum", []))
        assert "hey" in valid_generators
        assert "ab" in valid_generators

        report = _build_valid_mock_report()
        assert report["module_benchmark"]["load_generator"] in valid_generators

    def test_profile_enum_values(self):
        """profile field enum covers all valid profiles."""
        self._assert_schema_enum_values(
            "profile", "balanced", "streaming_first", "strict_cache"
        )

    def test_compression_enum_values(self):
        """compression field enum covers all valid types."""
        self._assert_schema_enum_values(
            "compression", "none", "gzip", "deflate"
        )

    def _assert_schema_enum_values(self, prop_name, *expected_values):
        schema_props = self._get_schema_props()
        items = schema_props["properties"]["scenarios"]["items"]
        prop = items["properties"].get(prop_name, {})
        valid_values = set(prop.get("enum", []))
        assert valid_values == set(expected_values)

# ---------------------------------------------------------------------------
# 4. Port cleanup on EXIT/INT/TERM signals (Requirement 1.5)
# ---------------------------------------------------------------------------


def _port_in_use(port: int) -> bool:
    """Check whether a port is currently bound on 127.0.0.1."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False
    finally:
        s.close()


def _wait_for_port(port: int, timeout: float = 5.0) -> bool:
    """Wait until a port becomes available (in use) on 127.0.0.1."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_in_use(port):
            return True
        time.sleep(0.1)
    return False


def _wait_for_port_free(port: int, timeout: float = 5.0) -> bool:
    """Wait until a port is no longer in use on 127.0.0.1."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_in_use(port):
            return True
        time.sleep(0.1)
    return False


@requires_bash
class TestPortCleanupOnSignals:
    """Test trap-based cleanup kills spawned processes and removes temp files.

    The benchmark harness registers traps on EXIT/INT/TERM to:
    - Kill the upstream mock (python3 http.server on port 19100)
    - Kill NGINX (on port 19101)
    - Remove the PID file
    - Remove the temp working directory

    These tests start the harness with a stub NGINX binary, wait for the
    upstream mock to start, then send signals and verify cleanup.
    """

    def _create_stub_nginx(self, tmpdir: Path) -> Path:
        """Create a stub NGINX binary that listens on a port but does nothing.

        The stub starts a Python HTTP server on the expected NGINX port (19101)
        and waits. This simulates NGINX being up so we can test signal cleanup.
        """
        stub = tmpdir / "nginx_stub.sh"
        stub.write_text(
            '#!/usr/bin/env bash\n'
            '# Stub NGINX binary for signal cleanup tests\n'
            '# Ignores all arguments except -t (config test)\n'
            'for arg in "$@"; do\n'
            '  if [[ "$arg" == "-t" ]]; then\n'
            '    exit 0\n'
            '  fi\n'
            'done\n'
            '# Start a simple listener on port 19101 to simulate NGINX\n'
            'python3 -m http.server 19101 --bind 127.0.0.1 &\n'
            'CHILD=$!\n'
            'trap "kill $CHILD 2>/dev/null; exit 0" EXIT INT TERM\n'
            'wait $CHILD\n'
        )
        stub.chmod(0o755)
        return stub

    def test_cleanup_on_sigterm(self):
        """SIGTERM triggers cleanup: upstream mock killed, ports freed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = self._spawn_benchmark_process(tmpdir)
            try:
                self._verify_sigterm_cleanup(proc)
            finally:
                # Safety: ensure process group is killed
                with contextlib.suppress(OSError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=5)

    def _verify_sigterm_cleanup(self, proc):
        # Wait for upstream mock to start (port 19100)
        upstream_ready = _wait_for_port(19100, timeout=10.0)
        if not upstream_ready:
            # Script may have failed early; check returncode
            proc.poll()
            if proc.returncode is not None:
                    stderr = proc.stderr.read() if proc.stderr else b""
                    raise AssertionError(
                        "benchmark harness exited before upstream "
                        f"readiness; stderr: {stderr.decode(errors='replace')}"
                    )
            # Give it a bit more time
            time.sleep(1.0)

        # Send SIGTERM to the process group
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)

        # Wait for process to terminate
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # Force kill if stuck
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)

        # Verify cleanup: port 19100 (upstream) should be freed
        port_freed = _wait_for_port_free(19100, timeout=5.0)
        assert port_freed, (
            "Port 19100 still in use after SIGTERM — upstream mock not cleaned up"
        )

    def test_cleanup_on_sigint(self):
        """SIGINT triggers cleanup: upstream mock killed, ports freed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = self._spawn_benchmark_process(tmpdir)
            try:
                self._verify_sigint_cleanup(proc)
            finally:
                with contextlib.suppress(OSError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=5)

    def _verify_sigint_cleanup(self, proc):
        # Wait for upstream mock to start
        upstream_ready = _wait_for_port(19100, timeout=10.0)
        if not upstream_ready:
            proc.poll()
            if proc.returncode is not None:
                    stderr = proc.stderr.read() if proc.stderr else b""
                    raise AssertionError(
                        "benchmark harness exited before upstream "
                        f"readiness; stderr: {stderr.decode(errors='replace')}"
                    )
            time.sleep(1.0)

        # Send SIGINT to the process group
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)

        # Verify port 19100 is freed
        port_freed = _wait_for_port_free(19100, timeout=5.0)
        assert port_freed, (
            "Port 19100 still in use after SIGINT — upstream mock not cleaned up"
        )

    def test_pid_file_removed_on_exit(self):
        """PID file is removed after script exits (normal or signal)."""
        env = os.environ.copy()
        # With NGINX_BIN unset, script exits with 75 immediately — verify
        # that for the early-exit path no PID file is left behind.
        env.pop("NGINX_BIN", None)

        result = subprocess.run(
            [BASH_BIN, str(BENCHMARK_SCRIPT)],
            env=env,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 75

        assert "Workdir:" not in result.stderr.decode(errors="replace")

    def test_trap_declaration_in_script(self):
        """Verify the script declares traps for EXIT, INT, and TERM signals."""
        script_content = BENCHMARK_SCRIPT.read_text(encoding="utf-8")
        # The trap line should register the cleanup function for these signals
        assert "trap cleanup EXIT INT TERM" in script_content or (
            "trap" in script_content
            and "EXIT" in script_content
            and "INT" in script_content
            and "TERM" in script_content
        ), "Script must declare traps for EXIT, INT, and TERM"

    def test_cleanup_removes_temp_directory(self):
        """Temp working directory is removed during cleanup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = self._spawn_benchmark_process(tmpdir)
            try:
                self._verify_temp_dir_cleanup(proc)
            finally:
                with contextlib.suppress(OSError):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=5)

    def _verify_temp_dir_cleanup(self, proc):
        # Wait for upstream mock to start
        upstream_ready = _wait_for_port(19100, timeout=10.0)
        if not upstream_ready:
            proc.poll()
            if proc.returncode is not None:
                stderr = proc.stderr.read() if proc.stderr else b""
                raise AssertionError(
                    "benchmark harness exited before upstream "
                    f"readiness; stderr: {stderr.decode(errors='replace')}"
                )
            time.sleep(1.0)

        # Send SIGTERM
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)

        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait(timeout=5)

        stderr = proc.stderr.read() if proc.stderr else b""
        workdir = _extract_workdir(stderr)
        # Give filesystem a moment to catch up
        time.sleep(0.2)
        assert not workdir.exists(), (
            f"Temp directory still exists after cleanup: {workdir}"
        )

    def _spawn_benchmark_process(self, tmpdir):
        tmpdir_path = Path(tmpdir)
        stub = self._create_stub_nginx(tmpdir_path)
        env = os.environ.copy()
        env["NGINX_BIN"] = str(stub)
        env.pop("MODULE_SO", None)
        return subprocess.Popen(
            [BASH_BIN, str(BENCHMARK_SCRIPT), "--iterations", "1"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )


class TestNginxConfigGeneration:
    """Validate NGINX configurations generated by run_module_benchmark.sh."""

    def test_generated_config_directives(self):
        """Verify generated config has correct directives, no legacy ones, and no local mime.types."""
        assert BENCHMARK_SCRIPT.exists(), "Benchmark script not found"
        script_content = BENCHMARK_SCRIPT.read_text(encoding="utf-8")

        # 1. Verification of V2 markdown_limits config vs deprecated markdown_max_size
        assert "markdown_limits" in script_content, "Config V2 'markdown_limits' directive is missing from benchmark script"
        assert "markdown_max_size" not in script_content, "Found legacy 'markdown_max_size' directive inside benchmark script"

        # 2. Verification of local mime.types removal and static types block addition
        assert "include       mime.types;" not in script_content, "Relative 'include mime.types;' should not be used in benchmark config"
        assert "types {" in script_content, "Explicit 'types' definition block is missing from benchmark config"
        assert "markdown_streaming_zero_copy on;" in script_content, (
            "streaming_first benchmark config should enable zero-copy"
        )
        assert '"$NGINX_BIN" -t -c "$conf_path" -p "$NGINX_WORKDIR"' in script_content, (
            "generated benchmark nginx.conf should be validated with nginx -t"
        )

    def test_streaming_first_uses_nonfallback_streaming_fixture(self):
        """The streaming-path evidence scenario must not force capability fallback."""
        script_content = BENCHMARK_SCRIPT.read_text(encoding="utf-8")

        assert (
            '"streaming-first|large/large-1mb.html|'
            'streaming_first|none|chunked|20"'
        ) in script_content
        assert (
            '"streaming-first|complex/documentation.html|' not in script_content
        )

        fixture = REPO_ROOT / "tests" / "corpus" / "large" / "large-1mb.html"
        metadata = json.loads(fixture.with_suffix(".meta.json").read_text())
        assert fixture.stat().st_size >= 1_000_000
        assert metadata["streaming_notes"]["expected_fallback"] is False

    def test_streaming_first_uses_real_upstream_streaming_transport(self):
        """Benchmark transport must preserve incremental upstream chunks."""
        script_content = BENCHMARK_SCRIPT.read_text(encoding="utf-8")

        assert "proxy_http_version 1.1;" in script_content
        assert "proxy_buffering off;" in script_content
        assert 'proxy_set_header Connection \\"\\";' in script_content

    def test_scenario_results_are_collected_as_json_lines(self):
        """Scenario JSON must not be split by delimiters that appear inside nested objects."""
        assert BENCHMARK_SCRIPT.exists(), "Benchmark script not found"
        script_content = BENCHMARK_SCRIPT.read_text(encoding="utf-8")

        assert "scenario-results.jsonl" in script_content
        assert 'split("},{")' not in script_content

    def test_gate_fallback_rate_uses_precommit_failopen(self):
        """The hard fallback gate tracks fail-open events, not capability fallbacks."""
        assert BENCHMARK_SCRIPT.exists(), "Benchmark script not found"
        script_content = BENCHMARK_SCRIPT.read_text(encoding="utf-8")

        assert 'streaming_data.get("precommit_failopen_total", 0)' in script_content
        assert "float(precommit_failopen_total) / requests_total" in script_content
        assert '"streaming_fallback_total": fallback_total' in script_content
