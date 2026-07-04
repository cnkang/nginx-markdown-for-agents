#!/usr/bin/env python3
"""Verify that no stale 0.8.0 directives/symbols have leaked into 0.9.0.
This gate is designed to stop the 'forgot to update directive' pattern.
"""
import sys
import subprocess
from pathlib import Path

STALE_SYMBOLS = [
    "markdown_on_wildcard",
    "markdown_max_size",
    "markdown_timeout",
    "markdown_memory_budget",
    "markdown_streaming_budget",
]

def main():
    repo = Path(__file__).resolve().parents[3]
    findings = []
    
    # We exclude docs/guides/MIGRATION-0.9.md and docs/harness/rules/ because 
    # they might reference stale symbols for explanation purposes.
    exclude_paths = ["MIGRATION-0.9.md", "docs/harness/rules/"]
    
    for symbol in STALE_SYMBOLS:
        cmd = ["grep", "-rn", symbol, str(repo)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            for line in result.stdout.splitlines():
                # filter excludes
                if any(excl in line for excl in exclude_paths):
                    continue
                findings.append(line)
        except Exception as e:
            print(f"Error grepping {symbol}: {e}")
            
    if findings:
        print("STALE SYMBOLS DETECTED:")
        for f in findings:
            print(f)
        sys.exit(1)
    
    print("No stale 0.8 symbols found.")
    sys.exit(0)

if __name__ == "__main__":
    main()
