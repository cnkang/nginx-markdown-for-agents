#!/usr/bin/env python3
"""
Batch process CodeRabbit findings digest:
- Parse digest.md findings
- Cross-reference with completed fixes
- Mark completed/pending/false-positive
- Output actionable P2/P3 list
"""

import re
import json
from pathlib import Path

DIGEST_PATH = Path.home() / 'code-review-0.9.2' / 'cr-run-20260819-new' / 'digest.md'

# Completed fixes mapping (file/path -> finding description keywords)
COMPLETED = {
    # P0-1 HEAD representation
    ('components/nginx-module/src/ngx_http_markdown_request_impl.h', 'HEAD'): 'P0-1 HEAD representation',
    ('components/nginx-module/src/ngx_http_markdown_headers_impl.h', 'HEAD'): 'P0-1 HEAD representation',
    ('components/nginx-module/src/ngx_http_markdown_request_impl.h', 'Decision E'): 'P0-1 Decision E',
    ('tools/e2e-harness/src/scenarios/conditional_requests.rs', 'case 10'): 'P0-1 E2E case 10',
    ('tools/e2e-harness/src/fixtures/http_backend.rs', 'HEAD'): 'P0-1 fixture',
    ('tools/e2e/verify_proxy_tls_backend_e2e.sh', 'Case 4'): 'P0-1 Case 4',
    ('tools/e2e/verify_diagnostics_access_phase_e2e.sh', 'run_case'): 'P1-2 run_case rc',
    ('tools/e2e/verify_diagnostics_access_phase_e2e.sh', 'run_case'): 'P1-2 rc capture',
    ('tools/e2e/verify_diagnostics_access_phase_e2e.sh', 'Case 3'): 'P1-2 deny case',

    # P0-2 exact version
    ('packaging/nfpm/scripts/preinstall.sh', 'patch'): 'P0-2 exact version',
    ('packaging/nfpm/nfpm.yaml', 'depends'): 'P0-2 exact version deps',
    ('packaging/debian/control.in', 'Depends'): 'P0-2 DEB exact',
    ('packaging/rpm/SPECS/nginx-module-markdown.spec', 'Requires'): 'P0-2 RPM exact',
    ('.github/workflows/release-deb.yml', 'depends'): 'P0-2 DEB exact',
    ('.github/workflows/release-rpm.yml', 'Requires'): 'P0-2 RPM exact',
    ('.github/workflows/release-deb.yml', 'NGINX_VERSION_FLOOR'): 'P0-2 FLOOR/CEIL removed',
    ('tools/release/gates/validate_package_metadata.py', 'NFPM_REQUIRED_SNIPPETS'): 'P0-2 validator exact',
    ('tools/release/gates/tests/test_validate_package_metadata.py', 'test_nfpm_dependency_uses_exact'): 'P0-2 test exact',
    ('packaging/tests/test-preinstall-version-policy.sh', 'exact'): 'P0-2 test preinstall',
    ('packaging/tests/test-deb-install.sh', 'exact'): 'P0-2 test deb exact',
    ('packaging/scripts/smoke-test-basic.sh', 'exact'): 'P0-2 smoke exact',

    # P0-3 DEB enablement
    ('packaging/nfpm/scripts/postinstall.sh', 'modules-enabled'): 'P0-3 postinstall main-context',
    ('packaging/nfpm/scripts/preremove.sh', 'symlink'): 'P1-3 preremove symlink',
    ('packaging/scripts/smoke-test-basic.sh', 'modules-enabled'): 'P0-3 smoke modules-enabled',
    ('packaging/tests/test-deb-install.sh', 'nginx -t'): 'P0-3 smoke nginx -t',
    ('packaging/repo/apt/README.md', 'modules-enabled'): 'P0-3 apt readme',
    ('tools/release/gates/validate_package_metadata.py', 'STANDALONE_DEB_SNIPPETS'): 'P0-3 validator DEB',
    ('tools/release/gates/validate_package_metadata.py', 'STANDALONE_RPM_WORKFLOW_SNIPPETS'): 'P0-3 validator RPM',
    ('packaging/nfpm/scripts/preremove.sh', 'symlink'): 'P1-3 preremove',
    ('packaging/nfpm/scripts/postinstall.sh', 'main-context'): 'P0-3 postinstall',

    # P0-4 trailers
    ('components/nginx-module/src/ngx_http_markdown_headers_impl.h', 'trailers'): 'P0-4 clear trailers',
    ('components/nginx-module/src/ngx_http_markdown_stream_commit.c', 'trailers'): 'P0-4 trailers stream',
    ('components/nginx-module/src/ngx_http_markdown_conditional.c', 'trailers'): 'P0-4 trailers conditional',
    ('tools/e2e/fixtures/tls_backend_server.py', 'trailer'): 'P0-4 E2E trailer',
    ('tools/e2e/verify_proxy_tls_backend_e2e.sh', 'trailer'): 'P0-4 E2E verify',
    ('components/nginx-module/tests/unit/headers_test.c', 'trailers'): 'P0-4 test trailers',

    # P0-5 MATRIX_ARCH
    ('.github/workflows/release-packages.yml', 'MATRIX_ARCH'): 'P0-5 MATRIX_ARCH',

    # P1-1 support_tier
    ('tools/release/matrix/update_matrix.py', 'support_tier'): 'P1-1 support_tier',
    ('tools/release/matrix/tests/test_update_matrix.py', 'support_tier'): 'P1-1 test support_tier',

    # P1-2 run_case rc
    ('tools/e2e/verify_diagnostics_access_phase_e2e.sh', 'run_case'): 'P1-2 rc capture',

    # P1-3 preremove symlink
    ('packaging/nfpm/scripts/preremove.sh', 'symlink'): 'P1-3 preremove',

    # P1-4 Helm
    ('charts/nginx-markdown/templates/deployment.yaml', 'image.repository'): 'P1-4 Helm image required',
    ('charts/nginx-markdown/values.yaml', 'image.repository'): 'P1-4 Helm values',
    ('charts/nginx-markdown/values.yaml', 'image.tag'): 'P1-4 Helm tag',
    ('docs/guides/KUBERNETES_DEPLOYMENT.md', 'installable by default'): 'P1-4 docs Helm',
    ('tools/release/gates/gate4_local_k8s_smoke.sh', 'zero-override'): 'P1-4 gate4 zero-override',
    ('charts/nginx-markdown/values.yaml', 'image.repository'): 'P1-4 Helm required',
    ('charts/nginx-markdown/templates/deployment.yaml', 'required'): 'P1-4 Helm required',

    # P1-5 K8s smoke
    ('examples/kubernetes/tests/smoke-test.sh', 'ingress-nginx'): 'P1-5 K8s namespace',
    ('examples/kubernetes/tests/smoke-test.sh', 'app=nginx-markdown'): 'P1-5 K8s label',
    ('examples/kubernetes/tests/smoke-test.sh', 'port-forward.*:80'): 'P1-5 K8s port',
    ('docs/guides/KUBERNETES_DEPLOYMENT.md', 'ingress-nginx'): 'P1-5 K8s docs',
    ('examples/kubernetes/tests/smoke-test.sh', '/metrics'): 'P1-5 K8s metrics path',
    ('docs/guides/KUBERNETES_DEPLOYMENT.md', '/metrics'): 'P1-5 K8s metrics docs',

    # P1-6 musl
    ('tools/release-matrix.json', 'musl'): 'P1-6 musl blocking',

    # P1-6 E2E SKIP
    ('tools/e2e/run_e2e_suite.sh', 'filter_ordering=skipped'): 'P1-7 filter_ordering mandatory',
    ('tools/e2e/run_e2e_suite.sh', 'subrequest_ssi=skipped'): 'P1-7 subrequest_ssi mandatory',
    ('tests/e2e/subrequest_ssi_test.sh', 'scenario 5'): 'P1-7 subrequest_in_memory',
    ('tests/e2e/subrequest_ssi_test.sh', 'REQUIRE_AUTH_SUBREQUEST'): 'P1-7 subrequest_in_memory',
    ('Makefile', 'filter ordering'): 'P1-7 Makefile mandatory',
    ('tests/e2e/subrequest_ssi_test.sh', 'REQUIRE_AUTH_SUBREQUEST'): 'P1-7 subrequest_ssi',

    # P1-8 Decision G Last-Modified
    ('components/nginx-module/src/ngx_http_markdown_conditional.c', 'last_modified'): 'P1-8 Last-Modified conditional',
    ('components/nginx-module/src/ngx_http_markdown_stream_commit.c', 'last_modified'): 'P1-8 Last-Modified stream',
    ('components/nginx-module/src/ngx_http_markdown_headers_impl.h', 'last_modified'): 'P1-8 Last-Modified headers_impl',
    ('components/nginx-module/src/ngx_http_markdown_conditional.c', 'content_type_lowcase'): 'P1-8 content_type cache',
    ('components/nginx-module/src/ngx_http_markdown_stream_commit.c', 'content_type_lowcase'): 'P1-8 content_type cache stream',
    ('components/nginx-module/src/ngx_http_markdown_headers_impl.h', 'content_type_lowcase'): 'P1-8 content_type cache headers_impl',

    # P1-9 PR body
    ('pr_body.md', 'SHA'): 'P1-9 PR body SHA',
    ('pr_body.md', 'To be updated'): 'P1-9 PR body placeholders',

    # docs
    ('docs/guides/DEPLOYMENT_EXAMPLES.md', 'HEAD.*fail-open'): 'P0-1 docs fail-open',
    ('docs/guides/DEPLOYMENT_EXAMPLES.md', 'curl -I'): 'P1-4 docs curl -I',
    ('docs/architecture/LARGE_RESPONSE_DESIGN.md', 'HEAD request'): 'P1-8 HEAD docs',
    ('docs/guides/KUBERNETES_DEPLOYMENT.md', 'installable by default'): 'P1-4 K8s docs',
    ('docs/guides/PACKAGE_INSTALLATION.md', 'nginx -V'): 'P0-2 package verify',
    ('docs/architecture/LARGE_RESPONSE_DESIGN.md', 'HEAD request'): 'P1-8 HEAD docs',
    ('docs/guides/KUBERNETES_DEPLOYMENT.md', 'ingress-nginx'): 'P1-5 K8s namespace docs',
    ('docs/guides/PACKAGE_INSTALLATION.md', 'nginx -V'): 'P0-2 package verify',
    ('docs/releases/0.9.2-release-notes.md', 'HEAD'): 'P1-8 HEAD release notes',
    ('docs/guides/KUBERNETES_DEPLOYMENT.md', 'smoke-test.sh'): 'P1-5 K8s smoke docs',
    ('packaging/repo/apt/README.md', 'modules-enabled'): 'P0-3 apt readme',
}

