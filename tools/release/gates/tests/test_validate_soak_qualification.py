"""Regression tests for tools/release/gates/validate_soak_qualification.py."""

from __future__ import annotations

import errno
import json
import os
import types
from pathlib import Path

import pytest

from tools.release.gates import validate_soak_qualification as validator

REPO_ROOT = Path(validator.__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "release"
MANIFEST = FIXTURE_DIR / "soak-qualification-manifest.json"


class _FakePasswd:
    """Minimal stand-in for pwd.struct_passwd with a distinctive id."""

    pw_uid = 65534
    pw_gid = 65534
    pw_name = "nobody"


class _FakeGroup:
    """Stand-in for grp.struct_group; the name differs from the user."""

    gr_name = "nogroup"


def _run_fixture(record_name: str) -> int:
    return validator.main(
        [
            "--mode",
            "fixture",
            "--manifest",
            str(MANIFEST),
            "--record-input",
            str(FIXTURE_DIR / record_name),
        ]
    )


def test_nginx_is_started_in_its_own_session(monkeypatch, tmp_path):
    """NGINX must get a new session, or the group-kill design breaks.

    Without `start_new_session=True` the child shares the test runner's process
    group, so `os.getpgid(nginx.pid)` returns the group leader's id and the
    SIGTERM/SIGKILL aimed at "NGINX's group" could reach unrelated processes.
    Every `_stop_nginx` test stubs `getpgid`/`killpg`, so none of them observes
    the real spawn arguments -- removing the flag left the whole suite green.
    """
    recorded: dict = {}

    class FakePopen:
        pid = 4242

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            raise ProcessLookupError

    def fake_popen(args, **kwargs):
        recorded["args"] = args
        recorded["kwargs"] = kwargs
        return FakePopen()

    runtime = tmp_path / "markdown-soak-session"
    (runtime / "logs").mkdir(parents=True)
    module_so = tmp_path / "module.so"
    module_so.write_bytes(b"")

    # SOAK_RUNTIME_ROOT is bound to the real build tree at import time, so
    # redirect it as well: otherwise the real prepare_runtime creates a runtime
    # directory under build/soak-runtime and nothing cleans it up -- the very
    # leak the cleanup regression tests guard against.
    runtime_root = tmp_path / "build" / "soak-runtime"
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))
    monkeypatch.setattr(validator, "validate_read_path", lambda p, **k: Path(p))
    monkeypatch.setattr(validator.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(validator.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(validator, "_port_holder", lambda port: None)
    monkeypatch.setattr(validator, "wait_for_ready", lambda url, **k: (True, ""))
    monkeypatch.setattr(validator, "_stop_nginx", lambda nginx: None)

    nginx_bin = tmp_path / "nginx"
    nginx_bin.write_bytes(b"")
    nginx_bin.chmod(0o755)
    monkeypatch.setattr(validator, "_validated_nginx_binary", lambda: nginx_bin)

    validator.prepare_runtime("http://127.0.0.1:19200",
                              {"corpus": [{"id": "small"}]}, str(module_so))

    assert recorded["kwargs"].get("start_new_session") is True, {
        "kwargs": sorted(recorded["kwargs"]),
        "msg": "NGINX must be started in its own session",
    }

    # Clean up the directory the real _runtime_directory created under tmp_path.
    for created in runtime_root.glob("markdown-soak-*"):
        validator._cleanup_runtime_directory(created)


def test_startup_log_is_captured_instead_of_discarded(tmp_path, monkeypatch):
    """NGINX's own diagnostics must survive a readiness failure.

    The failure used to be reported as a bare "nginx did not become ready"
    because both streams went to DEVNULL, so the reason -- a bad module path, a
    missing directive, a port clash -- was destroyed before anyone read it.
    """
    captured: dict = {}

    class FakeNginx:
        pid = 4242

        def __init__(self, *a, **kw):
            captured["stdout"] = kw.get("stdout")
            captured["stderr"] = kw.get("stderr")

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            pass

    monkeypatch.setattr(validator.subprocess, "Popen", FakeNginx)
    monkeypatch.setattr(validator, "build_corpus", lambda *a: {"small": "small.html"})
    # Port occupancy is not what this test is about; a real holder would abort
    # the run for an unrelated reason.
    monkeypatch.setattr(validator, "_port_holder", lambda port: None)
    # The runtime directory must live inside the repository: the startup log is
    # written through validate_write_path_within_root.
    runtime_dir = validator.REPO_ROOT / "build" / "soak-runtime" / "markdown-soak-selftest"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(validator, "_runtime_directory", lambda: runtime_dir)
    monkeypatch.setattr(validator, "write_nginx_conf", lambda *a, **kw: None)
    monkeypatch.setattr(
        validator, "_validated_nginx_binary", lambda: Path("/bin/true")
    )
    real_cleanup = validator._cleanup_runtime_directory
    try:
        _runtime, _corpus, nginx = validator.prepare_runtime(
            "http://127.0.0.1:8080", {}, ""
        )
        # Asserted before the cleanup below removes the directory.
        log_exists = (runtime_dir / "logs" / "startup.log").exists()
    finally:
        # The real cleanup, not a no-op: the directory name carries the
        # markdown-soak- prefix that cleanup recognises, so a stubbed cleanup
        # would leave it behind in build/soak-runtime.
        real_cleanup(runtime_dir)

    assert captured["stdout"] is not validator.subprocess.DEVNULL, (
        "NGINX stdout must be captured, not discarded"
    )
    assert captured["stderr"] is validator.subprocess.STDOUT, (
        "NGINX stderr must be merged into the captured log"
    )
    assert log_exists, (
        "the startup log must exist on disk for the failure path to read"
    )
    # `nginx` is intentionally left bound: the finally below closes it, and
    # deleting the name here would only hide a later accidental use.


def test_port_holder_detects_a_bound_port():
    """A stale NGINX must be named, not reported as "did not become ready"."""
    import socket

    # The gate probes loopback, so the holder binds loopback too. Binding the
    # wildcard address here would not be portable: Linux refuses a second bind
    # when any socket holds the port, while macOS lets a loopback bind succeed
    # alongside a wildcard listener, so a wildcard holder only exercises the
    # probe on Linux. A loopback holder is detected on both.
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        description = validator._port_holder(port)
        assert description is not None, "a listening port must be reported"
        assert "in use" in description, description
    finally:
        holder.close()


def test_port_holder_probe_mirrors_nginx_reuseaddr():
    """The probe must bind the way NGINX binds.

    NGINX sets SO_REUSEADDR on its listen socket, so a port left in TIME_WAIT
    binds fine for it. A probe without the flag refuses that same port and
    aborts a run that would have succeeded. Asserted directly because a
    behavioural test cannot see it: with the flag set, binding an unbound port
    succeeds either way.
    """
    import socket

    seen: list[int] = []
    real_socket = socket.socket

    class RecordingSocket(real_socket):  # type: ignore[misc,valid-type]
        def setsockopt(self, level, optname, value, *a):
            seen.append(optname)
            return super().setsockopt(level, optname, value, *a)

    # The validator imports socket inside the function, so patch the module
    # attribute it resolves at call time.
    try:
        socket.socket = RecordingSocket  # type: ignore[assignment]
        validator._port_holder(0)
    finally:
        socket.socket = real_socket  # type: ignore[assignment]

    assert socket.SO_REUSEADDR in seen, seen


def test_port_holder_probes_only_ipv4():
    """The generated config is `listen <port>;` -- IPv4 wildcard only.

    Probing IPv6 as well would report an occupied v6 socket as a conflict for a
    port NGINX binds regardless, aborting a run that would have served fine.
    """
    import socket

    created: list[int] = []
    real_socket = socket.socket

    class RecordingSocket(real_socket):  # type: ignore[misc,valid-type]
        def __init__(self, family=socket.AF_INET, *a, **kw):
            created.append(family)
            super().__init__(family, *a, **kw)

    try:
        socket.socket = RecordingSocket  # type: ignore[assignment]
        validator._port_holder(0)
    finally:
        socket.socket = real_socket  # type: ignore[assignment]

    assert created, "no probe socket was created"
    assert all(f == socket.AF_INET for f in created), created


def test_port_holder_is_none_for_a_free_port():
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    assert validator._port_holder(port) is None


def test_port_holder_detects_a_holder_on_a_specific_address():
    """A holder bound to one concrete address must still be detected.

    A loopback-only probe misses this case: on Linux a loopback bind succeeds
    alongside a listener on a concrete address, so only the wildcard probe
    catches it. BSD-derived kernels (macOS) let *any* bind succeed there, so the
    case is only observable on Linux -- the platform CI runs the soak on. The
    bind-address pair itself is pinned by test_port_probe_pins_both_bind_addresses
    so coverage does not depend on this host's kernel.
    """
    import socket
    import sys

    if sys.platform != "linux":
        pytest.skip("only Linux refuses a bind alongside a concrete-address holder")

    try:
        concrete = socket.gethostbyname(socket.gethostname())
    except OSError:
        pytest.skip("no resolvable non-loopback address")
    if concrete.startswith("127."):
        pytest.skip("host resolves to loopback only")

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        holder.bind((concrete, 0))
    except OSError as exc:
        pytest.skip(f"cannot bind a holder to {concrete}: {exc}")
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        description = validator._port_holder(port)
        assert description is not None, (
            f"a holder on {concrete} must be reported; a loopback bind succeeds "
            "alongside it, so only the wildcard probe sees this"
        )
        assert "in use" in description, description
    finally:
        holder.close()


def test_port_probe_pins_both_bind_addresses(monkeypatch):
    """Both probe addresses are load-bearing; dropping either loses coverage."""
    bound: list[str] = []

    reuseaddr: list[int] = []

    class FakeSocket:
        def __init__(self, family=None, type=None):
            self.closed = False

        def setsockopt(self, level, optname, value, *args):
            import socket as socket_module

            if optname == socket_module.SO_REUSEADDR:
                reuseaddr.append(value)

        def bind(self, address):
            bound.append(address[0])

        def close(self):
            self.closed = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    # `socket` is imported inside _port_holder, so patch the module attribute it
    # resolves through.
    import socket as socket_module

    monkeypatch.setattr(socket_module, "socket", lambda *a, **k: FakeSocket())

    assert validator._port_holder(19200) is None
    assert bound == ["127.0.0.1", "0.0.0.0"], {
        "bound": bound,
        "msg": "the loopback probe catches loopback/wildcard holders, the "
               "wildcard probe catches concrete-address holders",
    }
    # Both probes need SO_REUSEADDR: without it a TIME_WAIT socket would make
    # the probe report a port NGINX can still bind, aborting a valid run.
    assert len(reuseaddr) == 2 and all(v == 1 for v in reuseaddr), {
        "SO_REUSEADDR": reuseaddr,
        "msg": "each probe socket must set SO_REUSEADDR",
    }


def test_prepare_runtime_refuses_to_start_on_an_occupied_port(tmp_path, monkeypatch):
    """The gate must say the port is taken rather than time out on readiness."""
    import socket

    # Loopback, matching the probe; see the note on the sibling test.
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    runtime = validator.REPO_ROOT / "build" / "soak-runtime" / "markdown-soak-port-selftest"
    runtime.mkdir(parents=True, exist_ok=True)
    try:
        monkeypatch.setattr(validator, "build_corpus", lambda *a: {"small": "small.html"})
        monkeypatch.setattr(validator, "_runtime_directory", lambda: runtime)
        monkeypatch.setattr(validator, "write_nginx_conf", lambda *a, **kw: None)
        monkeypatch.setattr(
            validator, "_validated_nginx_binary", lambda: Path("/bin/true")
        )
        with pytest.raises(ValueError) as excinfo:
            validator.prepare_runtime(f"http://127.0.0.1:{port}", {}, "")
        assert "already in use" in str(excinfo.value), excinfo.value
    finally:
        holder.close()
        validator._cleanup_runtime_directory(runtime)


def _signal_fallback_nginx(captured: list[int]):
    """A master whose group signal fails, so only the per-process path runs."""

    class FakeNginx:
        pid = 4242

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            captured.append(sig)

    return FakeNginx()


def test_the_session_removes_its_runtime_directory_on_failure(
    monkeypatch, tmp_path
):
    """The session's own cleanup must run when the load phase blows up.

    prepare_runtime cleans up after itself, but the session creates nothing and
    still owns the teardown; deleting its finally clause leaked the directory
    with the whole suite green.
    """
    created = tmp_path / "build" / "soak-runtime" / "markdown-soak-session"
    order: list[str] = []

    def note_stop(_nginx):
        order.append("stop")

    def note_clean(_path):
        order.append("clean")

    monkeypatch.setattr(validator, "_cleanup_runtime_directory", note_clean)
    monkeypatch.setattr(validator, "_stop_nginx", note_stop)

    class FakeNginx:
        pid = 5150
        send_signal = staticmethod(lambda sig: None)

        def wait(self, timeout=None):
            return 0

    def fake_prepare(base, manifest, so):
        created.mkdir(parents=True, exist_ok=True)
        return created, {"small": "small.html"}, FakeNginx()

    monkeypatch.setattr(validator, "prepare_runtime", fake_prepare)
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", tmp_path / "build" / "soak-runtime")
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))
    monkeypatch.setattr(validator, "wait_for_ready", lambda url, **k: (True, ""))
    monkeypatch.setattr(validator, "find_worker_pid", lambda d: 5150)
    monkeypatch.setattr(
        validator, "assert_worker_dropped_privileges", lambda pid: None
    )

    # A load-branch failure must not stop the session from returning a numeric
    # window: the caller records it, and None there is not a number.
    monkeypatch.setattr(validator, "measure_drain", lambda pid: (0, True, []))
    monkeypatch.setattr(validator, "read_module_peak_memory", lambda url: 1)

    class ExplodingLoad:
        def __call__(self, *args, **kwargs):
            raise RuntimeError("load phase failed")

    monkeypatch.setattr(validator, "run_load_loop", ExplodingLoad())

    with pytest.raises(RuntimeError, match="load phase failed"):
        validator._run_soak_session(
            "http://127.0.0.1:19200", {"duration_minutes": 1, "concurrency": 1}, ""
        )

    # Teardown must survive the failure: NGINX left running keeps the port, and
    # the runtime directory left behind blocks the next run.
    assert order == ["stop", "clean"], {
        "order": order,
        "msg": "NGINX must be stopped before its working tree is removed; "
        "cleaning under a running NGINX can leave files behind",
    }


