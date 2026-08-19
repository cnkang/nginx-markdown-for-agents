#!/usr/bin/env python3
"""
Parse CodeRabbit findings digest into structured data.
"""

import re
from pathlib import Path
import json

DIGEST_PATH = Path.home() / 'code-review-0.9.2' / 'cr-run-20260819-new' / 'digest.md'

def parse_findings():
    findings = []
    with open(DIGEST_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Match findings in format: - **[SEVERITY]** `file:line-range` (slice) — description
    pattern = r'-\s*\*\*\[(\w+)\]\*\*\s*`([^`]+)`\s*\([^)]+\)\s*—\s*(.+)'
    matches = re.findall(pattern, content)
    
    for severity, file_part, description in matches:
        findings.append({
            'severity': severity,
            'file': file_part,
            'description': description.strip()
        })
    
    return findings

def main():
    findings = parse_findings()
    print(f"Total findings: {len(findings)}")
    
    by_severity = {}
    for f in findings:
        sev = f['severity']
        by_severity[sev] = by_severity.get(sev, 0) + 1
    
    for sev, count in sorted(by_severity.items()):
        print(f"  {sev}: {count}")
    
    # Save to JSON for further processing
    out_path = Path.home() / 'code-review-0.9.2' / 'cr-run-20260819-new' / 'findings.json'
    out_path.write_text(json.dumps(findings, indent=2))
    print(f"\nSaved {len(findings)} findings to {out_path}")
    
    # Print first few of each severity
    for sev in ['CRITICAL', 'MAJOR', 'MINOR']:
        print(f"\n=== {sev} ===")
        count = 0
        for f in findings:
            if f['severity'] == sev:
                print(f"  {f['file']}: {f['description'][:100]}...")
                count += 1
                if count >= 5:
                    break

if __name__ == '__main__':
    main()