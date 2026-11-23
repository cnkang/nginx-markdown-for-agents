"""Command-line entry point for the cross-release compatibility contract.

Three operations matter:

``extract`` renders one revision's contract manifest.
``freeze`` records an annotated release tag as an immutable anchor and refuses
to overwrite an anchor that already names a different commit.
``check`` proves the anchors are faithful to their tags, audits every declared
tag-to-tag comparison, and compares the newest anchor against a candidate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Bootstrap the repository root before the tools.* imports so the module also
# resolves when another script loads it from outside the package tree.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.compatibility import EXTRACTOR_NAME, EXTRACTOR_VERSION  # noqa: E402
from tools.compatibility.compare import (  # noqa: E402
    ADDITIVE,
    OK,
    apply_approvals,
    compare_contracts,
)
from tools.compatibility.extract import extract_surfaces  # noqa: E402
from tools.compatibility.model import (  # noqa: E402
    INDEX_SCHEMA,
    MANIFEST_SCHEMA,
    SURFACES,
    ContractError,
    contract_digest,
    digest_of_bytes,
    dump_manifest,
    surface_digests,
)
from tools.compatibility.sources import (  # noqa: E402
    SourceReader,
    SourceRevision,
    repo_root_for,
    resolve_commit,
    resolve_tag,
    resolve_worktree,
    verify_tag_commit,
)
from tools.lib.path_validation import (  # noqa: E402
    validate_read_path,
    validate_write_path_within_root,
)

DEFAULT_INDEX = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "contracts", "INDEX.json"
)
INDEX_FILE = "INDEX.json"
WORKTREE_TOKEN = "WORKTREE"


def _contracts_dir(index_path: str) -> str:
    """Return the directory that holds the frozen manifests."""
    return os.path.dirname(os.path.abspath(index_path))


# Manifest names are single path components.  An allowlist that excludes every
# separator makes a traversal impossible, and it keeps the value usable as a
# git argument or a file name without further escaping.
_NAME_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _manifest_path(base: str, name: str) -> str:
    """Resolve a single-component manifest name inside the contracts directory."""
    if not name or name in {".", ".."} or set(name) - _NAME_CHARS:
        raise ContractError(
            "manifest name is not a single safe path component: {!r}".format(name)
        )
    return os.path.join(os.path.realpath(base), name)




def _read_json(path: str, root: str) -> Any:
    """Read a JSON file that must live inside ``root``, failing closed."""
    try:
        resolved = validate_read_path(path, purpose="JSON input")
        resolved_root = os.path.realpath(root)
        if os.path.commonpath([resolved_root, os.path.realpath(resolved)]) != resolved_root:
            raise ContractError(
                "JSON input escapes {}: {}".format(resolved_root, resolved)
            )
        with open(resolved, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise ContractError("missing file: {}".format(path))
    except json.JSONDecodeError as error:
        raise ContractError("malformed JSON in {}: {}".format(path, error))


def _repo_root(args: argparse.Namespace, contracts_dir: str) -> str:
    """Return the repository root the gate audits."""
    return args.repo_root or repo_root_for(contracts_dir)


def load_index(index_path: str, repo_root: str) -> Dict[str, Any]:
    """Load and validate the anchor index."""
    index = _read_json(index_path, repo_root)
    if index.get("index_schema") != INDEX_SCHEMA:
        raise ContractError(
            "anchor index schema must be {}".format(INDEX_SCHEMA)
        )
    anchors = index.get("anchors") or []
    if not anchors:
        raise ContractError("anchor index declares no anchors")
    tags = [anchor.get("tag") for anchor in anchors]
    if len(tags) != len(set(tags)):
        raise ContractError("anchor index declares a duplicate tag")
    if index.get("baseline") not in tags:
        raise ContractError("anchor index baseline is not one of its anchors")
    return index


def _anchor_by_tag(index: Dict[str, Any], tag: str) -> Dict[str, Any]:
    """Return one anchor record, failing closed when it is absent."""
    for anchor in index["anchors"]:
        if anchor["tag"] == tag:
            return anchor
    raise ContractError("unknown anchor tag: {}".format(tag))


def build_manifest(
    repo_root: str, revision: SourceRevision, product_version: str
) -> Dict[str, Any]:
    """Build the manifest for one revision."""
    reader = SourceReader(repo_root, revision)
    surfaces = extract_surfaces(reader)
    return {
        "manifest_schema": MANIFEST_SCHEMA,
        "extractor": {"name": EXTRACTOR_NAME, "version": EXTRACTOR_VERSION},
        "product_version": product_version,
        "source": revision.as_dict(),
        "surfaces": surfaces,
        "surface_digests": surface_digests(surfaces),
        "contract_digest": contract_digest(surfaces),
    }


def _tag_product_version(tag: str) -> str:
    """Return the product version a ``v`` tag names."""
    if not tag.startswith("v"):
        raise ContractError("release tag must start with 'v': {}".format(tag))
    return tag[1:]


def _load_manifest(path: str, root: str) -> Dict[str, Any]:
    """Load and lightly validate a frozen manifest inside ``root``."""
    manifest = _read_json(path, root)
    if manifest.get("manifest_schema") != MANIFEST_SCHEMA:
        raise ContractError("{} has an unexpected manifest schema".format(path))
    missing = [name for name in SURFACES if name not in (manifest.get("surfaces") or {})]
    if missing:
        raise ContractError(
            "{} is missing contract surfaces: {}".format(path, ", ".join(missing))
        )
    return manifest


def verify_anchor(repo_root: str, anchor: Dict[str, Any], contracts_dir: str) -> Dict[str, Any]:
    """Prove one anchor still matches its manifest and its release tag."""
    tag = anchor["tag"]
    path = _manifest_path(contracts_dir, anchor["manifest"])
    try:
        resolved = validate_read_path(path, purpose="anchor manifest")
        resolved_root = os.path.realpath(contracts_dir)
        if os.path.commonpath([resolved_root, os.path.realpath(resolved)]) != resolved_root:
            raise ContractError(
                "anchor manifest escapes {}: {}".format(resolved_root, resolved)
            )
        with open(resolved, "rb") as handle:
            payload = handle.read()
    except FileNotFoundError:
        raise ContractError("missing file: {}".format(path))
    actual_file_digest = digest_of_bytes(payload)
    if actual_file_digest != anchor["manifest_sha256"]:
        raise ContractError(
            "{} was modified: manifest digest {} does not match the anchor record {}".format(
                anchor["manifest"], actual_file_digest, anchor["manifest_sha256"]
            )
        )
    manifest = _load_manifest(path, contracts_dir)
    recorded_digest = manifest.get("contract_digest")
    if recorded_digest != anchor["contract_digest"]:
        raise ContractError(
            "{} records contract digest {} but the anchor expects {}".format(
                anchor["manifest"], recorded_digest, anchor["contract_digest"]
            )
        )
    if manifest["source"]["commit"] != anchor["commit"]:
        raise ContractError(
            "{} records source commit {} but the anchor expects {}".format(
                anchor["manifest"], manifest["source"]["commit"], anchor["commit"]
            )
        )
    verify_tag_commit(repo_root, tag, anchor["commit"])
    extracted = build_manifest(
        repo_root, resolve_tag(repo_root, tag), manifest["product_version"]
    )
    if extracted["contract_digest"] != recorded_digest:
        raise ContractError(
            "re-extracting {} from tag {} produced digest {}, but the manifest records {}".format(
                tag, tag, extracted["contract_digest"], recorded_digest
            )
        )
    return {
        "tag": tag,
        "commit": anchor["commit"],
        "tag_object": anchor.get("tag_object"),
        "manifest": anchor["manifest"],
        "manifest_sha256": actual_file_digest,
        "contract_digest": recorded_digest,
        "surfaces_verified": sorted(SURFACES),
    }


def _historical_comparison(
    index: Dict[str, Any],
    record: Dict[str, Any],
    contracts_dir: str,
) -> Tuple[Dict[str, Any], List[str]]:
    """Audit one declared tag-to-tag comparison against its documented delta."""
    baseline_anchor = _anchor_by_tag(index, record["from"])
    candidate_anchor = _anchor_by_tag(index, record["to"])
    baseline = _load_manifest(
        _manifest_path(contracts_dir, baseline_anchor["manifest"]), contracts_dir
    )
    candidate = _load_manifest(
        _manifest_path(contracts_dir, candidate_anchor["manifest"]), contracts_dir
    )
    result = compare_contracts(
        baseline, candidate, record["from"], record["to"]
    )
    documented = set(record.get("documented_delta") or [])
    observed = {
        finding.finding_id
        for finding in result.findings
        if finding.verdict not in (OK, ADDITIVE)
    }
    problems: List[str] = []
    for finding_id in sorted(observed - documented):
        problems.append(
            "undocumented change between {} and {}: {}".format(
                record["from"], record["to"], finding_id
            )
        )
    for finding_id in sorted(documented - observed):
        problems.append(
            "documented delta {} no longer occurs between {} and {}".format(
                finding_id, record["from"], record["to"]
            )
        )
    report = result.as_dict()
    report["expectation"] = record.get("expect")
    report["documented_delta"] = sorted(documented)
    report["undocumented"] = sorted(observed - documented)
    report["problems"] = problems
    report["status"] = "DOCUMENTED" if not problems else "UNDOCUMENTED"
    return report, problems


def _candidate_revision(repo_root: str, candidate: str) -> SourceRevision:
    """Resolve the candidate revision token."""
    if candidate.upper() == WORKTREE_TOKEN:
        return resolve_worktree(repo_root)
    return resolve_commit(repo_root, candidate)


def _print_header(index_path: str) -> None:
    """Print the report header."""
    print("=== Cross-release compatibility contract check ===")
    print("  index: {}".format(os.path.relpath(index_path)))


def _print_anchor(record: Dict[str, Any]) -> None:
    """Print one verified anchor."""
    print(
        "  anchor {:<8} commit {}  digest {}".format(
            record["tag"], record["commit"][:12].ljust(12), record["contract_digest"]
        )
    )


def _print_comparison(report: Dict[str, Any], prefix: str) -> None:
    """Print one comparison summary."""
    counts = report["counts"]
    status = report.get("status") or report["verdict"]
    print(
        "  {}{} -> {}: {} (breaking {}, unverified {}, review {}, additive {}, documented {})".format(
            prefix,
            report["baseline"],
            report["candidate"],
            status,
            counts.get("BREAKING", 0),
            counts.get("UNVERIFIED", 0),
            counts.get("REVIEW_REQUIRED", 0),
            counts.get("ADDITIVE", 0),
            len(report.get("documented_delta") or []),
        )
    )


def _print_findings(report: Dict[str, Any], limit: int = 60) -> None:
    """Print every non-OK finding with its evidence."""
    shown = 0
    for finding in report["findings"]:
        if finding["verdict"] == OK:
            continue
        if shown >= limit:
            print("  ... additional findings suppressed; see the JSON report")
            break
        print("  [{}] {}".format(finding["verdict"], finding["id"]))
        print("      {}".format(finding["message"]))
        if "baseline" in finding:
            print("      baseline:  {}".format(json.dumps(finding["baseline"])))
        if "candidate" in finding:
            print("      candidate: {}".format(json.dumps(finding["candidate"])))
        shown += 1


def _verify_every_anchor(
    repo_root: str, index: Dict[str, Any], contracts_dir: str
) -> List[Dict[str, Any]]:
    """Verify each anchor and print its provenance."""
    records = [
        verify_anchor(repo_root, anchor, contracts_dir) for anchor in index["anchors"]
    ]
    for record in records:
        _print_anchor(record)
    return records


def _audit_historical(
    repo_root: str, index: Dict[str, Any], contracts_dir: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Audit every declared tag-to-tag comparison."""
    reports: List[Dict[str, Any]] = []
    failures: List[str] = []
    for record in index.get("comparisons") or []:
        report, problems = _historical_comparison(index, record, contracts_dir)
        reports.append(report)
        _print_comparison(
            report, "historical {}: ".format(record.get("expect", "contract_stable"))
        )
        for problem in problems:
            print("  problem: {}".format(problem))
        failures.extend(problems)
    return reports, failures