def parse_digest(path):
    content = path.read_text()
    findings = []
    
    # Parse each finding line: "- **[SEVERITY]** `file:line` (slice) — description"
    pattern = r'^\- \*\*(CRITICAL|MAJOR|MINOR|P2|P3)\*\* `([^:]+):(\d+)` \([^)]+\) — (.+)$'
    for line in content.split('\n'):
        m = re.match(pattern, line)
        if m:
            sev, file, line, desc = m.groups()
            findings.append({
                'severity': sev,
                'file': file,
                'line': int(line),
                'description': desc.strip(),
                'matched': None
            })
    return findings

def match_finding(finding):
    """Return (completed_key, P_level) if matches a completed fix"""
    file_key = finding['file']
    desc_lower = finding['description'].lower()
    
    for (pattern_file, pattern_desc), p_level in COMPLETED.items():
        # Match file path (basename or full)
        if pattern_file in finding['file'] or pattern_file == finding['file']:
            if pattern_desc.lower() in finding['description'].lower() or pattern_desc.lower() in finding['file'].lower():
                return p_level
    return None

def main():
    findings = parse_digest(DIGEST_PATH)
    print(f"Total findings: {len(findings)}")
    
    completed = []
    pending = []
    false_positive = []
    
    for f in findings:
        p_level = match_finding(f)
        if p_level:
            f['matched'] = p_level
            completed.append(f)
        else:
            # Check for false positive patterns
            desc = f['description'].lower()
            if any(kw in f['description'].lower() for kw in ['todo', 'todo:', 'consider', 'prefer', 'suggest', 'nit:', 'style:', 'format:', 'convention']):
                false_positive.append(f)
            else:
                pending.append(f)
    
    print(f"\n✓ Completed/Addressed: {len(completed)}")
    for f in completed:
        print(f"  [{f['matched']}] {f['file']}:{f['line']} — {f['description'][:80]}...")
    
    print(f"\n? Pending review: {len(pending)}")
    for f in pending[:20]:
        print(f"  [{f['severity']}] {f['file']}:{f['line']} — {f['description'][:80]}...")
    if len(pending) > 20:
        print(f"  ... and {len(pending) - 20} more")
    
    print(f"\n✗ False positives / nit-picks: {len(false_positive)}")
    for f in false_positive[:10]:
        print(f"  [{f['severity']}] {f['file']}:{f['line']} — {f['description'][:80]}...")
    
    # Output actionable P2/P3 list
    print("\n" + "="*60)
    print("ACTIONABLE P2/P3 ITEMS (pending, not false positive):")
    print("="*60)
    for f in pending:
        print(f"\n{f['file']}:{f['line']} [{f['severity']}]")
        print(f"  {f['description']}")
    
    # Save detailed report
    report = {
        'total': len(findings),
        'completed': len(completed),
        'pending': len(pending),
        'false_positive': len(false_positive),
        'pending_items': [
            {'file': f['file'], 'line': f['line'], 'severity': f['severity'], 'description': f['description']}
            for f in pending
        ],
        'completed_items': [
            {'file': f['file'], 'line': f['line'], 'matched': f['matched'], 'description': f['description']}
            for f in completed
        ],
        'false_positive_items': [
            {'file': f['file'], 'line': f['line'], 'severity': f['severity'], 'description': f['description']}
            for f in false_positive
        ]
    }
    
    out_path = Path.home() / 'code-review-0.9.2' / 'cr-run-20260819-new' / 'digest-processed.json'
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\n\nDetailed report saved to {out_path}")

if __name__ == '__main__':
    main()