def test_the_session_reports_a_numeric_window_when_the_load_branch_fails(
    monkeypatch, tmp_path
):
    """A readiness failure still has to produce a numeric window.

    The load branch never runs in that case, so `ended` stays None unless the
    finally stamps it; the caller records the window either way.
    """
    runtime = tmp_path / "markdown-soak-window"
    runtime.mkdir()

    class FakeNginx:
        pid = 6161

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            raise ProcessLookupError

    def fake_prepare(base, manifest, so):
        return runtime, {"small": "small.html"}, FakeNginx()

    monkeypatch.setattr(validator, "prepare_runtime", fake_prepare)
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(
        validator, "validate_write_path_within_root", lambda p, root, **k: Path(p)
    )
    monkeypatch.setattr(validator, "_stop_nginx", lambda n: None)
    monkeypatch.setattr(validator, "_cleanup_runtime_directory", lambda p: None)
    # Readiness failure: the load branch is skipped entirely, which is the path
    # that leaves `ended` unset.
    monkeypatch.setattr(
        validator, "wait_for_ready", lambda url, **k: (False, "connection refused")
    )
    monkeypatch.setattr(validator, "_startup_failure_reason", lambda d: "")

    result = validator._run_soak_session(
        "http://127.0.0.1:19200", {"duration_minutes": 1, "concurrency": 1}, ""
    )

    assert isinstance(result.get("ended"), (int, float)), {
        "ended": result.get("ended"),
        "msg": "a readiness failure must still report a numeric window",
    }


def test_a_non_conflict_bind_failure_is_not_reported_as_a_conflict(monkeypatch):
    """Only EADDRINUSE means a conflict.

    Reporting every bind error as "already in use" sends the reader looking for a
    stale process when the port is free and the host could not hand out the
    address at all.
    """
    import socket

    def refuse(*args, **kwargs):
        raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")

    monkeypatch.setattr(socket.socket, "bind", refuse)

    assert validator._port_holder(19200) is None, {
        "msg": "a non-conflict bind error must not be reported as a port conflict",
    }


def test_an_address_in_use_is_still_reported_as_a_conflict():
    """The discrimination must not swallow the real conflict."""
    import socket

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with holder:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))
        # Listen as well as bind: the conflict NGINX hits is a listening socket,
        # and a merely bound one does not occupy the port for a second bind.
        holder.listen(1)
        port = holder.getsockname()[1]

        assert validator._port_holder(port) is not None, {
            "msg": "an occupied port must still be reported",
        }


def test_a_runtime_tree_that_cannot_be_removed_is_reported(
    tmp_path, monkeypatch, capsys
):
    """A silent cleanup failure is the one that blocks the next run."""
    created = tmp_path / "markdown-soak-stuck"
    created.mkdir()

    def refuse(path, **kwargs):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(validator.shutil, "rmtree", refuse)

    validator._remove_runtime_tree(created)

    err = capsys.readouterr().err
    assert "could not remove the soak runtime directory" in err, {"stderr": err}


def test_a_runtime_tree_that_cannot_be_removed_does_not_raise(tmp_path, monkeypatch):
    """Cleanup runs on failure paths too; a raise there would mask the reason."""
    created = tmp_path / "markdown-soak-stuck"
    created.mkdir()
    monkeypatch.setattr(
        validator.shutil,
        "rmtree",
        lambda path, **kwargs: (_ for _ in ()).throw(PermissionError()),
    )

    validator._remove_runtime_tree(created)


def test_a_failed_group_lookup_still_signals_the_group(monkeypatch):
    """A session leader is its own process group, so the pid is the group id.

    Falling back to signalling the master alone would leave the workers holding
    the listen socket, which is exactly the failure this teardown exists to
    prevent.
    """
    groups: list[tuple[int, int]] = []
    import signal as signal_mod

    monkeypatch.setattr(
        validator.os, "getpgid", lambda pid: (_ for _ in ()).throw(PermissionError())
    )
    monkeypatch.setattr(
        validator.os, "killpg", lambda pgid, sig: groups.append((pgid, sig))
    )
    nginx = _signal_fallback_nginx([])
    nginx.pid = 4242

    validator._stop_nginx(nginx)

    assert groups, {
        "msg": "a failed getpgid must still signal the process group, not the master alone",
    }
    assert all(pgid == 4242 for pgid, _ in groups), {"groups": groups}
    assert any(sig == signal_mod.SIGKILL for _, sig in groups), {
        "groups": groups,
        "msg": "the group must receive the unconditional final SIGKILL",
    }


def test_stop_nginx_reports_a_permission_failure(tmp_path, monkeypatch, capsys):
    """A signal we could not deliver must be reported, not swallowed.

    Silently ignoring a permission failure leaves NGINX running and still holding
    the listen socket, so the next run fails to bind with nothing pointing back
    here.
    """
    import signal as signal_mod

    captured: list[int] = []
    nginx = _signal_fallback_nginx(captured)

    # Both the lookup and the group signal fail, so only the process remains.
    monkeypatch.setattr(
        validator.os, "getpgid", lambda pid: (_ for _ in ()).throw(PermissionError())
    )
    monkeypatch.setattr(
        validator.os,
        "killpg",
        lambda pgid, sig: (_ for _ in ()).throw(PermissionError()),
    )

    def denied(sig):
        raise PermissionError("operation not permitted")

    nginx.send_signal = denied  # type: ignore[method-assign]

    validator._stop_nginx(nginx)

    err = capsys.readouterr().err
    assert "could not signal NGINX" in err, {"stderr": err}
    assert str(signal_mod.SIGTERM) in err or "PermissionError" in err, {
        "stderr": err
    }


def test_stop_nginx_is_silent_when_the_process_is_already_gone(
    tmp_path, monkeypatch, capsys
):
    """A missing process is not a failure: nothing was left running."""
    captured: list[int] = []
    nginx = _signal_fallback_nginx(captured)

    monkeypatch.setattr(
        validator.os, "getpgid", lambda pid: (_ for _ in ()).throw(PermissionError())
    )

    def gone(sig):
        raise ProcessLookupError("no such process")

    nginx.send_signal = gone  # type: ignore[method-assign]

    validator._stop_nginx(nginx)

    err = capsys.readouterr().err
    assert "could not signal NGINX" not in err, {"stderr": err}


def test_stop_nginx_still_signals_the_process_when_the_group_is_unknown(
    tmp_path, monkeypatch
):
    """Losing the group signal must not skip the only signal we have left.

    The master pid is still a usable group id here, because a child started with
    start_new_session=True is a session leader. This covers the case where even
    that fails, so the process signal remains the last teardown available.
    """
    import signal as signal_mod

    captured: list[int] = []
    nginx = _signal_fallback_nginx(captured)

    monkeypatch.setattr(
        validator.os, "getpgid", lambda pid: (_ for _ in ()).throw(ProcessLookupError())
    )
    monkeypatch.setattr(
        validator.os,
        "killpg",
        lambda pgid, sig: (_ for _ in ()).throw(PermissionError()),
    )

    validator._stop_nginx(nginx)

    # SIGTERM and the unconditional final SIGKILL both have to reach the process.
    assert signal_mod.SIGTERM in captured, {"signals": captured}
    assert signal_mod.SIGKILL in captured, {
        "signals": captured,
        "msg": "the group is unknown, so the process signal is the only teardown",
    }


def test_stop_nginx_kills_a_surviving_worker_after_a_clean_master_exit(
    tmp_path, monkeypatch
):
    """A worker that outlives the master must still get signalled.

    The master usually honours SIGTERM and exits promptly, so `wait()` returns
    without a timeout. Gating the final SIGKILL on that timeout let the worker
    survive and keep the listen socket, which is what made the next run fail to
    bind. This test keeps `wait()` on the happy path, which is the case the
    timeout-based test above never exercises.
    """
    import signal as signal_mod

    group: list[tuple[int, int]] = []

    class FakeNginx:
        pid = 4242

        def wait(self, timeout=None):
            return 0  # the master exits cleanly on SIGTERM

        def send_signal(self, sig):
            raise AssertionError("the group signal should not need a fallback")

    nginx = FakeNginx()
    monkeypatch.setattr(validator.os, "getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(
        validator.os, "killpg", lambda pgid, sig: group.append((pgid, sig))
    )

    validator._stop_nginx(nginx)

    expected_pgid = nginx.pid + 1
    assert (expected_pgid, signal_mod.SIGTERM) in group, {"group": group}
    assert (expected_pgid, signal_mod.SIGKILL) in group, {
        "group": group,
        "msg": "a surviving worker keeps the listen socket without the final SIGKILL",
    }