def _compare_candidate(
    repo_root: str,
    index: Dict[str, Any],
    contracts_dir: str,
    args: argparse.Namespace,
) -> Tuple[Dict[str, Any], List[str]]:
    """Compare the candidate and return its report and blocking findings."""
    baseline_anchor = _anchor_by_tag(index, index["baseline"])
    baseline = _load_manifest(
        _manifest_path(contracts_dir, baseline_anchor["manifest"]), contracts_dir
    )
    revision = _candidate_revision(repo_root, args.candidate)
    candidate = build_manifest(repo_root, revision, args.product_version or "candidate")
    comparison = compare_contracts(
        baseline,
        candidate,
        index["baseline"],
        "{}@{}".format(revision.label, revision.commit[:12]),
    )
    if args.approvals:
        approvals = _read_json(args.approvals, repo_root)
        for notice in apply_approvals(comparison, approvals, args.approvals):
            print("  notice: {}".format(notice))
    report = comparison.as_dict()
    report["candidate_commit"] = revision.commit
    report["candidate_surfaces"] = candidate["surface_digests"]
    report["baseline_surfaces"] = baseline["surface_digests"]
    _print_comparison(report, "candidate: ")
    return report, [finding.finding_id for finding in comparison.blocking()]


def _write_report(path: str, root: str, payload: Dict[str, Any]) -> None:
    """Write the machine-readable report inside the repository root."""
    target = validate_write_path_within_root(path, root, purpose="report")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print("  report: {}".format(path))


