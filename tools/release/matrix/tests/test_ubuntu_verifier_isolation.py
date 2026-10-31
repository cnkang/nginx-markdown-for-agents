"""Exercise production shell ownership/cleanup paths with hermetic processes.

NGINX and curl doubles isolate process ownership from HTTP/module semantics.
Real Ubuntu build/load checks remain required separately. OS and executable
boundaries point to doubles. Former host paths map to private sentinels, so a
regression cannot damage the machine that runs this test.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "packaging/scripts/verify-ubuntu-native-module.sh"


def _wait_for_file(path):
    """Wait for a bounded process handoff and return its recorded value."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            content = path.read_text()
            if content:
                return content
        time.sleep(.01)
    raise AssertionError(f'process did not populate {path}')


@contextmanager
def _owned_verifier(command, env, root):
    """Keep a live session leader until all test-owned children are stopped."""
    supervisor = '''
import os, signal, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen(sys.argv[2:])
def forward(signum, _):
    try:
        child.send_signal(signal.SIGKILL if signum == signal.SIGUSR1 else signum)
    except ProcessLookupError:
        pass
signal.signal(signal.SIGTERM, forward)
signal.signal(signal.SIGINT, forward)
signal.signal(signal.SIGUSR1, forward)
Path(sys.argv[1]).write_text(str(child.wait()))
while True:
    time.sleep(1)
'''
    leader = subprocess.Popen([sys.executable, '-c', supervisor,
                               str(root / 'verifier.exit'), *command], env=env,
                              start_new_session=True, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    try:
        yield leader
    finally:
        # The supervisor remains alive after the verifier exits, so this PGID
        # cannot be reused between these signals and the final wait.
        if leader.poll() is None:
            os.killpg(leader.pid, signal.SIGTERM)
            deadline = time.monotonic() + 2
            while (root / 'child.pid').exists() and time.monotonic() < deadline:
                stopped = root / 'child.exit'
                if stopped.exists() and stopped.read_text():
                    break
                time.sleep(.01)
            os.killpg(leader.pid, signal.SIGKILL)
        leader.wait(timeout=10)


@pytest.fixture
def sandbox(tmp_path):
    """Provide private host sentinels and executable command doubles."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    release = tmp_path / "os-release"
    release.write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
    script = tmp_path / "verify.sh"
    script.write_text(SOURCE.read_text().replace("/etc/os-release", str(release))
                      .replace('/usr/sbin/nginx', str(bindir / 'nginx'))
                      .replace('/etc/nginx/sites-enabled/default', str(tmp_path / 'default'))
                      .replace('/etc/nginx/sites-enabled', str(tmp_path / 'sites-enabled'))
                      .replace('/etc/nginx/sites-available', str(tmp_path / 'sites-available'))
                      .replace('/etc/nginx/modules-enabled', str(tmp_path / 'modules-enabled'))
                      .replace('/usr/lib/nginx/modules', str(tmp_path))
                      .replace('/var/www/markdown-verify', str(tmp_path / 'document-root')))
    module = tmp_path / "ngx_http_markdown_filter_module.so"
    module.write_bytes(b"existing host module")
    incompatible = tmp_path / "negative.so"
    incompatible.write_bytes(b"negative control")
    default = tmp_path / "default"
    default.symlink_to("custom-site")
    site = tmp_path / "custom-site"
    site.write_text("custom host configuration\n")
    enabled = tmp_path / 'modules-enabled'
    enabled.mkdir()
    (enabled / '50-mod-markdown-verify.conf').write_text('# existing module config\n')
    document_root = tmp_path / 'document-root'
    document_root.mkdir()
    (document_root / 'sentinel').write_text('existing web root\n')
    log = tmp_path / "calls"
    uname = bindir / "uname"
    uname.write_text('#!/bin/sh\nprintf "aarch64\\n"\n')
    uname.chmod(0o755)
    nginx = bindir / "nginx"
    nginx.write_text(f"#!{sys.executable}\n" + '''
import json, os, signal, sys, time
from pathlib import Path
args = sys.argv[1:]
if args == ['-v']:
    print('nginx version: nginx/1.24.0 (Ubuntu)')
    sys.exit(0)
with open(os.environ['CALL_LOG'], 'a') as out:
    out.write(json.dumps(args) + '\\n')
assert '-p' in args and '-c' in args and '-e' in args
assert '-s' not in args
conf = Path(args[args.index('-c') + 1])
assert str(conf).startswith(os.environ['TMPDIR'])
if '-t' in args:
    if conf.name == 'incompatible.conf':
        module = conf.read_text().split('"')[1]
        print(os.environ.get('NEGATIVE_ERROR', f'module "{module}" is not binary compatible'), file=sys.stderr)
        sys.exit(1)
    sys.exit(1 if os.environ.get('MODE') == 'config-fail' else 0)
if os.environ.get('MODE') == 'start-fail':
    sys.exit(1)
signal.signal(signal.SIGHUP, lambda *_: None)
def stop(*_):
    Path(os.environ['CHILD_EXIT']).write_text('stopped')
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
Path(os.environ['CHILD_PID']).write_text(str(os.getpid()))
while True:
    time.sleep(.05)
''')
    nginx.chmod(0o755)
    dpkg = bindir / 'dpkg-query'
    dpkg.write_text(f"#!{sys.executable}\n" + '''
import os, sys
if sys.argv[1] == '-S':
    print('nginx: ' + sys.argv[2])
else:
    print(os.environ.get('NGINX_REVISION', '1.24.0-2ubuntu7.19'))
''')
    dpkg.chmod(0o755)
    curl = bindir / "curl"
    curl.write_text(f"#!{sys.executable}\n" + '''
import os, sys
from pathlib import Path
args = sys.argv[1:]
body = '<h1>Ubuntu native smoke</h1>' if 'Accept: text/html' in args else '# Ubuntu native smoke'
if os.environ.get('MODE') == 'http-fail':
    body = 'wrong representation'
token = next(Path(os.environ['TMPDIR']).glob('verify-ubuntu-native.*')).name
headers = f'Content-Type: text/markdown\\nVary: Accept\\nX-Markdown-Verify: {token}\\n'
for flag, content in [('-o', body), ('-D', headers)]:
    if flag in args:
        path = args[args.index(flag) + 1]
        if path != '/dev/null':
            Path(path).write_text(content)
if '-I' in args and '-o' in args:
    Path(args[args.index('-o') + 1]).write_text('Content-Type: text/markdown\\n')
if '-w' in args:
    print('200', end='')
''')
    curl.chmod(0o755)
    bash_env = tmp_path / 'bash-env'
    bash_env.write_text('kill() { printf "%s\\n" "$*" >>"$KILL_LOG"; builtin kill "$@"; }\n')
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", TMPDIR=str(work),
               CALL_LOG=str(log), CHILD_PID=str(tmp_path / "child.pid"),
               CHILD_EXIT=str(tmp_path / 'child.exit'),
               BASH_ENV=str(bash_env), KILL_LOG=str(tmp_path / 'signals'))
    command = ["bash", str(script), "--module", str(module), "--out",
               str(tmp_path / "ledger.json"), "--suite", "24.04",
               "--incompatible", str(incompatible)]
    return tmp_path, work, env, command


@pytest.mark.parametrize("mode,success", [('', True), ('config-fail', False),
                                        ('start-fail', False), ('http-fail', False)])
@pytest.mark.parametrize('default_kind', ['file', 'symlink'])
def test_owned_resources_and_existing_host_survive(sandbox, mode, success, default_kind):
    """All regular exits preserve input modules/sites and unrelated services."""
    root, work, env, command = sandbox
    env['MODE'] = mode
    if default_kind == 'file':
        (root / 'default').unlink()
        (root / 'default').write_text('custom standalone site\n')
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        for _ in range(2):
            result = subprocess.run(command, env=env, capture_output=True, timeout=20)
            assert (result.returncode == 0) is success, result.stderr.decode()
            assert sentinel.poll() is None
            assert (root / 'ngx_http_markdown_filter_module.so').read_bytes() == b'existing host module'
            if default_kind == 'symlink':
                assert (root / 'default').is_symlink()
                assert os.readlink(root / 'default') == 'custom-site'
            else:
                assert not (root / 'default').is_symlink()
                assert (root / 'default').read_text() == 'custom standalone site\n'
            assert (root / 'custom-site').read_text() == 'custom host configuration\n'
            assert (root / 'modules-enabled/50-mod-markdown-verify.conf').read_text() == '# existing module config\n'
            assert (root / 'document-root/sentinel').read_text() == 'existing web root\n'
            assert not list(work.iterdir())
            ledger = json.loads((root / 'ledger.json').read_text())
            assert ledger['schema_version'] == 1
            assert ledger['source_kind'] == 'ubuntu-default-repo'
            assert {test['name'] for test in ledger['tests']} == {
                'load', 'request_markdown', 'request_passthrough', 'lifecycle',
                'negative_abi_rejected'}
            assert ledger['skip'] == 0
            assert (ledger['fail'] == 0) is success
            if mode in ('config-fail', 'start-fail'):
                assert not (root / 'signals').exists()
    finally:
        sentinel.terminate()
        sentinel.wait()


@pytest.mark.parametrize('stop_signal', [signal.SIGTERM, signal.SIGINT])
def test_signal_cleans_only_owned_child(sandbox, stop_signal):
    """Signal exits stop the private child and remove its private prefix."""
    root, work, env, command = sandbox
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        with _owned_verifier(command, env, root) as process:
            _wait_for_file(root / 'child.pid')
            process.send_signal(stop_signal)
            assert int(_wait_for_file(root / 'verifier.exit')) != 0
            assert sentinel.poll() is None
            assert (root / 'child.exit').read_text() == 'stopped'
            assert not list(work.iterdir())
            assert (root / 'ngx_http_markdown_filter_module.so').read_bytes() == b'existing host module'
    finally:
        sentinel.terminate()
        sentinel.wait()


def test_session_cleanup_stops_child_after_verifier_is_killed(sandbox):
    """Teardown must clean leaked children even when the shell cannot do so."""
    root, _, env, command = sandbox
    with pytest.raises(AssertionError, match='simulated validation failure'):
        with _owned_verifier(command, env, root) as process:
            _wait_for_file(root / 'child.pid')
            process.send_signal(signal.SIGUSR1)
            assert int(_wait_for_file(root / 'verifier.exit')) == -signal.SIGKILL
            assert not (root / 'child.exit').exists()
            raise AssertionError('simulated validation failure')
    assert _wait_for_file(root / 'child.exit') == 'stopped'


def test_negative_control_requires_signature_rejection(sandbox):
    """An unrelated NGINX error cannot establish an ABI rejection."""
    root, _, env, command = sandbox
    env['NEGATIVE_ERROR'] = 'module file not found'
    result = subprocess.run(command, env=env, capture_output=True, timeout=20)
    assert result.returncode != 0
    ledger = json.loads((root / 'ledger.json').read_text())
    negative = next(test for test in ledger['tests'] if test['name'] == 'negative_abi_rejected')
    assert negative['status'] == 'FAIL'


def test_busy_port_aborts_before_creating_resources(sandbox):
    """A pre-existing listener must never be mistaken for the test server."""
    root, work, env, command = sandbox
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        result = subprocess.run(command + ['--port', str(listener.getsockname()[1])],
                                env=env, capture_output=True, timeout=10)
        assert result.returncode != 0
        assert not (root / 'calls').exists()
        assert not list(work.iterdir())


def test_rejects_nginx_org_package_before_start(sandbox):
    """An Ubuntu host alone does not establish distro NGINX identity."""
    root, work, env, command = sandbox
    env['NGINX_REVISION'] = '1.24.0-1~noble'
    result = subprocess.run(command, env=env, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert b'not an Ubuntu revision' in result.stderr
    assert not list(work.iterdir())
    assert not (root / 'child.pid').exists()


def test_rejects_stale_build_revision(sandbox):
    """A matching module ABI must not launder an untested package revision."""
    root, work, env, command = sandbox
    provenance = root / 'build.json'
    provenance.write_text(json.dumps({'nginx_version': '1.24.0',
                                      'nginx_package_revision': '1.24.0-2ubuntu7.18',
                                      'module_sha256': hashlib.sha256(
                                          (root / 'ngx_http_markdown_filter_module.so').read_bytes()).hexdigest()}))
    result = subprocess.run(command + ['--provenance', str(provenance)], env=env,
                            capture_output=True, timeout=10)
    assert result.returncode != 0
    assert result.stderr.strip() == b'installed NGINX revision differs from build provenance'
    assert not list(work.iterdir())
    assert not (root / 'child.pid').exists()


def test_rejects_different_module_from_build_provenance(sandbox):
    """A ledger may copy a build digest only after checking the loaded bytes."""
    root, work, env, command = sandbox
    provenance = root / 'build.json'
    provenance.write_text(json.dumps({'nginx_version': '1.24.0',
                                      'nginx_package_revision': '1.24.0-2ubuntu7.19',
                                      'module_sha256': '0' * 64}))
    result = subprocess.run(command + ['--provenance', str(provenance)], env=env,
                            capture_output=True, timeout=10)
    assert result.returncode != 0
    assert b'module digest differs' in result.stderr
    assert not list(work.iterdir())
    assert not (root / 'child.pid').exists()