def test_stop_nginx_resolves_the_group_before_the_master_exits(tmp_path, monkeypatch):
    """The pgid must be read while the master is alive, not after it exits.

    Reading it lazily meant a master that exited on SIGTERM made every later
    signal fall back to the single process, so the workers were never reached.
    """
    seen: list[int] = []

    class FakeNginx:
        pid = 4242

        def wait(self, timeout=None):
            # getpgid(nginx.pid) starts failing once the master is reaped.
            return 0

        def send_signal(self, sig):
            seen.append(-sig)

    nginx = FakeNginx()
    lookups = {"n": 0}

    def fake_getpgid(pid):
        lookups["n"] += 1
        return pid + 1

    monkeypatch.setattr(validator.os, "getpgid", fake_getpgid)
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(
        validator.os, "killpg", lambda pgid, sig: sent.append((pgid, sig))
    )

    validator._stop_nginx(nginx)

    assert lookups["n"] == 1, {
        "lookups": lookups["n"],
        "msg": "getpgid must be resolved once, before any signal",
    }
    assert len(sent) == 2, {"sent": sent, "fallback": seen}
    assert all(pgid == nginx.pid + 1 for pgid, _ in sent), {"sent": sent}
    assert not seen, "the cached pgid must be used instead of the per-process path"


def test_stop_nginx_signals_the_process_group(tmp_path, monkeypatch):
    """The worker must be signalled with the master.

    Signalling only the master leaves the worker holding the listen socket, so
    the next run in the same environment cannot bind.
    """
    import signal as signal_mod

    # Tracked separately, and the group records the pgid it was given: a test
    # that only watched a signal list would pass even if the implementation
    # signalled a single process instead of the group.
    group: list[tuple[int, int]] = []
    fallback: list[int] = []
    calls = {"n": 0}

    class FakeNginx:
        pid = 4242

        def wait(self, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise __import__("subprocess").TimeoutExpired("nginx", timeout)
            return 0

        def send_signal(self, sig):
            fallback.append(sig)

    nginx = FakeNginx()
    monkeypatch.setattr(validator.os, "getpgid", lambda pid: pid + 1)
    monkeypatch.setattr(
        validator.os, "killpg", lambda pgid, sig: group.append((pgid, sig))
    )

    validator._stop_nginx(nginx)

    expected_pgid = nginx.pid + 1
    assert (expected_pgid, signal_mod.SIGTERM) in group, {
        "group": group,
        "fallback": fallback,
    }
    assert (expected_pgid, signal_mod.SIGKILL) in group, {
        "group": group,
        "fallback": fallback,
    }
    assert not fallback, "killpg succeeded; the per-process fallback should not run"


def test_worker_user_is_pinned_only_when_the_master_is_root(monkeypatch):
    """A root master must run its workers unprivileged, by name.

    NGINX drops worker privileges only for a super-user master. Without an
    explicit `user` the drop is implicit and the directories it reads were never
    prepared for it, which produced a bare 403 with no usable diagnosis.
    """
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())

    assert validator.nginx_worker_user() == "nobody"


def test_worker_user_is_absent_for_a_non_root_master(monkeypatch):
    """A non-root master cannot drop privileges, so no directive is emitted."""
    monkeypatch.setattr(validator.os, "geteuid", lambda: 1000, raising=False)

    assert validator.nginx_worker_user() is None


def test_worker_user_refuses_to_run_workers_as_root(monkeypatch):
    """A root master with no unprivileged account must fail, not fall back.

    Without a `user` directive the workers would keep running as root, which is
    exactly what the soak exists to avoid -- and the run would still pass, so the
    gap has to be an error.
    """
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        validator.pwd, "getpwnam", lambda name: (_ for _ in ()).throw(KeyError(name))
    )

    with pytest.raises(RuntimeError) as excinfo:
        validator.nginx_worker_user()

    assert "refusing to start NGINX with root workers" in str(excinfo.value), {
        "msg": str(excinfo.value)[:160],
    }


def test_worker_user_falls_back_to_nginx_when_nobody_is_absent(monkeypatch):
    """Some images ship `nginx` but not `nobody`; either is acceptable."""
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)

    def lookup(name):
        if name == "nobody":
            raise KeyError(name)
        return _FakePasswd()

    monkeypatch.setattr(validator.pwd, "getpwnam", lookup)

    assert validator.nginx_worker_user() == "nginx"


def test_worker_user_ignores_an_account_named_like_root(monkeypatch):
    """A `nobody` that maps to uid 0 is not a privilege drop.

    `setuid(0)` is a no-op, so NGINX's workers would keep running as root while
    the gate reported that the identity had been pinned. The name is not the
    point; the resolved uid is.
    """

    class RootNamedNobody:
        pw_uid = 0
        pw_gid = 0
        pw_name = "nobody"

    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: RootNamedNobody())

    with pytest.raises(RuntimeError) as excinfo:
        validator.nginx_worker_user()

    assert "refusing to start NGINX with root workers" in str(excinfo.value), {
        "msg": str(excinfo.value)[:160],
    }


def test_worker_user_skips_a_root_nobody_and_uses_the_next_account(monkeypatch):
    """The uid check must skip to the next candidate, not give up."""
    accounts = {"nobody": 0, "nginx": 101}

    class Entry:
        def __init__(self, uid):
            self.pw_uid = uid
            self.pw_gid = uid
            self.pw_name = ""

    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        validator.pwd, "getpwnam", lambda name: Entry(accounts[name])
    )

    assert validator.nginx_worker_user() == "nginx"


def test_worker_identity_is_read_from_proc(monkeypatch, tmp_path):
    """The uid comes from /proc, not from the account name."""
    status = tmp_path / "status"
    status.write_text(
        "Name:\tnginx\nState:\tS (sleeping)\nUid:\t65534\t65534\t65534\t65534\n"
        "Gid:\t65534\t65534\t65534\t65534\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validator.pathlib, "Path", lambda p: status if "status" in str(p) else Path(str(p)))

    assert validator.read_process_uid(1234) == (65534, 65534, 65534, 65534)


def test_worker_with_a_saved_root_uid_is_not_privileged(monkeypatch):
    """A worker whose saved-set-uid is still 0 can setuid(0) back.

    NGINX's setuid() clears the effective uid but leaves the saved-set-uid
    alone, so a name that resolves correctly can still leave the worker able to
    regain root. All of real/effective/saved must be non-root.
    """
    monkeypatch.setattr(
        validator, "read_process_uid", lambda pid: (65534, 65534, 0, 65534)
    )

    with pytest.raises(ValueError) as excinfo:
        validator.assert_worker_dropped_privileges(1234)

    assert "retains uid 0" in str(excinfo.value), {"msg": str(excinfo.value)[:140]}


def test_worker_with_a_root_filesystem_uid_is_not_privileged(monkeypatch):
    """The fs uid governs file access, so it must be non-root too."""
    monkeypatch.setattr(
        validator, "read_process_uid", lambda pid: (65534, 65534, 65534, 0)
    )

    with pytest.raises(ValueError) as excinfo:
        validator.assert_worker_dropped_privileges(1234)

    assert "retains uid 0" in str(excinfo.value), {"msg": str(excinfo.value)[:140]}


def test_worker_identity_read_failure_is_not_assumed_safe(monkeypatch):
    """An unreadable identity must fail, not be treated as fine."""
    monkeypatch.setattr(validator, "read_process_uid", lambda pid: None)

    with pytest.raises(ValueError) as excinfo:
        validator.assert_worker_dropped_privileges(1234)

    assert "refusing to assume" in str(excinfo.value), {"msg": str(excinfo.value)[:140]}


def test_worker_running_as_root_fails_the_gate(monkeypatch):
    """A worker that stayed root invalidates the soak it is supposed to model."""
    monkeypatch.setattr(validator, "read_process_uid", lambda pid: (0, 0, 0, 0))

    with pytest.raises(ValueError) as excinfo:
        validator.assert_worker_dropped_privileges(1234)

    assert "retains uid 0" in str(excinfo.value), {"msg": str(excinfo.value)[:140]}


def test_worker_with_a_real_non_root_uid_passes(monkeypatch):
    """The gate must accept the normal case, or it would fail every run."""
    monkeypatch.setattr(
        validator, "read_process_uid", lambda pid: (65534, 65534, 65534, 65534)
    )

    validator.assert_worker_dropped_privileges(1234)


def test_grant_worker_traversal_adds_only_other_execute(
    monkeypatch, tmp_path
):
    """Only o+x for other; group and read stay untouched.

    0o711 would also grant execute to group; o+rx would grant read too. Neither
    is needed: the worker walks to a file it already knows the name of.
    """
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)

    target = tmp_path / "markdown-soak-x"
    target.mkdir()
    target.chmod(0o700)

    validator._grant_worker_traversal(target)

    mode = target.stat().st_mode & 0o777
    assert mode & 0o001, f"the worker cannot traverse: {oct(mode)}"
    assert not mode & 0o010, f"group gained execute: {oct(mode)}"
    assert not mode & 0o004, f"the directory became listable: {oct(mode)}"
    assert mode & 0o700 == 0o700, f"the owner lost access: {oct(mode)}"


def test_grant_worker_traversal_never_changes_ownership(monkeypatch, tmp_path):
    """A validation gate must not hand a directory to the worker account."""
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())
    chowned: list = []
    monkeypatch.setattr(
        validator.os, "chown", lambda path, uid, gid: chowned.append(path)
    )

    target = tmp_path / "markdown-soak-own"
    target.mkdir()
    target.chmod(0o700)

    validator._grant_worker_traversal(target)

    assert not chowned, {"chowned": [str(p) for p in chowned]}


def test_grant_worker_traversal_is_a_no_op_without_a_privilege_drop(
    monkeypatch, tmp_path
):
    """A non-root master keeps its own access, so nothing may change."""
    monkeypatch.setattr(validator.os, "geteuid", lambda: 1000, raising=False)

    target = tmp_path / "markdown-soak-y"
    target.mkdir()
    target.chmod(0o700)

    validator._grant_worker_traversal(target)

    assert target.stat().st_mode & 0o777 == 0o700


def test_grant_worker_traversal_is_idempotent(monkeypatch, tmp_path):
    """Re-running must not accumulate permission bits.

    The grant is a no-op unless NGINX drops privileges, so without forcing the
    root path this passes vacuously on an ordinary-user runner.
    """
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())

    target = tmp_path / "markdown-soak-y"
    target.mkdir()
    target.chmod(0o700)

    validator._grant_worker_traversal(target)
    first = target.stat().st_mode & 0o777
    validator._grant_worker_traversal(target)

    assert first == 0o701, {
        "mode": oct(first),
        "msg": "the grant must actually have applied",
    }
    assert target.stat().st_mode & 0o777 == first


def test_grant_worker_traversal_tolerates_a_missing_directory(monkeypatch, tmp_path):
    """A path that cannot be stat'ed must not abort the setup.

    Forced onto the root path so the loop body actually runs; otherwise the grant
    returns before touching the filesystem and nothing is exercised.
    """
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())

    validator._grant_worker_traversal(tmp_path / "absent")

    # And the same call on a real directory still widens it, proving the helper
    # is not simply returning for everything.
    present = tmp_path / "markdown-soak-present"
    present.mkdir()
    present.chmod(0o700)
    validator._grant_worker_traversal(present)
    assert present.stat().st_mode & 0o777 == 0o701, oct(present.stat().st_mode & 0o777)