def check_command(args: argparse.Namespace) -> int:
    """Run the blocking compatibility check."""
    index_path = os.path.abspath(args.index)
    contracts_dir = _contracts_dir(index_path)
    repo_root = _repo_root(args, contracts_dir)
    index = load_index(index_path, repo_root)
    _print_header(index_path)
    anchors = _verify_every_anchor(repo_root, index, contracts_dir)
    historical, failures = _audit_historical(repo_root, index, contracts_dir)
    candidate_report, blocking = _compare_candidate(
        repo_root, index, contracts_dir, args
    )
    if blocking:
        print()
        print("Blocking findings:")
        _print_findings(candidate_report)
        failures.extend(blocking)
    if args.json_report:
        _write_report(
            args.json_report,
            repo_root,
            {
                "anchors": anchors,
                "historical": historical,
                "candidate": candidate_report,
                "verdict": candidate_report["verdict"],
                "failures": failures,
            },
        )
    if failures:
        print()
        print("FAIL: {} blocking compatibility problem(s)".format(len(failures)))
        return 1
    print()
    print("PASS: the candidate preserves every frozen contract surface")
    return 0


def verify_command(args: argparse.Namespace) -> int:
    """Verify the recorded anchors without comparing a candidate."""
    index_path = os.path.abspath(args.index)
    contracts_dir = _contracts_dir(index_path)
    repo_root = _repo_root(args, contracts_dir)
    index = load_index(index_path, repo_root)
    _print_header(index_path)
    for anchor in index["anchors"]:
        _print_anchor(verify_anchor(repo_root, anchor, contracts_dir))
    print()
    print("PASS: {} anchor(s) match their release tags".format(len(index["anchors"])))
    return 0


