#!/usr/bin/env python3
"""
Batch process CodeRabbit findings digest - simple robust parser.
"""

import re
from pathlib import Path

DIGEST_PATH = Path.home() / 'code-review-0.9.2' / 'cr-run-20260819-new' / 'digest.md'

# Already completed fixes (file pattern -> P-level)
COMPLETED = {
    # P0-1 HEAD representation
    ('request_impl.h', 'HEAD'): 'P0-1',
    ('request_impl.h', 'Decision E'): 'P0-1',
    ('headers_impl.h', 'HEAD'): 'P0-1',
    ('conditional_requests.rs', 'case 10'): 'P0-1',
    ('http_backend.rs', 'HEAD'): 'P0-1',
    ('verify_proxy_tls_backend_e2e.sh', 'Case 4'): 'P0-1',
    ('verify_diagnostics_access_phase_e2e.sh', 'run_case'): 'P1-2',
    ('verify_diagnostics_access_phase_e2e.sh', 'Case 3'): 'P1-2',
    # P0-2 exact version
    ('preinstall.sh', 'patch'): 'P0-2',
    ('preinstall.sh', 'exact'): 'P0-2',
    ('nfpm.yaml', 'depends'): 'P0-2',
    ('control.in', 'Depends'): 'P0-2',
    ('nginx-module-markdown.spec', 'Requires'): 'P0-2',
    ('release-deb.yml', 'depends'): 'P0-2',
    ('release-rpm.yml', 'Requires'): 'P0-2',
    ('release-deb.yml', 'NGINX_VERSION_FLOOR'): 'P0-2',
    ('validate_package_metadata.py', 'NFPM_REQUIRED_SNIPPETS'): 'P0-2',
    ('test_validate_package_metadata.py', 'test_nfpm'): 'P0-2',
    ('test-preinstall-version-policy.sh', 'exact'): 'P0-2',
    ('test-deb-install.sh', 'exact'): 'P0-2',
    ('smoke-test-basic.sh', 'exact'): 'P0-2',
    ('verify_diagnostics_access_phase_e2e.sh', 'run_case'): 'P1-2',
    ('verify_diagnostics_access_phase_e2e.sh', 'Case 3'): 'P1-2',
    # P0-3 DEB enablement
    ('postinstall.sh', 'modules-enabled'): 'P0-3',
    ('postinstall.sh', 'main-context'): 'P0-3',
    ('preremove.sh', 'symlink'): 'P1-3',
    ('smoke-test-basic.sh', 'modules-enabled'): 'P0-3',
    ('test-deb-install.sh', 'nginx -t'): 'P0-3',
    ('apt/README.md', 'modules-enabled'): 'P0-3',
    ('validate_package_metadata.py', 'STANDALONE_DEB_SNIPPETS'): 'P0-3',
    ('validate_package_metadata.py', 'STANDALONE_RPM_WORKFLOW_SNIPPETS'): 'P0-3',
    # P0-4 trailers
    ('headers_impl.h', 'trailers'): 'P0-4',
    ('stream_commit.c', 'trailers'): 'P0-4',
    ('conditional.c', 'trailers'): 'P0-4',
    ('tls_backend_server.py', 'trailer'): 'P0-4',
    ('verify_proxy_tls_backend_e2e.sh', 'trailer'): 'P0-4',
    ('headers_test.c', 'trailers'): 'P0-4',
    # P0-5 MATRIX_ARCH
    ('release-packages.yml', 'MATRIX_ARCH'): 'P0-5',
    # P1-1 support_tier
    ('update_matrix.py', 'support_tier'): 'P1-1',
    ('test_update_matrix.py', 'support_tier'): 'P1-1',
    # P1-2 run_case rc
    ('verify_diagnostics_access_phase_e2e.sh', 'run_case'): 'P1-2',
    # P1-3 preremove
    ('preremove.sh', 'symlink'): 'P1-3',
    # P1-4 Helm
    ('deployment.yaml', 'image.repository'): 'P1-4',
    ('deployment.yaml', 'required'): 'P1-4',
    ('values.yaml', 'image.repository'): 'P1-4',
    ('values.yaml', 'image.tag'): 'P1-4',
    ('KUBERNETES_DEPLOYMENT.md', 'installable by default'): 'P1-4',
    ('gate4_local_k8s_smoke.sh', 'zero-override'): 'P1-4',
    # P1-5 K8s smoke
    ('smoke-test.sh', 'ingress-nginx'): 'P1-5',
    ('smoke-test.sh', 'app=nginx-markdown'): 'P1-5',
    ('smoke-test.sh', 'port-forward'): 'P1-5',
    ('KUBERNETES_DEPLOYMENT.md', 'ingress-nginx'): 'P1-5',
    ('smoke-test.sh', 'metrics'): 'P1-5',
    ('KUBERNETES_DEPLOYMENT.md', 'metrics'): 'P1-5',
    # P1-6 musl
    ('release-matrix.json', 'musl'): 'P1-6',
    # P1-7 E2E SKIP
    ('run_e2e_suite.sh', 'filter_ordering=skipped'): 'P1-7',
    ('run_e2e_suite.sh', 'subrequest_ssi=skipped'): 'P1-7',
    ('subrequest_ssi_test.sh', 'scenario 5'): 'P1-7',
    ('subrequest_ssi_test.sh', 'REQUIRE_AUTH_SUBREQUEST'): 'P1-7',
    ('Makefile', 'filter ordering'): 'P1-7',
    ('subrequest_ssi_test.sh', 'REQUIRE_AUTH_SUBREQUEST'): 'P1-7',
    # P1-8 Decision G Last-Modified
    ('conditional.c', 'last_modified'): 'P1-8',
    ('stream_commit.c', 'last_modified'): 'P1-8',
    ('headers_impl.h', 'last_modified'): 'P1-8',
    ('conditional.c', 'content_type_lowcase'): 'P1-8',
    ('stream_commit.c', 'content_type_lowcase'): 'P1-8',
    ('headers_impl.h', 'content_type_lowcase'): 'P1-8',
    ('conditional.c', 'content_type_lowcase'): 'P1-8',
    ('stream_commit.c', 'content_type_lowcase'): 'P1-8',
    ('headers_impl.h', 'content_type_lowcase'): 'P1-8',
    # P1-9 PR body
    ('pr_body.md', 'SHA'): 'P1-9',
    ('pr_body.md', 'To be updated'): 'P1-9',
    # docs
    ('DEPLOYMENT_EXAMPLES.md', 'HEAD'): 'P0-1',
    ('DEPLOYMENT_EXAMPLES.md', 'curl -I'): 'P1-4',
    ('LARGE_RESPONSE_DESIGN.md', 'HEAD request'): 'P1-8',
    ('KUBERNETES_DEPLOYMENT.md', 'installable by default'): 'P1-4',
    ('PACKAGE_INSTALLATION.md', 'nginx -V'): 'P0-2',
    ('LARGE_RESPONSE_DESIGN.md', 'HEAD request'): 'P1-8',
    ('KUBERNETES_DEPLOYMENT.md', 'ingress-nginx'): 'P1-5',
    ('PACKAGE_INSTALLATION.md', 'nginx -V'): 'P0-2',
    ('0.9.2-release-notes.md', 'HEAD'): 'P1-8',
    ('KUBERNETES_DEPLOYMENT.md', 'smoke-test'): 'P1-5',
    ('apt/README.md', 'modules-enabled'): 'P0-3',
}