def test_generated_config_pins_the_worker_user_when_root(monkeypatch, tmp_path):
    """The config must name the worker user the directories were prepared for."""
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())
    monkeypatch.setattr(validator.grp, "getgrgid", lambda gid: _FakeGroup())
    monkeypatch.setattr(validator, "validate_read_path", lambda p, **k: Path(p))
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))

    runtime = tmp_path / "markdown-soak-conf"
    (runtime / "logs").mkdir(parents=True)
    so = tmp_path / "module.so"
    so.write_bytes(b"")

    validator.write_nginx_conf(runtime, 19200, str(tmp_path), str(so))

    conf = (runtime / "nginx.conf").read_text()
    # The group is named explicitly: NGINX resolves it by name, and on a host
    # where the worker's primary group is not called the same as the user,
    # `user nobody;` alone refuses to start.
    assert conf.startswith("user nobody nogroup;"), conf[:120]


def test_generated_config_falls_back_to_the_user_name_for_the_group(
    monkeypatch, tmp_path
):
    """An unresolvable gid must still produce a startable directive."""
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())
    monkeypatch.setattr(validator.grp, "getgrgid", lambda gid: (_ for _ in ()).throw(KeyError(gid)))
    monkeypatch.setattr(validator, "validate_read_path", lambda p, **k: Path(p))
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))

    runtime = tmp_path / "markdown-soak-conf3"
    (runtime / "logs").mkdir(parents=True)
    so = tmp_path / "module.so"
    so.write_bytes(b"")

    validator.write_nginx_conf(runtime, 19200, str(tmp_path), str(so))

    conf = (runtime / "nginx.conf").read_text()
    # No group entry for the gid: name the user alone so NGINX uses its primary
    # gid, rather than looking up a group named after the user that may not exist.
    assert conf.startswith("user nobody;"), conf[:120]
    assert "user nobody nobody;" not in conf, conf[:120]


def test_unreachable_ancestor_names_the_blocked_directory(monkeypatch, tmp_path):
    """A blocked ancestor must be named, not widened.

    The chain walk deliberately stops below the checkout, so a 0700 directory
    above it leaves the worker unable to reach the corpus. Reporting it beats a
    bare 403 from NGINX with no usable diagnosis.
    """
    monkeypatch.setattr(validator, "REPO_ROOT", (tmp_path / "checkout").resolve())
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())

    # tmp_path stands in for a 0700 home directory above the checkout. Only that
    # one object is faked: patching Path.stat globally would leak into every
    # other test in the module.
    # _unreachable_ancestor walks resolved paths, so the stub has to match on
    # the resolved form; tmp_path itself is unresolved (/var -> /private/var).
    blocked = tmp_path.resolve()
    real_stat = type(tmp_path).stat

    def fake_stat(self, *args, **kwargs):
        if self == blocked:
            class Result:
                st_mode = 0o700

            return Result()
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "stat", fake_stat)

    nested = blocked / "checkout" / "build" / "soak-runtime"
    nested.mkdir(parents=True)

    found = validator._unreachable_ancestor(nested)

    assert found == blocked, {"found": str(found) if found else None}


def test_generated_config_omits_the_user_directive_when_not_root(
    monkeypatch, tmp_path
):
    """A non-root master keeps the previous behaviour: no `user` line."""
    monkeypatch.setattr(validator.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(validator, "validate_read_path", lambda p, **k: Path(p))
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))

    runtime = tmp_path / "markdown-soak-conf2"
    (runtime / "logs").mkdir(parents=True)
    so = tmp_path / "module.so"
    so.write_bytes(b"")

    validator.write_nginx_conf(runtime, 19200, str(tmp_path), str(so))

    conf = (runtime / "nginx.conf").read_text()
    assert not conf.startswith("user "), conf[:120]
    assert "worker_processes 1;" in conf


def test_prepare_runtime_grants_traversal_on_every_ancestor(
    monkeypatch, tmp_path
):
    """Each ancestor the worker must cross needs traversal, not just the leaf.

    The 403 came from a readable fixture behind a 0700 parent: NGINX logs
    `open() ... failed (13: Permission denied)` with no hint which ancestor
    blocked it. Asserting the traversal on the runtime root and the per-run
    directory keeps that failure from returning.
    """
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", tmp_path / "build" / "soak-runtime")
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))

    recorded: list[Path] = []
    monkeypatch.setattr(
        validator, "_grant_worker_traversal",
        lambda *dirs: recorded.extend(Path(d) for d in dirs),
    )
    monkeypatch.setattr(validator, "validate_read_path", lambda p, **k: Path(p))
    monkeypatch.setattr(validator, "tempfile", types.SimpleNamespace(
        mkdtemp=lambda prefix, dir: str(tmp_path / "build" / "soak-runtime" / "markdown-soak-pinned"),
    ))

    runtime = validator._runtime_directory()
    runtime.mkdir(parents=True, exist_ok=True)

    assert runtime in [Path(d) for d in recorded], {
        "recorded": [str(d) for d in recorded],
        "msg": "the per-run directory must be traversable",
    }
    root = validator.SOAK_RUNTIME_ROOT
    assert root in [Path(d) for d in recorded], {
        "recorded": [str(d) for d in recorded],
        "msg": "the runtime root must be traversable",
    }


def test_write_nginx_conf_grants_traversal_on_the_runtime_dir(
    monkeypatch, tmp_path
):
    """The generated config's own directory must be traversable too."""
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))
    monkeypatch.setattr(validator, "validate_read_path", lambda p, **k: Path(p))

    recorded: list[Path] = []
    monkeypatch.setattr(
        validator, "_grant_worker_traversal",
        lambda *dirs: recorded.extend(Path(d) for d in dirs),
    )

    runtime = tmp_path / "markdown-soak-conf3"
    (runtime / "logs").mkdir(parents=True)
    so = tmp_path / "module.so"
    so.write_bytes(b"")

    validator.write_nginx_conf(runtime, 19200, str(tmp_path), str(so))

    assert runtime in [Path(d) for d in recorded], {
        "recorded": [str(d) for d in recorded],
        "msg": "the directory holding nginx.conf must be traversable",
    }


def test_fixtures_are_world_readable_under_a_restrictive_umask(
    monkeypatch, tmp_path
):
    """A 0077 umask must not leave the fixtures at 0600.

    write_bytes inherits the umask, so every fixture would be unreadable to the
    unprivileged worker and NGINX would answer 403 for all of them.
    """
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))

    # Capture the real umask before any patching, then set a restrictive one so
    # mkdir and write_bytes both produce owner-only paths.
    set_umask = os.umask
    original = set_umask(0o077)
    try:
        runtime = tmp_path / "markdown-soak-umask"
        runtime.mkdir()
        runtime.chmod(0o700)
        (runtime / "html").mkdir()
        (runtime / "html").chmod(0o700)
        manifest = {"corpus": [{"id": "small"}, {"id": "medium"}, {"id": "large"}]}

        corpus = validator.build_corpus(runtime, manifest)
    finally:
        set_umask(original)

    for scenario_id, name in corpus.items():
        mode = (runtime / "html" / name).stat().st_mode & 0o777
        assert mode & 0o044, f"{scenario_id} unreadable under umask 0077: {oct(mode)}"


def test_default_runtime_dir_walks_the_whole_ancestor_chain(
    monkeypatch, tmp_path
):
    """The default branch must not name just two directories.

    mkdir(parents=True) on the runtime root can invent ancestors of its own, so
    granting traversal on the root and the leaf leaves the real blocker behind:
    a 0700 directory in between that only the chain walk reaches.
    """
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT",
                        tmp_path / "build" / "soak-runtime")
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))
    monkeypatch.delenv("SOAK_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(validator, "validate_read_path", lambda p, **k: Path(p))
    monkeypatch.setattr(validator, "tempfile", types.SimpleNamespace(
        mkdtemp=lambda prefix, dir: str(tmp_path / "build" / "soak-runtime" / "markdown-soak-chain"),
    ))

    # Track which helper the branch used. A single list would accept either,
    # so the two are recorded separately.
    chains: list[Path] = []
    direct: list[Path] = []
    monkeypatch.setattr(
        validator, "_grant_worker_traversal_chain",
        lambda directory: chains.append(Path(directory)),
    )
    monkeypatch.setattr(
        validator, "_grant_worker_traversal",
        lambda *dirs: direct.extend(Path(d) for d in dirs),
    )

    runtime = validator._runtime_directory()
    runtime.mkdir(parents=True, exist_ok=True)

    assert chains == [runtime], {
        "chain": [str(d) for d in chains],
        "direct": [str(d) for d in direct],
        "msg": "the default branch must walk the chain, not name two directories",
    }
    assert not direct, {
        "direct": [str(d) for d in direct],
        "msg": "naming the root and the leaf leaves invented ancestors blocked",
    }


def test_configured_runtime_dir_gets_traversal_on_every_ancestor(
    monkeypatch, tmp_path
):
    """SOAK_RUNTIME_DIR must be traversable too, not just the default path.

    The configured branch calls mkdir(parents=True), so it can invent ancestors
    that are 0700 under a restrictive umask; without a chain grant the worker
    cannot reach the corpus and NGINX returns 403.
    """
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))

    recorded: list[Path] = []
    monkeypatch.setattr(
        validator, "_grant_worker_traversal",
        lambda *dirs: recorded.extend(Path(d) for d in dirs),
    )

    configured = tmp_path / "build" / "soak" / "run"
    monkeypatch.setenv("SOAK_RUNTIME_DIR", str(configured))

    runtime = validator._runtime_directory()
    runtime.mkdir(parents=True, exist_ok=True)

    granted = [Path(d) for d in recorded]
    for expected in (runtime, runtime.parent, runtime.parent.parent):
        assert expected in granted, {
            "granted": [str(d) for d in granted],
            "missing": str(expected),
            "msg": "every invented ancestor must be traversable",
        }


def test_traversal_chain_leaves_the_checkout_untouched(monkeypatch, tmp_path):
    """The chain must not widen anything the gate did not create.

    An earlier version appended the repository root before breaking and chowned
    the chain to the worker, so a root-run soak transferred the whole checkout
    and build/ -- breaking later non-root writes and tripping git's
    dubious-ownership check.
    """
    # REPO_ROOT is compared against a resolved path, so point it at the resolved
    # checkout rather than tmp_path (macOS /tmp is a symlink).
    checkout = (tmp_path / "checkout").resolve()
    monkeypatch.setattr(validator, "REPO_ROOT", checkout)
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())
    chowned: list = []
    monkeypatch.setattr(
        validator.os, "chown", lambda path, uid, gid: chowned.append(path)
    )

    # The checkout itself already exists with a normal mode; everything below it
    # is created by the gate, so every level must end up traversable.
    checkout.mkdir()
    checkout.chmod(0o700)
    runtime_root = checkout / "build" / "soak-runtime"
    nested = runtime_root / "markdown-soak-x"
    nested.mkdir(parents=True)
    runtime_root.chmod(0o700)
    nested.chmod(0o700)

    # The checkout is not widened at all: the master is root and traverses it
    # anyway, so granting anything here would be pure exposure. It is left at
    # 0700 so an accidental inclusion shows up as a mode change.
    assert checkout.stat().st_mode & 0o777 == 0o700, {
        "mode": oct(checkout.stat().st_mode & 0o777),
        "msg": "the checkout root must not be widened",
    }
    # build/ is created by mkdir with the ambient umask, so pin it to 0700 here:
    # this test is about what the traversal grant changes, not about ambient
    # modes.
    build = checkout / "build"
    build.chmod(0o700)

    # Record exactly which directories were touched, so an off-by-one that pulls
    # the checkout into the chain is visible rather than implied.
    chmodded: list = []
    real_chmod = Path.chmod

    def recording_chmod(self, mode, **kwargs):
        chmodded.append((self, mode & 0o777))
        return real_chmod(self, mode, **kwargs)

    monkeypatch.setattr(Path, "chmod", recording_chmod)

    validator._grant_worker_traversal_chain(nested)

    # Every gate-created level is traversable; nothing else moves. 0700 -> 0701
    # means: reachable by name, still unlistable, still unwritable, and group
    # gains nothing -- which is what separates this from 0o711.
    for created in (build, runtime_root, nested):
        mode = created.stat().st_mode & 0o777
        assert mode == 0o701, {
            "path": str(created),
            "mode": oct(mode),
            "msg": "only o+x for other may be added to a 0700 directory",
        }

    touched = {path for path, _ in chmodded}
    assert checkout not in touched, {
        "touched": sorted(str(p) for p in touched),
        "msg": "the checkout root must never be chmod'ed by the chain walk",
    }
    # Asserted *after* the walk: before the call the list is empty no matter what
    # the walk does, so the assertion would prove nothing.
    assert not chowned, {
        "chowned": [str(p) for p in chowned],
        "msg": "the chain walk must not transfer ownership of anything",
    }