def extract_command(args: argparse.Namespace) -> int:
    """Render one revision's manifest."""
    repo_root = args.repo_root or repo_root_for(
        os.path.dirname(os.path.abspath(args.index)) if args.index else "."
    )
    if args.rev.upper() == WORKTREE_TOKEN:
        revision = resolve_worktree(repo_root)
        version = args.product_version or "worktree"
    else:
        revision = resolve_tag(repo_root, args.rev)
        version = args.product_version or _tag_product_version(args.rev)
    manifest = build_manifest(repo_root, revision, version)
    payload = dump_manifest(manifest)
    if args.out:
        target = validate_write_path_within_root(
            args.out, os.getcwd(), purpose="manifest"
        )
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(payload)
        print("wrote {} ({})".format(target, manifest["contract_digest"]))
    else:
        sys.stdout.write(payload)
    return 0


def freeze_command(args: argparse.Namespace) -> int:
    """Record a release tag as an immutable, checked-in anchor."""
    index_path = os.path.abspath(args.index)
    contracts_dir = _contracts_dir(index_path)
    repo_root = args.repo_root or repo_root_for(contracts_dir)
    revision = resolve_tag(repo_root, args.tag)
    manifest = build_manifest(repo_root, revision, _tag_product_version(args.tag))
    relative = "nginx-markdown-{}.json".format(args.tag[1:])
    target = _manifest_path(contracts_dir, relative)
    if os.path.exists(target) and not args.replace:
        existing = _load_manifest(target)
        if existing["source"]["commit"] != revision.commit:
            raise ContractError(
                "{} already anchors commit {}; pass --replace only after a "
                "maintainer decision to move the anchor".format(
                    relative, existing["source"]["commit"]
                )
            )
    os.makedirs(os.path.dirname(target), exist_ok=True)
    target = validate_write_path_within_root(
        target, contracts_dir, purpose="anchor manifest"
    )
    payload = dump_manifest(manifest)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(payload)
    record = {
        "tag": args.tag,
        "role": args.role,
        "commit": revision.commit,
        "tag_object": revision.tag_object,
        "manifest": relative,
        "manifest_sha256": digest_of_bytes(payload.encode("utf-8")),
        "contract_digest": manifest["contract_digest"],
    }
    print("wrote {}".format(target))
    print("add this anchor to {}:".format(INDEX_FILE))
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="tools.compatibility",
        description="Freeze and enforce the published compatibility contract.",
    )
    sub = parser.add_subparsers(dest="command")

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--index", default=DEFAULT_INDEX)
        target.add_argument("--repo-root", default="")

    check = sub.add_parser("check", help="verify anchors and compare a candidate")
    add_common(check)
    check.add_argument(
        "--candidate",
        default=WORKTREE_TOKEN,
        help="candidate revision (default: the working tree)",
    )
    check.add_argument(
        "--approvals",
        default="",
        help="reviewed approval file that waives exact REVIEW_REQUIRED findings",
    )
    check.add_argument("--json-report", default="")
    check.add_argument(
        "--product-version",
        default="",
        help="product version recorded for the candidate (metadata only)",
    )
    check.set_defaults(func=check_command)

    verify = sub.add_parser("verify-anchors", help="verify anchors only")
    add_common(verify)
    verify.set_defaults(func=verify_command)

    extract = sub.add_parser("extract", help="render one revision's manifest")
    add_common(extract)
    extract.add_argument("--rev", required=True)
    extract.add_argument("--product-version", default="")
    extract.add_argument("--out", default="")
    extract.set_defaults(func=extract_command)

    freeze = sub.add_parser("freeze", help="record a release tag as an anchor")
    add_common(freeze)
    freeze.add_argument("--tag", required=True)
    freeze.add_argument(
        "--role",
        required=True,
        choices=["breaking-freeze", "maintenance-baseline"],
    )
    freeze.add_argument("--replace", action="store_true")
    freeze.set_defaults(func=freeze_command)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Run the requested operation."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return 2
    try:
        return args.func(args)
    except ContractError as error:
        print("FAIL: {}".format(error), file=sys.stderr)
        return 1
    except ValueError as error:
        print("FAIL: {}".format(error), file=sys.stderr)
        return 1