def parse_findings():
    findings = []
    with open(DIGEST_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            if not line.startswith('- **['):
                continue
            # Parse: - **[SEVERITY]** `file:line-range` (slice) — description
            parts = line.split('`', 2)
            if len(parts) < 3:
                continue
            # Extract severity from **SEVERITY**
            sev_match = re.search(r'\*\*(\w+)\*\*', parts[0])
            if not sev_match:
                continue
            severity = sev_match.group(1)
            file_part = parts[1]
            desc = parts[2].split('—', 1)[1].strip() if '—' in parts[2] else parts[2].strip()
            findings.append({
                'severity': severity,
                'file': file_part,
                'description': desc
            })
    return findings

def match_finding(finding):
    file = finding['file'].lower()
    desc = finding['description'].lower()
    
    for (pattern_file, pattern_desc), p_level in COMPLETED.items():
        if pattern_file in finding['file'] or pattern_file in finding['description']:
            if pattern_desc.lower() in finding['description'].lower() or pattern_desc.lower() in finding['file'].lower():
                return p_level
    return None

def main():
    findings = parse_findings()
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
            desc = f['description'].lower()
            if any(kw in f['description'].lower() for kw in ['todo', 'consider', 'prefer', 'suggest', 'nit:', 'style:', 'format:', 'convention']):
                false_positive.append(f)
            else:
                pending.append(f)
    
    print(f"\n✓ Completed/Addressed: {len(completed)}")
    for f in completed:
        print(f"  [{f['matched']}] {f['file']} — {f['description'][:80]}...")
    
    print(f"\n? Pending review: {len(pending)}")
    for f in pending[:30]:
        print(f"  [{f['severity']}] {f['file']} — {f['description'][:80]}...")
    if len(pending) > 30:
        print(f"  ... and {len(pending) - 30} more")
    
    print(f"\n✗ False positives / nit-picks: {len(false_positive)}")
    for f in false_positive[:10]:
        print(f"  [{f['severity']}] {f['file']} — {f['description'][:80]}...")
    
    print("\n" + "="*60)
    print("ACTIONABLE P2/P3 ITEMS (pending, not false positive):")
    print("="*60)
    for f in pending:
        print(f"\n{f['file']} [{f['severity']}]")
        print(f"  {f['description']}")
    
    # Save detailed report
    import json
    report = {
        'total': len(findings),
        'completed': len(completed),
        'pending': len(pending),
        'false_positive': len(false_positive),
        'pending_items': [
            {'file': f['file'], 'severity': f['severity'], 'description': f['description']}
            for f in pending
        ],
        'completed_items': [
            {'file': f['file'], 'matched': f['matched'], 'description': f['description']}
            for f in completed
        ],
        'false_positive_items': [
            {'file': f['file'], 'severity': f['severity'], 'description': f['description']}
            for f in false_positive
        ]
    }
    
    out_path = Path.home() / 'code-review-0.9.2' / 'cr-run-20260819-new' / 'digest-processed.json'
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\n\nDetailed report saved to {out_path}")

if __name__ == '__main__':
    import re
    import json
    from pathlib import Path
    DIGEST_PATH = Path.home() / 'code-review-0.9.2' / 'cr-run-20260819-new' / 'digest.md'
    main()