def test_traversal_chain_stops_at_the_repository_root(monkeypatch, tmp_path):
    """Nothing at or above the checkout may be widened.

    Every directory used here is created by the test under tmp_path. The earlier
    version chmod'ed tmp_path.parent -- pytest's own ancestor, shared with every
    other test in the run -- and only passed because the traversal grant is a
    no-op for a non-root master.
    """
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())

    above = tmp_path / "above"
    above.mkdir()
    above.chmod(0o700)
    checkout = above / "checkout"
    checkout.mkdir()
    checkout.chmod(0o700)
    nested = checkout / "build" / "soak-runtime" / "markdown-soak-x"
    nested.mkdir(parents=True)
    nested.chmod(0o700)

    monkeypatch.setattr(validator, "REPO_ROOT", checkout.resolve())

    validator._grant_worker_traversal_chain(nested)

    assert not checkout.stat().st_mode & 0o001, {
        "mode": oct(checkout.stat().st_mode & 0o777),
        "msg": "the checkout root must not be widened",
    }
    assert not above.stat().st_mode & 0o001, {
        "mode": oct(above.stat().st_mode & 0o777),
        "msg": "an ancestor above the checkout must not be widened",
    }
    # The gate-created levels are traversable, or the worker cannot reach them.
    for created in (checkout / "build", checkout / "build" / "soak-runtime", nested):
        mode = created.stat().st_mode & 0o777
        assert mode & 0o001, {"path": str(created), "mode": oct(mode)}


def test_corpus_root_is_reachable_by_the_unprivileged_worker(
    monkeypatch, tmp_path
):
    """The document root must be listable and readable, or NGINX returns 403.

    mkdtemp makes the enclosing directory 0700, so a readable fixture is still
    unreachable: every lookup has to traverse each ancestor by name.
    """
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))

    runtime = tmp_path / "markdown-soak-corpus"
    runtime.mkdir()
    runtime.chmod(0o700)
    # A restrictive umask is what made the document root 0700; force that here so
    # the assertion is about the fix, not about the ambient environment.
    (runtime / "html").mkdir(exist_ok=True)
    (runtime / "html").chmod(0o700)
    manifest = {"corpus": [{"id": "small"}, {"id": "medium"}, {"id": "large"}]}

    corpus = validator.build_corpus(runtime, manifest)

    docroot = runtime / "html"
    docroot_mode = docroot.stat().st_mode & 0o777
    assert docroot_mode & 0o055, f"document root not readable: {oct(docroot_mode)}"
    for scenario_id, name in corpus.items():
        fixture_mode = (docroot / name).stat().st_mode & 0o777
        assert fixture_mode & 0o004, (
            f"{scenario_id} fixture is not world-readable: {oct(fixture_mode)}"
        )


def test_the_package_hook_fails_the_run_on_a_leak(monkeypatch, tmp_path):
    """The session hook must fail the run, not just print.

    Reporting a leftover without failing leaves CI green while directories
    accumulate, which is the regression the hook exists to prevent.
    """
    import tools.release.gates.tests.conftest as hook

    class FakeConfig:
        class pluginmanager:
            @staticmethod
            def get_plugin(name):
                return None

    class FakeSession:
        config = FakeConfig()
        exitstatus = 0

    monkeypatch.setattr(hook, "_leaked_runtime_dirs", lambda: ["markdown-soak-leak"])
    session = FakeSession()

    hook.pytest_sessionfinish(session, 0)

    assert session.exitstatus != 0, {
        "exitstatus": session.exitstatus,
        "msg": "a leaked runtime directory must fail the run",
    }


def test_the_suite_leaves_no_runtime_directory_behind() -> None:
    """No test may leave a directory in the real build tree.

    SOAK_RUNTIME_ROOT is bound to build/soak-runtime at import time, so a test
    that exercises the real prepare_runtime without redirecting it creates a
    directory there that nothing cleans up. That is the leak this branch fixed,
    so it is asserted rather than trusted.

    This runs in definition order, so it only covers the tests defined before it.
    The conftest-level check in this package closes the gap for the ones after.
    """
    root = validator.SOAK_RUNTIME_ROOT
    # Same scope as the conftest hook: only this gate's own run directories.
    # Anything else in build/ is not ours to fail on.
    leftover = sorted(p.name for p in root.glob("markdown-soak-*")) if root.is_dir() else []
    assert not leftover, {
        "leftover": leftover,
        "root": str(root),
    }


def test_prepare_runtime_refuses_to_start_when_an_ancestor_blocks(
    monkeypatch, tmp_path
):
    """A blocked ancestor must stop the run before NGINX answers 403.

    The chain walk deliberately stops below the checkout, so a 0700 directory
    above it is the gate's to report, not to widen. Removing the check lets the
    run proceed and fail later with a bare 403 that names nothing.
    """
    # _unreachable_ancestor walks resolved paths, so the stub has to match on
    # the resolved form; tmp_path itself is unresolved (/var -> /private/var).
    blocked = tmp_path.resolve()
    real_stat = type(tmp_path).stat

    def fake_stat(self, *args, **kwargs):
        if self == blocked:
            class Result:
                st_mode = 0o700

            return Result()
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "stat", fake_stat)
    monkeypatch.setattr(validator, "REPO_ROOT", (blocked / "checkout").resolve())
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT",
                        blocked / "checkout" / "build" / "soak-runtime")
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())
    # Nothing may be started: the refusal has to happen first.
    monkeypatch.setattr(
        validator.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("NGINX was started")),
    )

    with pytest.raises(ValueError) as excinfo:
        validator.prepare_runtime("http://127.0.0.1:19200",
                                  {"corpus": [{"id": "small"}]}, "module.so")

    assert "not traversable by other" in str(excinfo.value), {
        "msg": str(excinfo.value)[:200],
    }
    for created in (blocked / "checkout" / "build" / "soak-runtime").glob(
        "markdown-soak-*"
    ):
        validator._cleanup_runtime_directory(created)


def test_a_configured_runtime_directory_is_removed(monkeypatch, tmp_path):
    """SOAK_RUNTIME_DIR is ours to clean even though the name is not ours.

    Cleanup only deletes markdown-soak-* directories so it cannot remove
    something else, which left every run with a configured directory behind.
    """
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", tmp_path / "build" / "soak-runtime")
    monkeypatch.setattr(validator, "_OWNED_RUNTIME_DIR", None, raising=False)
    configured = tmp_path / "build" / "soak-runtime" / "ci-owned"
    monkeypatch.setenv("SOAK_RUNTIME_DIR", str(configured))

    runtime = validator._runtime_directory()
    assert runtime.is_dir(), {"msg": "the configured directory was not created"}

    validator._cleanup_runtime_directory(runtime)

    assert not runtime.exists(), {
        "leftover": str(runtime),
        "msg": "a configured SOAK_RUNTIME_DIR must be cleaned up",
    }


def test_a_pre_existing_non_empty_runtime_directory_is_refused(
    monkeypatch, tmp_path
):
    """Adopting someone else's directory would chmod it and then delete it.

    Verified against the previous behaviour: a pre-existing directory with a file
    in it was claimed as owned, chmodded to 0700 and removed with its contents.
    """
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", tmp_path / "build" / "soak-runtime")
    monkeypatch.setattr(validator, "_OWNED_RUNTIME_DIR", None, raising=False)
    monkeypatch.setattr(
        validator, "validate_write_path_within_root", lambda p, root, **k: Path(p)
    )
    existing = tmp_path / "build" / "soak-runtime" / "someone-elses-data"
    existing.mkdir(parents=True)
    keep = existing / "IMPORTANT.txt"
    keep.write_text("not ours", encoding="utf-8")
    monkeypatch.setenv("SOAK_RUNTIME_DIR", str(existing))

    with pytest.raises(ValueError) as excinfo:
        validator._runtime_directory()

    assert "not empty" in str(excinfo.value), {"msg": str(excinfo.value)[:140]}
    assert keep.exists(), {"msg": "the pre-existing file must survive"}


def test_an_empty_pre_existing_runtime_directory_is_reusable(monkeypatch, tmp_path):
    """An empty directory left by an interrupted run is ours to reuse."""
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", tmp_path / "build" / "soak-runtime")
    monkeypatch.setattr(validator, "_OWNED_RUNTIME_DIR", None, raising=False)
    monkeypatch.setattr(
        validator, "validate_write_path_within_root", lambda p, root, **k: Path(p)
    )
    existing = tmp_path / "build" / "soak-runtime" / "leftover"
    existing.mkdir(parents=True)
    monkeypatch.setenv("SOAK_RUNTIME_DIR", str(existing))

    runtime = validator._runtime_directory()

    assert runtime.is_dir(), {"msg": "an empty directory must be reusable"}
    validator._cleanup_runtime_directory(runtime)


def test_no_directory_is_created_when_the_worker_account_cannot_be_resolved(
    monkeypatch, tmp_path
):
    """Account resolution fails the run, so it must not leave a directory behind."""
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", tmp_path / "build" / "soak-runtime")
    monkeypatch.setattr(validator, "_OWNED_RUNTIME_DIR", None, raising=False)
    monkeypatch.delenv("SOAK_RUNTIME_DIR", raising=False)

    def no_account():
        raise RuntimeError("no unprivileged account resolves to a non-root uid")

    monkeypatch.setattr(validator, "nginx_worker_user", no_account)

    with pytest.raises(RuntimeError):
        validator._runtime_directory()

    assert not (tmp_path / "build" / "soak-runtime").exists(), {
        "msg": "a run that cannot start must not create the runtime tree",
    }


def test_cleanup_still_refuses_directories_it_did_not_create(monkeypatch, tmp_path):
    """Tracking the owned directory must not weaken the prefix guard."""
    root = tmp_path / "build" / "soak-runtime"
    root.mkdir(parents=True)
    stranger = root / "not-ours"
    stranger.mkdir()
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", root)
    monkeypatch.setattr(validator, "_OWNED_RUNTIME_DIR", None, raising=False)

    validator._cleanup_runtime_directory(stranger)

    assert stranger.is_dir(), {"msg": "cleanup must not delete an unrelated directory"}


