#!/usr/bin/env python3
"""Simple parse and match digest.md"""

import re
from pathlib import Path

DIGEST_PATH = Path.home() / 'code-review-0.9.2' / 'cr-run-20260819-new' / 'digest.md'

with open(DIGEST_PATH, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"Total lines: {len(lines)}")

# Simple parse: find lines that start with '- **[SEVERITY]**'
findings = []
for i, line in enumerate(lines):
    line = line.rstrip('\n')
    if not line.startswith('- **['):
        continue
    # Split on backtick
    parts = line.split('`', 2)
    if len(parts) < 3:
        continue
    sev_match = re.search(r'\*\*(\w+)\*\*', parts[0])
    if not sev_match:
        continue
    severity = sev_match.group(1)
    file_part = parts[1]
    desc = parts[2].split('\u2014', 1)[1].strip() if '\u2014' in parts[2] else parts[2].strip()
    findings.append({
        'severity': severity,
        'file': file_part,
        'description': desc
    })

print(f"Parsed {len(findings)} findings")

# Simple match against completed
COMPLETED_KEYS = [
    ('request_impl.h', 'HEAD'), ('request_impl.h', 'Decision E'),
    ('headers_impl.h', 'HEAD'), ('conditional_requests.rs', 'case 10'),
    ('http_backend.rs', 'HEAD'), ('verify_proxy_tls_backend_e2e.sh', 'Case 4'),
    ('verify_diagnostics_access_phase_e2e.sh', 'run_case'),
    ('verify_diagnostics_access_phase_e2e.sh', 'Case 3'),
    ('preinstall.sh', 'patch'), ('preinstall.sh', 'exact'),
    ('nfpm.yaml', 'depends'), ('control.in', 'Depends'),
    ('nginx-module-markdown.spec', 'Requires'),
    ('release-deb.yml', 'depends'), ('release-rpm.yml', 'Requires'),
    ('release-deb.yml', 'NGINX_VERSION_FLOOR'),
    ('validate_package_metadata.py', 'NFPM_REQUIRED_SNIPPETS'),
    ('test_validate_package_metadata.py', 'test_nfpm'),
    ('test-preinstall-version-policy.sh', 'exact'),
    ('test-deb-install.sh', 'exact'), ('smoke-test-basic.sh', 'exact'),
    ('verify_diagnostics_access_phase_e2e.sh', 'run_case'), ('verify_diagnostics_access_phase_e2e.sh', 'Case 3'),
    ('postinstall.sh', 'modules-enabled'), ('postinstall.sh', 'main-context'),
    ('preremove.sh', 'symlink'), ('smoke-test-basic.sh', 'modules-enabled'),
    ('test-deb-install.sh', 'nginx -t'), ('apt/README.md', 'modules-enabled'),
    ('validate_package_metadata.py', 'STANDALONE_DEB_SNIPPETS'),
    ('validate_package_metadata.py', 'STANDALONE_RPM_WORKFLOW_SNIPPETS'),
    ('headers_impl.h', 'trailers'), ('stream_commit.c', 'trailers'),
    ('conditional.c', 'trailers'), ('tls_backend_server.py', 'trailer'),
    ('verify_proxy_tls_backend_e2e.sh', 'trailer'), ('headers_test.c', 'trailers'),
    ('release-packages.yml', 'MATRIX_ARCH'),
    ('update_matrix.py', 'support_tier'), ('test_update_matrix.py', 'support_tier'),
    ('verify_diagnostics_access_phase_e2e.sh', 'run_case'),
    ('preremove.sh', 'symlink'),
    ('deployment.yaml', 'image.repository'), ('deployment.yaml', 'required'),
    ('values.yaml', 'image.repository'), ('values.yaml', 'image.tag'),
    ('KUBERNETES_DEPLOYMENT.md', 'installable by default'),
    ('gate4_local_k8s_smoke.sh', 'zero-override'),
    ('smoke-test.sh', 'ingress-nginx'), ('smoke-test.sh', 'app=nginx-markdown'),
    ('smoke-test.sh', 'port-forward'), ('KUBERNETES_DEPLOYMENT.md', 'ingress-nginx'),
    ('smoke-test.sh', 'metrics'), ('KUBERNETES_DEPLOYMENT.md', 'metrics'),
    ('release-matrix.json', 'musl'),
    ('run_e2e_suite.sh', 'filter_ordering=skipped'), ('run_e2e_suite.sh', 'subrequest_ssi=skipped'),
    ('subrequest_ssi_test.sh', 'scenario 5'), ('subrequest_ssi_test.sh', 'REQUIRE_AUTH_SUBREQUEST'),
    ('Makefile', 'filter ordering'), ('subrequest_ssi_test.sh', 'REQUIRE_AUTH_SUBREQUEST'),
    ('conditional.c', 'last_modified'), ('stream_commit.c', 'last_modified'),
    ('headers_impl.h', 'last_modified'), ('conditional.c', 'content_type_lowcase'),
    ('stream_commit.c', 'content_type_lowcase'), ('headers_impl.h', 'content_type_lowcase'),
    ('conditional.c', 'content_type_lowcase'), ('stream_commit.c', 'content_type_lowcase'),
    ('headers_impl.h', 'content_type_lowcase'),
    ('pr_body.md', 'SHA'), ('pr_body.md', 'To be updated'),
    ('DEPLOYMENT_EXAMPLES.md', 'HEAD'), ('DEPLOYMENT_EXAMPLES.md', 'curl -I'),
    ('LARGE_RESPONSE_DESIGN.md', 'HEAD request'), ('KUBERNETES_DEPLOYMENT.md', 'installable by default'),
    ('PACKAGE_INSTALLATION.md', 'nginx -V'), ('LARGE_RESPONSE_DESIGN.md', 'HEAD request'),
    ('KUBERNETES_DEPLOYMENT.md', 'ingress-nginx'), ('PACKAGE_INSTALLATION.md', 'nginx -V'),
    ('0.9.2-release-notes.md', 'HEAD'), ('KUBERNETES_DEPLOYMENT.md', 'smoke-test'),
    ('apt/README.md', 'modules-enabled'),
]

import json
from pathlib import Path

# Simple matching
findings = []
with open('/Users/liukang/code-review-0.9.2/cr-run-20260819-new/digest.md', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.rstrip('\n')
        if not line.startswith('- **['):
            continue
        parts = line.split('`', 2)
        if len(parts) < 3:
            continue
        sev_match = re.search(r'\*\*(\w+)\*\*', parts[0])
        if not sev_match:
            continue
        severity = sev_match.group(1)
        file_part = parts[1]
        desc = parts[2].split('\u2014', 1)[1].strip() if '\u2014' in parts[2] else parts[2].strip()
        findings.append({'severity': severity, 'file': file_part, 'description': desc})

print(f"Parsed {len(findings)} findings")

# Match
COMPLETED_SET = {
    'request_impl.h:HEAD', 'request_impl.h:Decision E', 'headers_impl.h:HEAD',
    'conditional_requests.rs:case 10', 'http_backend.rs:HEAD',
    'verify_proxy_tls_backend_e2e.sh:Case 4', 'verify_diagnostics_access_phase_e2e.sh:run_case',
    'verify_diagnostics_access_phase_e2e.sh:Case 3',
    'preinstall.sh:patch', 'preinstall.sh:exact', 'nfpm.yaml:depends',
    'control.in:Depends', 'nginx-module-markdown.spec:Requires',
    'release-deb.yml:depends', 'release-rpm.yml:Requires',
    'release-deb.yml:NGINX_VERSION_FLOOR',
    'validate_package_metadata.py:NFPM_REQUIRED_SNIPPETS',
    'test_validate_package_metadata.py:test_nfpm',
    'test-preinstall-version-policy.sh:exact', 'test-deb-install.sh:exact',
    'smoke-test-basic.sh:exact',
    'verify_diagnostics_access_phase_e2e.sh:run_case', 'verify_diagnostics_access_phase_e2e.sh:Case 3',
    'postinstall.sh:modules-enabled', 'postinstall.sh:main-context',
    'preremove.sh:symlink', 'smoke-test-basic.sh:modules-enabled',
    'test-deb-install.sh:nginx -t', 'apt/README.md:modules-enabled',
    'validate_package_metadata.py:STANDALONE_DEB_SNIPPETS',
    'validate_package_metadata.py:STANDALONE_RPM_WORKFLOW_SNIPPETS',
    'headers_impl.h:trailers', 'stream_commit.c:trailers', 'conditional.c:trailers',
    'tls_backend_server.py:trailer', 'verify_proxy_tls_backend_e2e.sh:trailer', 'headers_test.c:trailers',
    'release-packages.yml:MATRIX_ARCH',
    'update_matrix.py:support_tier', 'test_update_matrix.py:support_tier',
    'verify_diagnostics_access_phase_e2e.sh:run_case',
    'preremove.sh:symlink',
    'deployment.yaml:image.repository', 'deployment.yaml:required',
    'values.yaml:image.repository', 'values.yaml:image.tag',
    'KUBERNETES_DEPLOYMENT.md:installable by default', 'gate4_local_k8s_smoke.sh:zero-override',
    'smoke-test.sh:ingress-nginx', 'smoke-test.sh:app=nginx-markdown',
    'smoke-test.sh:port-forward', 'KUBERNETES_DEPLOYMENT.md:ingress-nginx',
    'smoke-test.sh:metrics', 'KUBERNETES_DEPLOYMENT.md:metrics',
    'release-matrix.json:musl',
    'run_e2e_suite.sh:filter_ordering=skipped', 'run_e2e_suite.sh:subrequest_ssi=skipped',
    'subrequest_ssi_test.sh:scenario 5', 'subrequest_ssi_test.sh:REQUIRE_AUTH_SUBREQUEST',
    'Makefile:filter ordering', 'subrequest_ssi_test.sh:REQUIRE_AUTH_SUBREQUEST',
    'conditional.c:last_modified', 'stream_commit.c:last_modified',
    'headers_impl.h:last_modified', 'conditional.c:content_type_lowcase',
    'stream_commit.c:content_type_lowcase', 'headers_impl.h:content_type_lowcase',
    'conditional.c:content_type_lowcase', 'stream_commit.c:content_type_lowcase',
    'headers_impl.h:content_type_lowcase',
    'pr_body.md:SHA', 'pr_body.md:To be updated',
}

def check_completed(f):
    file = f['file'].lower()
    desc = f['description'].lower()
    for key in COMPLETED_SET:
        pfile, pdesc = key.split(':', 1)
        if pfile in f['file'] and pdesc in f['description'].lower():
            return True
    return False

import re
findings = []
with open('/Users/liukang/code-review-0.9.2/cr-run-20260819-new/digest.md', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.rstrip('\n')
        if not line.startswith('- **['):
            continue
        parts = line.split('`', 2)
        if len(parts) < 3:
            continue
        sev_match = re.search(r'\*\*(\w+)\*\*', parts[0])
        if not sev_match:
            continue
        severity = sev_match.group(1)
        file_part = parts[1]
        desc = parts[2].split('\u2014', 1)[1].strip() if '\u2014' in parts[2] else parts[2].strip()
        findings.append({'severity': severity, 'file': file_part, 'description': desc})

print(f"Parsed {len(findings)} findings")

completed = []
pending = []
false_pos = []

for f in findings:
    key = f'{f["file"].lower()}:{f["description"].lower()}'
    matched = False
    for ckey in COMPLETED_SET:
        pfile, pdesc = ckey.split(':', 1)
        if pfile in f['file'].lower() and pdesc in f['description'].lower():
            matched = True
            break
    if matched:
        completed.append(f)
    elif any(kw in f['description'].lower() for kw in ['todo', 'consider', 'prefer', 'suggest', 'nit:', 'style:', 'format:', 'convention']):
        false_pos.append(f)
    else:
        pending.append(f)

print(f"Completed: {len(completed)}")
print(f"Pending: {len(pending)}")
print(f"False positive: {len(false_pos)}")

# Save report
import json
report = {
    'total': len(findings),
    'completed': len(completed),
    'pending': len(pending),
    'false_positive': len(false_pos),
    'pending_items': [{'file': f['file'], 'severity': f['severity'], 'description': f['description']} for f in pending],
    'completed_items': [{'file': f['file'], 'description': f['description']} for f in completed],
}
with open('/Users/liukang/code-review-0.9.2/cr-run-20260819-new/digest-processed.json', 'w') as f:
    json.dump(report, f, indent=2)
print('Report saved')
EOF