def test_the_load_phase_verifies_the_worker_actually_dropped_privileges(
    monkeypatch, tmp_path
):
    """The session must call the identity check, not merely define it.

    The helper has its own unit tests, but those pass even when the call site is
    deleted -- the whole point of the check is that the soak refuses to run with
    root workers, so the wiring needs its own assertion.
    """
    checked: list[int] = []
    monkeypatch.setattr(
        validator, "assert_worker_dropped_privileges", lambda pid: checked.append(pid)
    )

    runtime = tmp_path / "markdown-soak-wiring"
    (runtime / "logs").mkdir(parents=True)

    class FakeNginx:
        pid = 4242

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            raise ProcessLookupError

    monkeypatch.setattr(
        validator, "prepare_runtime",
        lambda base, m, so: (runtime, {"small": "small.html"}, FakeNginx()),
    )
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", tmp_path / "build" / "soak-runtime")
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))
    monkeypatch.setattr(validator, "wait_for_ready", lambda url, **k: (True, ""))
    monkeypatch.setattr(validator, "find_worker_pid", lambda d: 4242)
    monkeypatch.setattr(validator, "run_load_loop",
                        lambda *a, **k: ([], {"small": []}))
    monkeypatch.setattr(validator, "measure_drain", lambda pid: (0, False, []))
    monkeypatch.setattr(validator, "read_module_peak_memory", lambda url: 1)
    monkeypatch.setattr(validator, "_stop_nginx", lambda n: None)

    validator._run_soak_session(
        "http://127.0.0.1:19200", {"duration_minutes": 1, "concurrency": 1}, ""
    )

    assert checked == [4242], {
        "checked": checked,
        "msg": "the soak must verify the worker's real identity before loading",
    }

    validator._cleanup_runtime_directory(runtime)


def test_a_blocked_ancestor_refusal_still_removes_the_runtime_directory(
    monkeypatch, tmp_path
):
    """The refusal must not leave behind the directory it is complaining about.

    The check runs inside the try for exactly this reason; raising before it
    would leak the per-run directory on every refused run.
    """
    # _unreachable_ancestor walks resolved paths, so the stub has to match on
    # the resolved form; tmp_path itself is unresolved (/var -> /private/var).
    blocked = tmp_path.resolve()
    real_stat = type(tmp_path).stat

    def fake_stat(self, *args, **kwargs):
        if self == blocked:
            class Result:
                st_mode = 0o700

            return Result()
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "stat", fake_stat)
    checkout = blocked / "checkout"
    runtime_root = checkout / "build" / "soak-runtime"
    monkeypatch.setattr(validator, "REPO_ROOT", checkout.resolve())
    monkeypatch.setattr(validator, "SOAK_RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(validator, "validate_write_path_within_root",
                        lambda p, root, **k: Path(p))
    monkeypatch.setattr(validator.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(validator.pwd, "getpwnam", lambda name: _FakePasswd())
    monkeypatch.setattr(
        validator.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("NGINX was started")),
    )

    with pytest.raises(ValueError):
        validator.prepare_runtime("http://127.0.0.1:19200",
                                  {"corpus": [{"id": "small"}]}, "module.so")

    assert not list(runtime_root.glob("markdown-soak-*")), {
        "leftover": [p.name for p in runtime_root.glob("markdown-soak-*")],
        "msg": "the refusal must clean up the directory it created",
    }


def test_cleanup_removes_a_prefixed_runtime_directory() -> None:
    """The leak fix needs its own regression assertion.

    Stubbing the cleanup out, or weakening the prefix/parent guard, left every
    other test green while the soak tests quietly accumulated directories under
    build/soak-runtime.
    """
    runtime_root = validator.SOAK_RUNTIME_ROOT
    runtime = runtime_root / "markdown-soak-cleanup-regression"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "marker").write_text("x", encoding="utf-8")

    validator._cleanup_runtime_directory(runtime)

    assert not runtime.exists(), "a prefixed runtime directory must be removed"


def test_cleanup_leaves_an_unprefixed_directory_intact() -> None:
    """The guard is a safety limit: it must not delete outside its own scope."""
    runtime_root = validator.SOAK_RUNTIME_ROOT
    other = runtime_root / "not-a-soak-runtime"
    other.mkdir(parents=True, exist_ok=True)
    try:
        validator._cleanup_runtime_directory(other)
        assert other.exists(), "a directory without the prefix must be left alone"
    finally:
        import shutil as _shutil

        _shutil.rmtree(other, ignore_errors=True)


def test_generated_config_states_the_parser_budget(tmp_path, monkeypatch):
    """The config must set `markdown_limits parser_budget`.

    The default is 32 MiB and one conversion of the 1 MiB `large` fixture
    allocates about 41 MB, so without the directive every request fails with
    ERROR_PARSE_BUDGET_EXCEEDED (error_code 11, category resource_limit) and
    the soak reports a resource limit the module never imposed.
    """
    monkeypatch.setattr(
        validator, "_runtime_directory", lambda: tmp_path
    )
    runtime = validator.REPO_ROOT / "build" / "soak-runtime" / "markdown-soak-limits-selftest"
    runtime.mkdir(parents=True, exist_ok=True)
    try:
        validator.write_nginx_conf(
            runtime,
            validator.SOAK_PORT,
            str(runtime / "html"),
            None,
            parser_budget_bytes=validator.SOAK_PARSER_BUDGET_BYTES,
        )
        conf = (runtime / "nginx.conf").read_text(encoding="utf-8")
    finally:
        validator._cleanup_runtime_directory(runtime)

    assert (
        f"markdown_limits parser_budget={validator.SOAK_PARSER_BUDGET_BYTES};" in conf
    ), conf


def test_an_unprobeable_address_does_not_hide_a_conflict_on_the_other(monkeypatch):
    """A host that cannot bind loopback must not mask a real holder.

    Giving up on the first non-EADDRINUSE error would report "no conflict" for a
    port the wildcard bind then cannot take.
    """
    import socket

    # A real holder on the wildcard address, so the second probe has something
    # genuine to collide with rather than a fabricated error.
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with holder:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("0.0.0.0", 0))
        holder.listen(1)
        port = holder.getsockname()[1]

        real_bind = socket.socket.bind

        def bind(self, address):
            if address[0] == "127.0.0.1":
                raise OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
            return real_bind(self, address)

        monkeypatch.setattr(socket.socket, "bind", bind)

        assert validator._port_holder(port) is not None, {
            "msg": "an unprobeable loopback must not hide a wildcard conflict",
        }


def test_peak_memory_holds_the_peak_to_the_smallest_ceiling():
    """A run-wide peak has no scenario attribution, so the smallest budget wins.

    Taking the largest would let a peak that exceeds the tightest scenario's
    ceiling pass as long as some other scenario allowed more.
    """
    manifest = {
        "corpus": [
            {"id": "small", "conversion_memory_bytes": 32 * 1024 * 1024},
            {"id": "large", "conversion_memory_bytes": 96 * 1024 * 1024},
        ]
    }
    peak = 64 * 1024 * 1024  # fits the large budget, exceeds the small one

    issue = validator._peak_memory_issue(
        {"module_managed_peak_observed": True, "per_request_peak_bytes": peak},
        manifest,
    )

    assert issue is not None, {
        "peak": peak,
        "msg": "a peak above the smallest ceiling must fail the run",
    }
    assert "32" in issue or "33554432" in issue, {"issue": issue}


def test_peak_memory_passes_when_the_peak_fits_every_ceiling():
    """The conservative rule must not reject a run that fits all budgets."""
    manifest = {
        "corpus": [
            {"id": "small", "conversion_memory_bytes": 32 * 1024 * 1024},
            {"id": "large", "conversion_memory_bytes": 96 * 1024 * 1024},
        ]
    }
    peak = 16 * 1024 * 1024

    assert (
        validator._peak_memory_issue(
            {"module_managed_peak_observed": True, "per_request_peak_bytes": peak},
            manifest,
        )
        is None
    ), {"msg": "a peak within every ceiling must pass"}


def test_parser_budget_clears_the_measurement_with_headroom():
    """41 MB was measured; the default 32 MiB is what failed the soak.

    The budget has to clear the measurement by enough to absorb allocator
    variation. A bound of "greater than 41 MiB" alone would accept 42 MiB, which
    is inside the noise the headroom exists for.
    """
    measured = 41 * 1024 * 1024
    assert validator.SOAK_PARSER_BUDGET_BYTES > measured, (
        "the budget must clear the observed parser allocation"
    )
    headroom = validator.SOAK_PARSER_BUDGET_BYTES / measured
    assert headroom >= 1.25, {
        "headroom": round(headroom, 3),
        "budget_mib": validator.SOAK_PARSER_BUDGET_BYTES // (1024 * 1024),
        "msg": "the budget must leave at least 25% headroom over the measurement",
    }


def test_parser_budget_does_not_relax_the_evidence_ceiling():
    """Raising the parser budget must not widen what the evidence is held to.

    The manifest's conversion_memory ceilings are what the per-request peak is
    compared against, so they stay independent of the parser budget.
    """
    record = {
        "status": "pass",
        "module_managed_peak_observed": True,
        "per_request_peak_bytes": 40 * 1024 * 1024,
        "rss_samples": [1, 2, 3],
        "monotonic_growth_after_drain": False,
    }
    manifest = {
        "duration_minutes": 30,
        "concurrency": 16,
        "corpus": [{"id": "small", "conversion_memory_bytes": 33_554_432}],
        "scenario_refs": ["release/scope/short-soak-scope.json"],
    }
    issue = validator._peak_memory_issue(record, manifest)
    assert issue is not None, "a peak over the ceiling must be reported"
    assert "ceiling" in issue, issue


def test_prepare_runtime_wires_the_parser_budget_into_the_config(
    tmp_path, monkeypatch
):
    """The budget must reach the written config through prepare_runtime.

    Testing `write_nginx_conf` directly misses this: the other prepare_runtime
    tests monkeypatch it away, so dropping the keyword argument at the call site
    reverted every request to the 32 MiB default -- the exact failure this
    change fixes -- while all tests stayed green.
    """
    runtime = validator.REPO_ROOT / "build" / "soak-runtime" / "markdown-soak-wiring-selftest"
    runtime.mkdir(parents=True, exist_ok=True)

    class FakeNginx:
        pid = 4242

        def __init__(self, *a, **kw):
            pass

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            pass

    monkeypatch.setattr(validator, "_runtime_directory", lambda: runtime)
    monkeypatch.setattr(validator, "build_corpus", lambda *a: {"small": "small.html"})
    # Port occupancy is not what this test is about.
    monkeypatch.setattr(validator, "_port_holder", lambda port: None)
    monkeypatch.setattr(validator.subprocess, "Popen", FakeNginx)
    monkeypatch.setattr(
        validator, "_validated_nginx_binary", lambda: Path("/bin/true")
    )
    try:
        validator.prepare_runtime(
            f"http://127.0.0.1:{validator.SOAK_PORT}", {"corpus": []}, ""
        )
        conf = (runtime / "nginx.conf").read_text(encoding="utf-8")
    finally:
        validator._cleanup_runtime_directory(runtime)

    assert f"markdown_limits parser_budget={validator.SOAK_PARSER_BUDGET_BYTES};" in (
        conf
    ), conf


def test_startup_failure_reason_reports_the_nginx_error(tmp_path):
    """The reason must name the actual NGINX error, not just 'not ready'."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "startup.log").write_text(
        "nginx: [emerg] dlopen() failed while loading "
        "/usr/lib/nginx/modules/x.so (cannot open shared object file)\n",
        encoding="utf-8",
    )

    detail = validator._startup_failure_reason(tmp_path)

    assert "dlopen" in detail, detail
    assert "[emerg]" in detail, detail


def test_startup_failure_reason_reads_the_error_log_too(tmp_path):
    """A runtime failure only reaches error.log, so it must be consulted.

    NGINX writes CLI failures (the `nginx: [emerg] ...` banner) to the captured
    output, but runtime failures to its own error log. Dropping the second file
    leaves every other test green while the reason goes empty.
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "startup.log").write_text("all fine\n", encoding="utf-8")
    (logs / "error.log").write_text(
        "2026/10/03 12:00:00 [alert] worker process exited on signal 11\n",
        encoding="utf-8",
    )

    detail = validator._startup_failure_reason(tmp_path)

    assert "alert" in detail, detail
    assert "signal 11" in detail, detail


def test_startup_failure_reason_surfaces_crit_and_error_levels(tmp_path):
    """[crit] and [error] are just as diagnostic as [emerg]/[alert].

    A worker that dies with an error-level message must not produce an empty
    reason, which would read as "NGINX logged nothing".
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "startup.log").write_text("all fine\n", encoding="utf-8")
    (logs / "error.log").write_text(
        "2026/10/04 12:00:00 [crit] 1#1: *1 open() \"/etc/nginx/x\" failed (2: No such file)\n"
        "2026/10/04 12:00:01 [error] 1#1: *2 rewrite cycle aborted\n",
        encoding="utf-8",
    )

    detail = validator._startup_failure_reason(tmp_path)

    assert "crit" in detail, detail
    assert "rewrite cycle aborted" in detail, detail


def test_startup_failure_reason_caps_a_single_line(tmp_path):
    """One runaway log line must not be pasted whole into the failure message."""
    logs = tmp_path / "logs"
    logs.mkdir()
    noise = "x" * (validator._STARTUP_LOG_MAX_BYTES + 5000)
    (logs / "startup.log").write_text(f"nginx: [emerg] {noise}\n", encoding="utf-8")

    detail = validator._startup_failure_reason(tmp_path)

    assert len(detail) <= validator._STARTUP_LOG_MAX_BYTES, {
        "len": len(detail),
        "cap": validator._STARTUP_LOG_MAX_BYTES,
    }


def test_startup_failure_reason_caps_the_number_of_lines(tmp_path):
    """A log with many errors must not flood the failure message."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "startup.log").write_text(
        "".join(f"nginx: [emerg] error {i}\n" for i in range(50)),
        encoding="utf-8",
    )

    detail = validator._startup_failure_reason(tmp_path)

    assert detail.count("nginx:") == 3, {
        "detail": detail,
        "msg": "at most three reasons belong in the failure message",
    }


def test_ready_failure_reports_the_status_and_the_nginx_log(tmp_path, monkeypatch):
    """The readiness error must name the HTTP status *and* what NGINX logged.

    The three pieces are assembled in one place: the generic reason, the poll's
    last error, and the log detail. The individual helpers are covered, so only
    this assembly can be verified -- dropping a piece or changing the join left
    the suite green.
    """
    # tmp_path is removed by pytest; a hardcoded directory would leak.
    runtime_dir = tmp_path / "markdown-soak-ready-error"
    logs = runtime_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "error.log").write_text(
        "2026/10/03 12:00:00 [emerg] bind() to 0.0.0.0:19200 failed\n",
        encoding="utf-8",
    )

    class FakeNginx:
        pid = 4242

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            raise ProcessLookupError

    monkeypatch.setattr(validator, "prepare_runtime",
                        lambda base, m, so: (runtime_dir, {"small": "f"}, FakeNginx()))
    monkeypatch.setattr(validator, "wait_for_ready",
                        lambda url: (False, "HTTP 502"))
    monkeypatch.setattr(validator, "_stop_nginx", lambda nginx: None)

    result = validator._run_soak_session("http://127.0.0.1:19200", {"concurrency": 1}, "x.so")

    error = result.get("ready_error") or ""
    assert "nginx did not become ready" in error, result
    assert "HTTP 502" in error, {"msg": "the poll's last error must survive", "error": error}
    assert "19200" in error, {"msg": "NGINX's own log line must survive", "error": error}
    assert error.count(": ") >= 2, {"msg": "the pieces must be joined", "error": error}


def test_ready_failure_omits_absent_pieces_without_trailing_separator(
    tmp_path, monkeypatch
):
    """With nothing logged, the message must not trail an empty separator."""
    runtime_dir = tmp_path / "markdown-soak-ready-nodetail"
    (runtime_dir / "logs").mkdir(parents=True)
    (runtime_dir / "logs" / "startup.log").write_text("all fine\n", encoding="utf-8")

    class FakeNginx:
        pid = 4242

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            raise ProcessLookupError

    monkeypatch.setattr(validator, "prepare_runtime",
                        lambda base, m, so: (runtime_dir, {"small": "f"}, FakeNginx()))
    monkeypatch.setattr(validator, "wait_for_ready", lambda url: (False, ""))
    monkeypatch.setattr(validator, "_stop_nginx", lambda nginx: None)

    result = validator._run_soak_session("http://127.0.0.1:19200", {"concurrency": 1}, "x.so")

    error = result.get("ready_error") or ""
    assert error == "nginx did not become ready", {"error": error}
    assert not error.endswith(": "), {"error": error}


def test_wait_for_ready_reports_a_non_200_status(monkeypatch):
    """A responding-but-refusing NGINX must surface its status code."""
    import urllib.request as urlreq

    class FakeResponse:
        status = 502

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urlreq, "urlopen", lambda *a, **k: FakeResponse())

    # A real scenario filename: the URL validator rejects anything else. The
    # timeout stays small so the poll loop exits promptly; do not freeze the
    # clock, or the deadline never passes and the loop spins forever.
    ready, last_error = validator.wait_for_ready(
        f"http://127.0.0.1:{validator.SOAK_PORT}/{validator.SOAK_SCENARIO_FILES['small']}",
        timeout=1,
    )

    assert ready is False, {"ready": ready}
    assert "502" in last_error, {"last_error": last_error}


def test_startup_failure_reason_is_empty_when_nothing_was_logged(tmp_path):
    """Silence is itself a signal, but it must not invent a reason."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "startup.log").write_text("all fine\n", encoding="utf-8")

    assert validator._startup_failure_reason(tmp_path) == ""


def test_wait_for_ready_returns_the_last_error():
    """A refusal must name the error instead of collapsing to False."""
    # Mock the connection so the outcome does not depend on whether something
    # happens to be listening on SOAK_PORT while this test runs.
    import urllib.error
    import urllib.request

    def refuse(*a, **kw):
        raise urllib.error.URLError("mocked refusal")

    fixture = next(iter(validator.SOAK_SCENARIO_FILES.values()))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(urllib.request, "urlopen", refuse)
        ready, error = validator.wait_for_ready(
            f"http://127.0.0.1:{validator.SOAK_PORT}/{fixture}", timeout=1
        )

    assert ready is False
    assert "mocked refusal" in error, error


def test_valid_soak_record_passes() -> None:
    assert _run_fixture("soak-qualification-valid.json") == 0


def test_fixture_skip_record_is_accepted_before_threshold_checks(
    tmp_path: Path,
) -> None:
    """An explicitly skipped fixture does not need fabricated soak evidence."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"
        )
    )
    record["candidate_sha"] = manifest["candidate_sha"]
    record["status"] = "skip"
    record["skip_reason"] = "module binary unavailable in fixture environment"

    manifest_path = tmp_path / "manifest.json"
    record_path = tmp_path / "record.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    record_path.write_text(json.dumps(record), encoding="utf-8")

    assert validator.main(
        [
            "--mode",
            "fixture",
            "--manifest",
            str(manifest_path),
            "--record-input",
            str(record_path),
        ]
    ) == 0


def test_below_threshold_fails() -> None:
    assert _run_fixture("soak-qualification-below-threshold.json") == 1


def test_malformed_fails() -> None:
    with pytest.raises(SystemExit, match="malformed|missing-observation"):
        _run_fixture("soak-qualification-schema-invalid.json")


def test_blocking_pending_fails() -> None:
    assert _run_fixture("soak-qualification-blocking-pending.json") == 1


def test_stale_digest_fails() -> None:
    assert _run_fixture("soak-qualification-stale-digest.json") == 1


def test_missing_observation_fails() -> None:
    with pytest.raises(SystemExit, match="missing-observation"):
        _run_fixture("soak-qualification-missing-observation.json")


def test_manifest_contract_valid() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["duration_minutes"] == 30
    assert manifest["concurrency"] == 16
    assert {entry["id"] for entry in manifest["corpus"]} == {
        "small", "medium", "large"
    }


def test_real_soak_requires_module_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real mode must not run a stock NGINX without the module."""
    args = type("Args", (), {"allow_skip_soak": False})()
    manifest = {"candidate_sha": "a" * 40}
    monkeypatch.setattr(validator, "_validated_nginx_binary", lambda: Path("nginx"))
    monkeypatch.setattr(validator, "_validated_module", lambda: None)

    assert validator.handle_missing_nginx(args, manifest) == 1


def test_allowed_soak_skip_writes_a_structurally_valid_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {"candidate_sha": "a" * 40, "concurrency": 4}
    args = type("Args", (), {
        "allow_skip_soak": True,
        "output": None,
        "record": "artifacts/release/0.9.2/soak-record.json",
    })()
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "_validated_nginx_binary", lambda: None)
    monkeypatch.setattr(validator, "_validated_module", lambda: None)

    assert validator.handle_missing_nginx(args, manifest) == 0
    record = json.loads(
        (tmp_path / "artifacts" / "release" / "0.9.2" /
         "soak-record.json").read_text(encoding="utf-8")
    )
    validator.validate_record_structure(record)
    assert record["status"] == "skip"


def test_manifest_rejects_path_like_scenario_id(tmp_path: Path) -> None:
    """Scenario IDs must remain within the fixed local URL allowlist."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["corpus"][0]["id"] = "../escape"
    staged = tmp_path / "manifest.json"
    staged.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SystemExit, match="must be one of"):
        validator.load_manifest(str(staged))


def test_local_url_rejects_traversal() -> None:
    """The HTTP client must not accept a traversal URL path."""
    with pytest.raises(ValueError, match="Invalid"):
        validator._validated_local_url("http://127.0.0.1:19200/../etc/passwd")


def test_worker_child_skips_malformed_pid() -> None:
    """A malformed process-table row must not abort worker discovery."""
    output = "not-a-pid 42 nginx\n1234 42 nginx: worker process\n"
    assert validator._find_worker_child(output, 42) == 1234


def test_worker_child_returns_not_found_for_only_malformed_rows() -> None:
    """Malformed matching rows are not valid worker PIDs."""
    assert validator._find_worker_child("not-a-pid 42 nginx\n", 42) == -1


def test_worker_child_ignores_unrelated_child_processes() -> None:
    """A same-parent helper process must not become RSS evidence."""
    assert validator._find_worker_child("1234 42 helper\n", 42) == -1


def test_rss_evidence_requires_samples_and_nonnegative_values() -> None:
    """A pass record cannot omit or sentinel-fill worker RSS evidence."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"
        )
    )

    record["rss_time_series"] = []
    with pytest.raises(
        validator.SoakQualificationValidationError, match="insufficient-data"
    ):
        validator.validate_soak_outcome(record, manifest)

    record["rss_time_series"] = [[0.0, 100], [1.0, 101], [2.0, -1]]
    with pytest.raises(
        validator.SoakQualificationValidationError, match="insufficient-data"
    ):
        validator.validate_soak_outcome(record, manifest)

    record["rss_time_series"] = [[0.0, 100], [1.0, 101], [2.0, 102]]
    record["worker_rss_drain_samples"] = []
    with pytest.raises(
        validator.SoakQualificationValidationError, match="insufficient-data"
    ):
        validator.validate_soak_outcome(record, manifest)


def test_peak_memory_metric_parser_requires_positive_gauge() -> None:
    body = (
        "# TYPE nginx_markdown_conversion_peak_memory_bytes gauge\n"
        "nginx_markdown_conversion_peak_memory_bytes 65536\n"
    )
    assert validator._parse_peak_memory_metric(body) == 65536
    assert validator._parse_peak_memory_metric(
        "nginx_markdown_conversion_peak_memory_bytes 0\n"
    ) is None
    assert validator._parse_peak_memory_metric("other_metric 65536\n") is None


def test_runtime_directory_rejects_external_override(
    tmp_path: Path, monkeypatch
) -> None:
    """SOAK_RUNTIME_DIR must stay inside the repository build tree."""
    monkeypatch.setenv("SOAK_RUNTIME_DIR", str(tmp_path / "runtime"))

    with pytest.raises(ValueError, match="escapes root"):
        validator._runtime_directory()


def test_soak_nginx_runs_in_foreground_for_reliable_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The soak master must own the launcher process and its shutdown."""
    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    runtime_dir = tmp_path / "runtime"
    document_root = runtime_dir / "html"
    document_root.mkdir(parents=True)

    validator.write_nginx_conf(runtime_dir, 19000, str(document_root), None)
    config = (runtime_dir / "nginx.conf").read_text(encoding="utf-8")

    assert "daemon off;" in config
    assert f"pid {runtime_dir}/nginx.pid;" in config
    assert f"access_log {runtime_dir}/logs/access.log;" in config


def test_record_output_path_rejects_external_override(tmp_path: Path) -> None:
    """Qualification records must stay under the generated output root."""
    args = type("Args", (), {
        "output": str(tmp_path / "soak-record.json"),
        "record": "unused.json",
    })()

    with pytest.raises(ValueError, match="Output path"):
        validator._write_record({}, args)


def test_negative_error_rate_is_not_treated_as_zero() -> None:
    """Non-zero floating-point error rates must fail the fixture gate."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"
        )
    )
    record["per_scenario"][0]["error_rate"] = -0.1

    with pytest.raises(
        validator.SoakQualificationValidationError, match="error_rate"
    ):
        validator.validate_soak_outcome(record, manifest)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "0"])
def test_non_finite_error_rate_is_rejected(value) -> None:
    """NaN, infinity, and non-numeric error rates cannot qualify."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"))
    record["per_scenario"][0]["error_rate"] = value

    with pytest.raises(
        validator.SoakQualificationValidationError, match="error_rate"
    ):
        validator.validate_soak_outcome(record, manifest)


def test_parse_ab_report_uses_requests_per_second() -> None:
    """rps must come from ab's request-rate line, not transfer rate."""
    report = validator.parse_ab_report(
        "Transfer rate: 9000.00 [Kbytes/sec] received\n"
        "Requests per second:    42.50 [#/sec] (mean)\n")

    assert report["rps"] == 42.5


def test_missing_per_request_peak_is_insufficient_data() -> None:
    """A pass record must include an observed module-managed peak."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record = json.loads(
        (FIXTURE_DIR / "soak-qualification-valid.json").read_text(
            encoding="utf-8"
        )
    )
    record["module_managed_peak_observed"] = False
    record["per_request_peak_bytes"] = None

    with pytest.raises(
        validator.SoakQualificationValidationError, match="insufficient-data"
    ):
        validator.validate_soak_outcome(record, manifest)


def test_real_mode_missing_peak_is_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """Real mode must FAIL when the module-managed per-request peak was
    not observed: the gauge is a run-wide high-water mark covering both
    streaming and full-buffer conversions, so its absence means no
    conversion completed."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record_path = (
        tmp_path / "artifacts" / "release" / "0.9.2" / "soak-record.json"
    )

    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "load_manifest", lambda path: manifest)
    monkeypatch.setattr(validator, "handle_missing_nginx", lambda args, data: None)
    monkeypatch.setattr(
        validator,
        "_run_soak_session",
        lambda base_url, data, module_so: {
            "started": 0,
            "ended": 1800,
            "rss_series": [[0.0, 100.0], [1.0, 101.0], [2.0, 102.0]],
            "scenario_metrics": {
                "small": [
                    {
                        "completed_requests": 100,
                        "failed_requests": 0,
                        "error_rate": 0.0,
                        "p50_ms": 1.0,
                        "p99_ms": 2.0,
                        "rps": 10.0,
                    }
                ],
                "medium": [
                    {
                        "completed_requests": 100,
                        "failed_requests": 0,
                        "error_rate": 0.0,
                        "p50_ms": 1.0,
                        "p99_ms": 2.0,
                        "rps": 10.0,
                    }
                ],
                "large": [
                    {
                        "completed_requests": 100,
                        "failed_requests": 0,
                        "error_rate": 0.0,
                        "p50_ms": 1.0,
                        "p99_ms": 2.0,
                        "rps": 10.0,
                    }
                ],
            },
            "drain_delta": 0,
            "monotonic": False,
            "drain_samples": [100, 100, 100],
            "peak_memory_bytes": None,
            "ready_error": None,
        },
    )

    args = type(
        "Args",
        (),
        {
            "manifest": str(MANIFEST),
            "record": "artifacts/release/0.9.2/soak-record.json",
            "output": None,
            "allow_skip_soak": False,
        },
    )()

    assert validator.real_main(args) == 1
    saved = json.loads(record_path.read_text(encoding="utf-8"))
    assert saved["status"] == "fail"
    assert any("insufficient-data" in error for error in saved["errors"])


def test_real_mode_cannot_pass_with_missing_worker_rss_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    """A valid module peak cannot mask a missing worker RSS observation."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    record_path = (
        tmp_path / "artifacts" / "release" / "0.9.2" / "soak-record.json"
    )
    runtime_dir = tmp_path / "runtime"

    class FakeNginx:
        pid = 4242

        def terminate(self) -> None:
            pass

        def wait(self, timeout: int) -> None:
            pass

        def send_signal(self, sig) -> None:
            pass

    monkeypatch.setattr(validator, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(validator, "load_manifest", lambda path: manifest)
    monkeypatch.setattr(validator, "handle_missing_nginx", lambda args, data: None)
    monkeypatch.setattr(
        validator,
        "prepare_runtime",
        lambda base_url, data, module_so: (
            runtime_dir,
            {"small": "small.html"},
            FakeNginx(),
        ),
    )
    monkeypatch.setattr(
        validator, "wait_for_ready", lambda url: (True, "")
    )
    monkeypatch.setattr(validator, "find_worker_pid", lambda path: -1)
    monkeypatch.setattr(
        validator,
        "run_load_loop",
        lambda corpus, worker_pid, duration, started, concurrency, runtime: (
            [], {}
        ),
    )
    monkeypatch.setattr(
        validator,
        "measure_drain",
        lambda worker_pid: (None, False, []),
    )
    monkeypatch.setattr(validator, "read_module_peak_memory", lambda base_url: 65536)

    args = type(
        "Args",
        (),
        {
            "manifest": str(MANIFEST),
            "record": "artifacts/release/0.9.2/soak-record.json",
            "output": None,
            "allow_skip_soak": False,
        },
    )()

    assert validator.real_main(args) == 1
    saved = json.loads(record_path.read_text(encoding="utf-8"))
    assert saved["status"] == "fail"
    assert any("worker RSS" in error for error in saved["errors"])


@pytest.mark.parametrize("sample", [None, 0, 65536])
def test_run_peak_gauge_semantics(sample):
    """The gauge is a run-wide high-water mark: a positive sample certifies
    an observed peak, while None/zero means no conversion completed."""
    session = {
        "started": 0,
        "ended": 1800,
        "rss_series": [],
        "drain_delta": 0,
        "drain_samples": [100, 100, 100],
        "monotonic": False,
        "peak_memory_bytes": sample,
    }
    manifest = {
        "candidate_sha": "a" * 40,
        "concurrency": 16,
        "corpus": [],
    }
    record = validator._build_soak_record(manifest, 1800, [], session)
    assert record["last_streaming_peak_estimate_bytes"] == sample
    if sample is None or sample == 0:
        # Zero, like None, is missing evidence: production treats
        # peak <= 0 as unobserved, so the record must carry the zero
        # gauge through unchanged and mark the peak unobserved.
        assert record["module_managed_peak_observed"] is False
        assert record["per_request_peak_bytes"] is None
        assert validator._peak_memory_issue(record, manifest) is not None
    else:
        assert record["module_managed_peak_observed"] is True
        assert record["per_request_peak_bytes"] == sample
        # Positive sample with no corpus ceilings -> missing-ceiling issue,
        # not missing-observation.
        assert validator._peak_memory_issue(record, manifest) == (
            "insufficient-data: scenario memory ceiling is missing"
        )


def test_load_generator_requests_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sustained load must negotiate conversion rather than HTML bypass."""
    monkeypatch.setattr(validator, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(validator, 'resolve_approved_executable', lambda name: '/usr/bin/ab')
    captured = []

    def run(command, **kwargs):
        captured.extend(command)
        return validator.subprocess.CompletedProcess(command, 0, 'Complete requests: 10\nFailed requests: 0\n', '')

    monkeypatch.setattr(validator.subprocess, 'run', run)
    result = validator.run_ab_chunk(f'http://127.0.0.1:{validator.SOAK_PORT}/small.html', 16, 1, tmp_path)
    assert captured[captured.index('-H') + 1] == 'Accept: text/markdown'
    assert result['completed_requests'] == 10


def test_peak_memory_request_selects_prometheus(monkeypatch: pytest.MonkeyPatch) -> None:
    """The peak parser requests the representation it understands."""
    import io

    def open_metrics(request, timeout):
        assert request.get_header('Accept') == 'text/plain'
        assert timeout == 5
        response = io.BytesIO(b'nginx_markdown_conversion_peak_memory_bytes 65536\n')
        response.status = 200
        return response

    monkeypatch.setattr(validator.urllib.request, 'urlopen', open_metrics)
    assert validator.read_module_peak_memory(f'http://127.0.0.1:{validator.SOAK_PORT}') == 65536


@pytest.mark.parametrize('peak,passes', [(32, True), (33, False), (96, False)])
def test_global_peak_respects_smallest_scenario_budget(peak: int, passes: bool) -> None:
    record = {'module_managed_peak_observed': True, 'per_request_peak_bytes': peak}
    manifest = {'corpus': [{'conversion_memory_bytes': 32}, {'conversion_memory_bytes': 96}]}
    assert (validator._peak_memory_issue(record, manifest) is None) is passes


@pytest.mark.parametrize('budget', [None, True, 0, -1, '32'])
def test_peak_check_rejects_any_invalid_scenario_budget(budget: object) -> None:
    record = {'module_managed_peak_observed': True, 'per_request_peak_bytes': 1}
    manifest = {'corpus': [{'conversion_memory_bytes': budget}, {'conversion_memory_bytes': 96}]}
    assert validator._peak_memory_issue(record, manifest) is not None


def test_soak_failures_missing_peak_is_blocking() -> None:
    """A missing module-managed peak is a blocking failure; an observed
    peak above the ceiling stays blocking."""
    record = {
        "per_scenario": [{"error_rate": 0.0}],
        "monotonic_growth_after_drain": False,
        "rss_time_series": [[0.0, 100.0], [1.0, 101.0], [2.0, 102.0]],
        "worker_rss_drain_delta_kb": 0,
        "worker_rss_drain_samples": [100, 100, 100],
        "module_managed_peak_observed": False,
        "per_request_peak_bytes": None,
    }
    manifest = {
        "duration_minutes": 30,
        "corpus": [{"conversion_memory_bytes": 32}],
    }
    failures = validator._soak_failures(record, manifest, 1800, None)
    assert any("insufficient-data" in f for f in failures)

    record["module_managed_peak_observed"] = True
    record["per_request_peak_bytes"] = 2**40
    failures = validator._soak_failures(record, manifest, 1800, None)
    assert any("below-threshold" in f for f in failures)
