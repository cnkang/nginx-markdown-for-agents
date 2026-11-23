"""Compare two contract manifests under the 1.0 compatibility policy.

The policy has five verdicts.  ``BREAKING`` is a change to published stable
behaviour.  ``REVIEW_REQUIRED`` is a change the policy permits only with a
recorded maintainer decision, so a bare gate run fails closed on it.
``UNVERIFIED`` means the evidence needed to decide is missing, which is never
treated as a pass.  ``ADDITIVE`` and ``OK`` are compatible outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .model import digest_of

OK = "OK"
ADDITIVE = "ADDITIVE"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
BREAKING = "BREAKING"
UNVERIFIED = "UNVERIFIED"

BLOCKING_VERDICTS = (BREAKING, REVIEW_REQUIRED, UNVERIFIED)
SEVERITY_ORDER = {BREAKING: 0, UNVERIFIED: 1, REVIEW_REQUIRED: 2, ADDITIVE: 3, OK: 4}
# One suffix for every "this surface changed" finding id.
CHANGED_ID = "{}-changed"

# The diagnostics endpoint freezes exactly these top-level fields.  Additive
# state belongs in ``extensions``, so a new top-level field is a contract change
# rather than an additive extension.
DIAGNOSTICS_FROZEN_TOP_LEVEL = 7
DIAGNOSTICS_EXTENSION_FIELD = "extensions"


@dataclass
class Finding:
    """One comparison outcome for one contract fact."""

    finding_id: str
    surface: str
    verdict: str
    message: str
    baseline: Optional[Any] = None
    candidate: Optional[Any] = None

    @property
    def fingerprint(self) -> str:
        """Identify this finding by its id and both evidence values.

        An approval names this value, so the same finding id with different
        evidence cannot inherit an earlier decision.
        """
        return digest_of([self.finding_id, self.baseline, self.candidate])

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable finding."""
        record: Dict[str, Any] = {
            "id": self.finding_id,
            "fingerprint": self.fingerprint,
            "surface": self.surface,
            "verdict": self.verdict,
            "message": self.message,
        }
        if self.baseline is not None:
            record["baseline"] = self.baseline
        if self.candidate is not None:
            record["candidate"] = self.candidate
        return record


@dataclass
class Comparison:
    """The full comparison result."""

    baseline_label: str
    candidate_label: str
    findings: List[Finding] = field(default_factory=list)

    def add(
        self,
        surface: str,
        verdict: str,
        finding_id: str,
        message: str,
        baseline: Optional[Any] = None,
        candidate: Optional[Any] = None,
    ) -> None:
        """Record one finding."""
        self.findings.append(
            Finding(
                finding_id="{}:{}".format(surface, finding_id),
                surface=surface,
                verdict=verdict,
                message=message,
                baseline=baseline,
                candidate=candidate,
            )
        )

    def blocking(self) -> List[Finding]:
        """Return findings that fail a bare gate run."""
        return [item for item in self.findings if item.verdict in BLOCKING_VERDICTS]

    def counts(self) -> Dict[str, int]:
        """Count findings per verdict."""
        counts = dict.fromkeys(SEVERITY_ORDER, 0)
        for item in self.findings:
            counts[item.verdict] = counts.get(item.verdict, 0) + 1
        return counts

    def verdict(self) -> str:
        """Return the aggregate verdict for the candidate."""
        if any(item.verdict == BREAKING for item in self.findings):
            return BREAKING
        if any(item.verdict == UNVERIFIED for item in self.findings):
            return UNVERIFIED
        if any(item.verdict == REVIEW_REQUIRED for item in self.findings):
            return REVIEW_REQUIRED
        if any(item.verdict == ADDITIVE for item in self.findings):
            return ADDITIVE
        return OK

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable report."""
        ordered = sorted(
            self.findings,
            key=lambda item: (SEVERITY_ORDER.get(item.verdict, 9), item.finding_id),
        )
        return {
            "baseline": self.baseline_label,
            "candidate": self.candidate_label,
            "verdict": self.verdict(),
            "counts": self.counts(),
            "findings": [item.as_dict() for item in ordered],
        }


def _by_name(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index contract entries by their identity field."""
    return {entry["name"]: entry for entry in entries}


def _compare_directive(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare one directive that exists on both sides."""
    if old["handler"] != new["handler"]:
        result.add(
            "directives",
            BREAKING,
            "handler-changed:{}".format(name),
            "directive {} changed handler, which changes whether it is active".format(name),
            old["handler"],
            new["handler"],
        )
    if old["args"] != new["args"]:
        result.add(
            "directives",
            BREAKING,
            "args-changed:{}".format(name),
            "directive {} changed its argument contract".format(name),
            old["args"],
            new["args"],
        )
    old_context = set(old["context"])
    new_context = set(new["context"])
    if new_context - old_context:
        result.add(
            "directives",
            ADDITIVE,
            "context-widened:{}".format(name),
            "directive {} accepts an additional context".format(name),
            sorted(old_context),
            sorted(new_context),
        )
    if old_context - new_context:
        result.add(
            "directives",
            BREAKING,
            "context-narrowed:{}".format(name),
            "directive {} no longer accepts a context it used to accept".format(name),
            sorted(old_context),
            sorted(new_context),
        )
    if old["default"] != new["default"]:
        result.add(
            "directives",
            BREAKING,
            "default-changed:{}".format(name),
            "directive {} changed its documented default".format(name),
            old["default"],
            new["default"],
        )
    if old["syntax"] != new["syntax"]:
        result.add(
            "directives",
            BREAKING,
            "syntax-changed:{}".format(name),
            "directive {} changed its accepted value syntax".format(name),
            old["syntax"],
            new["syntax"],
        )
    if old["documented_context"] != new["documented_context"]:
        result.add(
            "directives",
            REVIEW_REQUIRED,
            "documented-context-changed:{}".format(name),
            "directive {} documentation changed its context list".format(name),
            old["documented_context"],
            new["documented_context"],
        )
    if old["documentation"] != new["documentation"]:
        result.add(
            "directives",
            REVIEW_REQUIRED,
            "documentation-changed:{}".format(name),
            "directive {} documentation text changed".format(name),
            old["documentation"],
            new["documentation"],
        )


def compare_directives(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the directive command-table contract."""
    old_map = _by_name(baseline["entries"])
    new_map = _by_name(candidate["entries"])
    for name in sorted(set(old_map) - set(new_map)):
        result.add(
            "directives",
            BREAKING,
            "removed:{}".format(name),
            "stable directive {} is no longer registered".format(name),
            old_map[name]["syntax"],
            None,
        )
    for name in sorted(set(new_map) - set(old_map)):
        result.add(
            "directives",
            ADDITIVE,
            "added:{}".format(name),
            "new directive {} is registered".format(name),
            None,
            new_map[name]["syntax"],
        )
    for name in sorted(set(old_map) & set(new_map)):
        _compare_directive(result, name, old_map[name], new_map[name])
    if candidate["reject_only_count"] > baseline["reject_only_count"]:
        result.add(
            "directives",
            BREAKING,
            "reject-only-added",
            "the command table gained a reject-only entry",
            baseline["reject_only_count"],
            candidate["reject_only_count"],
        )


def _compare_metric_family(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare one declared metric family."""
    if old["type"] != new["type"]:
        result.add(
            "metrics",
            BREAKING,
            "type-changed:{}".format(name),
            "metric family {} changed its Prometheus type".format(name),
            old["type"],
            new["type"],
        )
    if old.get("bucket_boundaries") != new.get("bucket_boundaries"):
        result.add(
            "metrics",
            BREAKING,
            "buckets-changed:{}".format(name),
            "metric family {} changed its histogram buckets".format(name),
            old.get("bucket_boundaries"),
            new.get("bucket_boundaries"),
        )
    old_labels = _by_name(old["labels"])
    new_labels = _by_name(new["labels"])
    for label in sorted(set(old_labels) - set(new_labels)):
        result.add(
            "metrics",
            BREAKING,
            "label-removed:{}:{}".format(name, label),
            "metric family {} dropped label {}".format(name, label),
            label,
            None,
        )
    for label in sorted(set(new_labels) - set(old_labels)):
        result.add(
            "metrics",
            BREAKING,
            "label-added:{}:{}".format(name, label),
            "metric family {} gained label {}, which changes series identity".format(
                name, label
            ),
            None,
            label,
        )
    for label in sorted(set(old_labels) & set(new_labels)):
        old_values = set(old_labels[label]["values"])
        new_values = set(new_labels[label]["values"])
        if old_labels[label]["dynamic"] != new_labels[label]["dynamic"]:
            result.add(
                "metrics",
                BREAKING,
                "label-dynamic-changed:{}:{}".format(name, label),
                "label {} of {} changed between a bounded allowlist and a dynamic value".format(
                    label, name
                ),
                old_labels[label]["dynamic"],
                new_labels[label]["dynamic"],
            )
        if old_values - new_values:
            result.add(
                "metrics",
                BREAKING,
                "label-value-removed:{}:{}".format(name, label),
                "label {} of {} lost a stable value".format(label, name),
                sorted(old_values - new_values),
                None,
            )
        if new_values - old_values:
            result.add(
                "metrics",
                ADDITIVE,
                "label-value-added:{}:{}".format(name, label),
                "label {} of {} gained a value".format(label, name),
                None,
                sorted(new_values - old_values),
            )


def _compare_emitted(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the renderer's emitted families, label sets, and series."""
    old = baseline["emitted"]
    new = candidate["emitted"]
    for name in sorted(set(old["families"]) - set(new["families"])):
        result.add(
            "metrics",
            BREAKING,
            "emitted-family-removed:{}".format(name),
            "the renderer no longer emits family {}".format(name),
            old["families"][name],
            None,
        )
    for name in sorted(set(new["families"]) - set(old["families"])):
        result.add(
            "metrics",
            ADDITIVE,
            "emitted-family-added:{}".format(name),
            "the renderer emits a new family {}".format(name),
            None,
            new["families"][name],
        )
    for name in sorted(set(old["families"]) & set(new["families"])):
        if old["families"][name] != new["families"][name]:
            result.add(
                "metrics",
                BREAKING,
                "emitted-type-changed:{}".format(name),
                "the renderer changed the emitted type of {}".format(name),
                old["families"][name],
                new["families"][name],
            )
        if old["label_sets"].get(name) != new["label_sets"].get(name):
            result.add(
                "metrics",
                BREAKING,
                "emitted-labels-changed:{}".format(name),
                "the renderer changed the label set of {}".format(name),
                old["label_sets"].get(name),
                new["label_sets"].get(name),
            )
        old_series = {tuple(item) for item in old["series"].get(name, [])}
        new_series = {tuple(item) for item in new["series"].get(name, [])}
        for removed in sorted(old_series - new_series):
            result.add(
                "metrics",
                BREAKING,
                "emitted-series-removed:{}:{}".format(name, ",".join(removed)),
                "the renderer stopped emitting a series of {}".format(name),
                list(removed),
                None,
            )
        for added in sorted(new_series - old_series):
            result.add(
                "metrics",
                ADDITIVE,
                "emitted-series-added:{}:{}".format(name, ",".join(added)),
                "the renderer emits a new series of {}".format(name),
                None,
                list(added),
            )


def compare_metrics(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the declared metrics registry and the emitted wire set."""
    old_schema = baseline.get("registry_schema_version")
    new_schema = candidate.get("registry_schema_version")
    if old_schema != new_schema:
        verdict = BREAKING if (new_schema or 0) < (old_schema or 0) else REVIEW_REQUIRED
        result.add(
            "metrics",
            verdict,
            "registry-schema-version-changed",
            "the metrics registry content schema version changed",
            old_schema,
            new_schema,
        )
    if baseline.get("format") != candidate.get("format"):
        result.add(
            "metrics",
            BREAKING,
            "wire-format-changed",
            "the Prometheus wire format changed",
            baseline.get("format"),
            candidate.get("format"),
        )
    for key in ("family_prefix", "max_histogram_buckets"):
        if baseline.get(key) != candidate.get(key):
            result.add(
                "metrics",
                BREAKING,
                CHANGED_ID.format(key.replace("_", "-")),
                "the metrics registry {} changed".format(key),
                baseline.get(key),
                candidate.get(key),
            )
    if set(baseline.get("forbidden_labels") or []) - set(candidate.get("forbidden_labels") or []):
        result.add(
            "metrics",
            BREAKING,
            "forbidden-labels-narrowed",
            "a label dimension that must stay absent is no longer forbidden",
            baseline.get("forbidden_labels"),
            candidate.get("forbidden_labels"),
        )
    old_map = _by_name(baseline["families"])
    new_map = _by_name(candidate["families"])
    for name in sorted(set(old_map) - set(new_map)):
        result.add(
            "metrics",
            BREAKING,
            "declared-family-removed:{}".format(name),
            "the registry no longer declares family {}".format(name),
            old_map[name]["type"],
            None,
        )
    for name in sorted(set(new_map) - set(old_map)):
        result.add(
            "metrics",
            ADDITIVE,
            "declared-family-added:{}".format(name),
            "the registry declares a new family {}".format(name),
            None,
            new_map[name]["type"],
        )
    for name in sorted(set(old_map) & set(new_map)):
        _compare_metric_family(result, name, old_map[name], new_map[name])
    _compare_emitted(result, baseline, candidate)
    _compare_unlabelled_samples(result, baseline, candidate)


def _compare_reason(
    result: Comparison, key: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare one reason-code entry that exists on both sides."""
    if old["discriminant"] != new["discriminant"]:
        result.add(
            "reasons",
            BREAKING,
            "discriminant-changed:{}".format(key),
            "reason {} changed its numeric discriminant".format(key),
            old["discriminant"],
            new["discriminant"],
        )
    for field_name in ("stage", "outcome", "default_origin", "operator_visible"):
        if old[field_name] != new[field_name]:
            result.add(
                "reasons",
                BREAKING,
                "{}-changed:{}".format(field_name.replace("_", "-"), key),
                "reason {} changed its {}".format(key, field_name),
                old[field_name],
                new[field_name],
            )
    old_origins = set(old["allowed_origins"])
    new_origins = set(new["allowed_origins"])
    if old_origins - new_origins:
        result.add(
            "reasons",
            BREAKING,
            "origins-removed:{}".format(key),
            "reason {} no longer allows an origin it used to allow".format(key),
            sorted(old_origins - new_origins),
            None,
        )
    if new_origins - old_origins:
        result.add(
            "reasons",
            ADDITIVE,
            "origins-added:{}".format(key),
            "reason {} allows an additional origin".format(key),
            None,
            sorted(new_origins - old_origins),
        )


def compare_reasons(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the declarative reason registry."""
    old_map = {entry["key"]: entry for entry in baseline["entries"]}
    new_map = {entry["key"]: entry for entry in candidate["entries"]}
    for key in sorted(set(old_map) - set(new_map)):
        result.add(
            "reasons",
            BREAKING,
            "removed:{}".format(key),
            "reason {} was removed from the registry".format(key),
            old_map[key]["discriminant"],
            None,
        )
    for key in sorted(set(new_map) - set(old_map)):
        result.add(
            "reasons",
            ADDITIVE,
            "added:{}".format(key),
            "reason {} was added to the registry".format(key),
            None,
            new_map[key]["discriminant"],
        )
    for key in sorted(set(old_map) & set(new_map)):
        _compare_reason(result, key, old_map[key], new_map[key])


def _compare_field_type(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare the declared type of one diagnostics field."""
    if old["type"] != new["type"]:
        result.add(
            "diagnostics",
            BREAKING,
            "type-changed:{}".format(name),
            "diagnostics field {} changed type".format(name),
            old["type"],
            new["type"],
        )


def _compare_field_nullability(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare the nullability of one diagnostics field."""
    if old["nullable"] and not new["nullable"]:
        result.add(
            "diagnostics",
            BREAKING,
            "nullability-narrowed:{}".format(name),
            "diagnostics field {} no longer permits null".format(name),
            old["nullable"],
            new["nullable"],
        )
    if not old["nullable"] and new["nullable"]:
        result.add(
            "diagnostics",
            REVIEW_REQUIRED,
            "nullability-widened:{}".format(name),
            "diagnostics field {} now permits null".format(name),
            old["nullable"],
            new["nullable"],
        )


def _compare_field_enum(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare the allowed values of one diagnostics field.

    A field without an ``enum`` accepts any value, so declaring one narrows the
    contract and removing one widens it.  Comparing set differences alone would
    invert both cases.
    """
    old_declares = "enum" in old
    new_declares = "enum" in new
    if not old_declares and new_declares:
        result.add(
            "diagnostics",
            BREAKING,
            "enum-added:{}".format(name),
            "diagnostics field {} gained a closed value set".format(name),
            None,
            sorted(new.get("enum") or []),
        )
        return
    if old_declares and not new_declares:
        result.add(
            "diagnostics",
            ADDITIVE,
            "enum-removed:{}".format(name),
            "diagnostics field {} no longer restricts its values".format(name),
            sorted(old.get("enum") or []),
            None,
        )
        return
    old_enum = set(old.get("enum") or [])
    new_enum = set(new.get("enum") or [])
    if old_enum - new_enum:
        result.add(
            "diagnostics",
            BREAKING,
            "enum-narrowed:{}".format(name),
            "diagnostics field {} lost an allowed value".format(name),
            sorted(old_enum - new_enum),
            None,
        )
    if new_enum - old_enum:
        result.add(
            "diagnostics",
            ADDITIVE,
            "enum-widened:{}".format(name),
            "diagnostics field {} gained an allowed value".format(name),
            None,
            sorted(new_enum - old_enum),
        )


def _compare_field_closed_object(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Reject an object field that stopped refusing unknown properties."""
    if old.get("additional_properties") is False and (
        new.get("additional_properties") is not False
    ):
        result.add(
            "diagnostics",
            BREAKING,
            "additional-properties-weakened:{}".format(name),
            "diagnostics object {} stopped rejecting unknown properties".format(name),
            old.get("additional_properties"),
            new.get("additional_properties"),
        )


def _compare_field_required_children(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare the required child fields of one diagnostics object."""
    old_list = old.get("required_children") or []
    new_list = new.get("required_children") or []
    old_children = set(old_list)
    new_children = set(new_list)
    if old_children - new_children:
        result.add(
            "diagnostics",
            BREAKING,
            "required-child-removed:{}".format(name),
            "diagnostics object {} no longer requires a field".format(name),
            sorted(old_children - new_children),
            None,
        )
    if new_children - old_children:
        result.add(
            "diagnostics",
            REVIEW_REQUIRED,
            "required-child-added:{}".format(name),
            "diagnostics object {} requires an additional field".format(name),
            None,
            sorted(new_children - old_children),
        )


def _compare_properties(
    result: Comparison, name: str, old: List[Dict[str, Any]], new: List[Dict[str, Any]]
) -> None:
    """Compare a projected child field set, including shared field types."""
    old_children = _by_name(old)
    new_children = _by_name(new)
    for child in sorted(set(old_children) - set(new_children)):
        result.add(
            "diagnostics",
            BREAKING,
            "child-removed:{}:{}".format(name, child),
            "diagnostics object {} dropped field {}".format(name, child),
            old_children[child]["type"],
            None,
        )
    for child in sorted(set(new_children) - set(old_children)):
        result.add(
            "diagnostics",
            ADDITIVE,
            "child-added:{}:{}".format(name, child),
            "diagnostics object {} added field {}".format(name, child),
            None,
            new_children[child]["type"],
        )
    for child in sorted(set(old_children) & set(new_children)):
        if old_children[child]["type"] != new_children[child]["type"]:
            result.add(
                "diagnostics",
                BREAKING,
                "child-type-changed:{}:{}".format(name, child),
                "diagnostics object {} changed the type of field {}".format(name, child),
                old_children[child]["type"],
                new_children[child]["type"],
            )
        old_enum = set(old_children[child].get("enum") or [])
        new_enum = set(new_children[child].get("enum") or [])
        if old_children[child].get("enum") is not None and (
            new_children[child].get("enum") is None
        ):
            result.add(
                "diagnostics",
                ADDITIVE,
                "child-enum-removed:{}:{}".format(name, child),
                "diagnostics field {} no longer restricts {}".format(name, child),
                sorted(old_enum),
                None,
            )
        elif new_children[child].get("enum") is not None and (
            old_children[child].get("enum") is None
        ):
            result.add(
                "diagnostics",
                BREAKING,
                "child-enum-added:{}:{}".format(name, child),
                "diagnostics field {} of {} gained a closed value set".format(name, child),
                None,
                sorted(new_enum),
            )
        elif old_enum - new_enum:
            result.add(
                "diagnostics",
                BREAKING,
                "child-enum-narrowed:{}:{}".format(name, child),
                "diagnostics field {} of {} lost an allowed value".format(name, child),
                sorted(old_enum - new_enum),
                None,
            )
        elif new_enum - old_enum:
            result.add(
                "diagnostics",
                ADDITIVE,
                "child-enum-widened:{}:{}".format(name, child),
                "diagnostics field {} of {} gained an allowed value".format(name, child),
                None,
                sorted(new_enum - old_enum),
            )


def _compare_field_items(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare the item schema of an array field."""
    if old.get("items_type") != new.get("items_type"):
        result.add(
            "diagnostics",
            BREAKING,
            "items-type-changed:{}".format(name),
            "diagnostics array {} changed its item type".format(name),
            old.get("items_type"),
            new.get("items_type"),
        )
    old_required = set(old.get("items_required") or [])
    new_required = set(new.get("items_required") or [])
    if old_required - new_required:
        result.add(
            "diagnostics",
            BREAKING,
            "items-required-removed:{}".format(name),
            "diagnostics array {} no longer requires an item field".format(name),
            sorted(old_required - new_required),
            None,
        )
    if new_required - old_required:
        result.add(
            "diagnostics",
            REVIEW_REQUIRED,
            "items-required-added:{}".format(name),
            "diagnostics array {} requires an additional item field".format(name),
            None,
            sorted(new_required - old_required),
        )
    if (
        old.get("items_additional_properties") is False
        and new.get("items_additional_properties") is not False
    ):
        result.add(
            "diagnostics",
            BREAKING,
            "items-additional-properties-weakened:{}".format(name),
            "diagnostics array items for {} stopped rejecting unknown fields".format(name),
            old.get("items_additional_properties"),
            new.get("items_additional_properties"),
        )
    _compare_properties(
        result,
        name + "[]",
        old.get("items_properties") or [],
        new.get("items_properties") or [],
    )


def _compare_diagnostics_field(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare one diagnostics schema property."""
    _compare_field_type(result, name, old, new)
    _compare_field_nullability(result, name, old, new)
    _compare_field_enum(result, name, old, new)
    if "items_type" in old or "items_type" in new:
        _compare_field_items(result, name, old, new)
    if "required_children" not in old and "required_children" not in new:
        return
    _compare_field_closed_object(result, name, old, new)
    _compare_field_required_children(result, name, old, new)
    if old.get("properties") is not None and new.get("properties") is not None:
        _compare_properties(result, name, old["properties"], new["properties"])


def _compare_diagnostics_header(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the schema version, the method set, and the read-only flag."""
    old_version = baseline.get("schema_version")
    new_version = candidate.get("schema_version")
    if old_version != new_version:
        verdict = BREAKING if (new_version or 0) < (old_version or 0) else REVIEW_REQUIRED
        result.add(
            "diagnostics",
            verdict,
            "schema-version-changed",
            "the diagnostics schema version changed",
            old_version,
            new_version,
        )
    if baseline.get("methods") != candidate.get("methods"):
        result.add(
            "diagnostics",
            BREAKING,
            "method-set-changed",
            "the diagnostics endpoint changed its accepted methods",
            baseline.get("methods"),
            candidate.get("methods"),
        )
    if baseline.get("read_only") and candidate.get("read_only") is not True:
        result.add(
            "diagnostics",
            BREAKING,
            "read-only-contract-weakened",
            "the diagnostics endpoint is no longer declared read-only",
            baseline.get("read_only"),
            candidate.get("read_only"),
        )


def _compare_diagnostics_top_level(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the required top-level field set and the additive extension."""
    old_fields = set(baseline["required"])
    new_fields = set(candidate["required"])
    if old_fields - new_fields:
        result.add(
            "diagnostics",
            BREAKING,
            "required-removed",
            "top-level diagnostics fields are no longer required",
            sorted(old_fields - new_fields),
            None,
        )
    for name in sorted(new_fields - old_fields):
        result.add(
            "diagnostics",
            REVIEW_REQUIRED,
            "required-added:{}".format(name),
            "diagnostics field {} became required".format(name),
            None,
            name,
        )
    added = sorted(
        {field["name"] for field in candidate["fields"]}
        - {field["name"] for field in baseline["fields"]}
    )
    for name in added:
        verdict = ADDITIVE if name == DIAGNOSTICS_EXTENSION_FIELD else BREAKING
        result.add(
            "diagnostics",
            verdict,
            "top-level-field-added:{}".format(name),
            "diagnostics added top-level field {}; the frozen contract keeps additive state in extensions".format(
                name
            ),
            None,
            name,
        )
    if (
        candidate["additional_properties"] is not False
        and baseline["additional_properties"] is False
    ):
        result.add(
            "diagnostics",
            BREAKING,
            "root-additional-properties-weakened",
            "the diagnostics root stopped rejecting unknown properties",
            baseline["additional_properties"],
            candidate["additional_properties"],
        )


def compare_diagnostics(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the diagnostics wire schema."""
    _compare_diagnostics_header(result, baseline, candidate)
    _compare_diagnostics_top_level(result, baseline, candidate)
    old_map = _by_name(baseline["fields"])
    new_map = _by_name(candidate["fields"])
    for name in sorted(set(old_map) - set(new_map)):
        result.add(
            "diagnostics",
            BREAKING,
            "field-removed:{}".format(name),
            "diagnostics field {} was removed".format(name),
            old_map[name]["type"],
            None,
        )
    for name in sorted(set(old_map) & set(new_map)):
        _compare_diagnostics_field(result, name, old_map[name], new_map[name])


def _compare_unlabelled_samples(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the families the renderer emits without any label block."""
    old_samples = set(baseline["emitted"].get("unlabelled_samples") or [])
    new_samples = set(candidate["emitted"].get("unlabelled_samples") or [])
    for name in sorted(old_samples - new_samples):
        result.add(
            "metrics",
            BREAKING,
            "unlabelled-sample-removed:{}".format(name),
            "the renderer stopped emitting the unlabelled sample of {}".format(name),
            name,
            None,
        )
    for name in sorted(new_samples - old_samples):
        result.add(
            "metrics",
            ADDITIVE,
            "unlabelled-sample-added:{}".format(name),
            "the renderer emits an unlabelled sample of {}".format(name),
            None,
            name,
        )


def compare_http(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the behavioural HTTP contract binding."""
    old_behaviours = baseline["behaviours"]
    new_behaviours = candidate["behaviours"]
    for name in sorted(set(old_behaviours) - set(new_behaviours)):
        result.add(
            "http",
            BREAKING,
            "behaviour-removed:{}".format(name),
            "HTTP behaviour {} is no longer bound to a contract test".format(name),
            old_behaviours[name]["tests"],
            None,
        )
    for name in sorted(set(new_behaviours) - set(old_behaviours)):
        result.add(
            "http",
            REVIEW_REQUIRED,
            "behaviour-added:{}".format(name),
            "HTTP behaviour {} was added to the contract".format(name),
            None,
            new_behaviours[name]["tests"],
        )
    for name in sorted(set(old_behaviours) & set(new_behaviours)):
        if old_behaviours[name]["tests"] != new_behaviours[name]["tests"]:
            result.add(
                "http",
                REVIEW_REQUIRED,
                "binding-changed:{}".format(name),
                "the test binding for HTTP behaviour {} changed".format(name),
                old_behaviours[name]["tests"],
                new_behaviours[name]["tests"],
            )
        if old_behaviours[name]["executed_by"] != new_behaviours[name]["executed_by"]:
            result.add(
                "http",
                REVIEW_REQUIRED,
                "executor-changed:{}".format(name),
                "the executing target for HTTP behaviour {} changed".format(name),
                old_behaviours[name]["executed_by"],
                new_behaviours[name]["executed_by"],
            )
    old_digests = baseline["test_file_digests"]
    new_digests = candidate["test_file_digests"]
    for path in sorted(set(old_digests) & set(new_digests)):
        if old_digests[path] != new_digests[path]:
            result.add(
                "http",
                REVIEW_REQUIRED,
                "test-changed:{}".format(path),
                "HTTP contract test {} changed; the behaviour it asserts needs review".format(
                    path
                ),
                old_digests[path],
                new_digests[path],
            )


SUPPORT_TIER_RANK = {"experimental": 0, "best-effort": 1, "supported": 2}


def _support_groups(contract: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Group matrix rows by the target the comparison reports on.

    Every row keeps its own tier, so a group can report more than one value.
    The group takes the highest tier and the blocking flag of any member, which
    makes the comparison independent of row order and of patch progression.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for row in contract["support"]["targets"]:
        key = "{}/{}/{}/{}".format(
            row["nginx_minor"], row["os"], row["libc"], row["arch"]
        )
        bucket = groups.setdefault(
            key,
            {
                "nginx_minor": row["nginx_minor"],
                "os": row["os"],
                "libc": row["libc"],
                "arch": row["arch"],
                "tiers": set(),
                "blocking": False,
                "patches": [],
            },
        )
        bucket["tiers"].add(row["support_tier"])
        bucket["blocking"] = bucket["blocking"] or row["release_blocking"]
        bucket["patches"].append(row["nginx_version"])
    for bucket in groups.values():
        bucket["patches"].sort()
        bucket["tiers"] = sorted(
            bucket["tiers"], key=lambda tier: SUPPORT_TIER_RANK.get(tier, -1)
        )
    return groups


def _support_row_key(row: Dict[str, Any]) -> str:
    """Identify one matrix row by its exact target and patch version."""
    return "{}|{}/{}/{}".format(
        row["nginx_version"], row["os"], row["libc"], row["arch"]
    )


def _compare_support_rows(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the retained matrix rows one by one.

    A group aggregate would hide a demotion on one row whenever a sibling row
    keeps the higher tier, so each retained row is compared on its own.
    """
    old_rows = {
        _support_row_key(row): row for row in baseline["support"]["targets"]
    }
    new_rows = {
        _support_row_key(row): row for row in candidate["support"]["targets"]
    }
    for key in sorted(set(old_rows) & set(new_rows)):
        old_row = old_rows[key]
        new_row = new_rows[key]
        if old_row["support_tier"] != new_row["support_tier"]:
            old_rank = SUPPORT_TIER_RANK.get(old_row["support_tier"], -1)
            new_rank = SUPPORT_TIER_RANK.get(new_row["support_tier"], -1)
            verdict = ADDITIVE if new_rank > old_rank else BREAKING
            result.add(
                "packaging",
                verdict,
                "row-tier-changed:{}".format(key),
                "support tier changed for row {} from {} to {}".format(
                    key, old_row["support_tier"], new_row["support_tier"]
                ),
                old_row["support_tier"],
                new_row["support_tier"],
            )
        if old_row["release_blocking"] and not new_row["release_blocking"]:
            result.add(
                "packaging",
                BREAKING,
                "row-release-blocking-dropped:{}".format(key),
                "row {} stopped being release blocking".format(key),
                old_row["release_blocking"],
                new_row["release_blocking"],
            )
        if not old_row["release_blocking"] and new_row["release_blocking"]:
            result.add(
                "packaging",
                ADDITIVE,
                "row-release-blocking-added:{}".format(key),
                "row {} became release blocking".format(key),
                old_row["release_blocking"],
                new_row["release_blocking"],
            )


def _compare_support_target(
    result: Comparison, key: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Report patch progression for one supported target group."""
    if old["patches"] != new["patches"]:
        result.add(
            "packaging",
            OK,
            "patch-progression:{}".format(key),
            "{} tracks patch versions {} -> {}".format(
                key, old["patches"], new["patches"]
            ),
            old["patches"],
            new["patches"],
        )


def _compare_install_paths(
    result: Comparison, key: str, old: List[str], new: List[str]
) -> None:
    """Compare one family's effective install path list."""
    for path in sorted(set(old) - set(new)):
        result.add(
            "packaging",
            BREAKING,
            "{}-path-removed:{}".format(key, path),
            "the {} package no longer installs {}".format(key, path),
            path,
            None,
        )
    for path in sorted(set(new) - set(old)):
        result.add(
            "packaging",
            ADDITIVE,
            "{}-path-added:{}".format(key, path),
            "the {} package installs {}".format(key, path),
            None,
            path,
        )


def compare_packaging(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare package identity, install layout, and support tiers."""
    if baseline["package_name"] != candidate["package_name"]:
        result.add(
            "packaging",
            BREAKING,
            "package-name-changed",
            "the package name changed",
            baseline["package_name"],
            candidate["package_name"],
        )
    for key in ("deb_module_path", "rpm_module_path"):
        if baseline[key] != candidate[key]:
            result.add(
                "packaging",
                BREAKING,
                CHANGED_ID.format(key.replace("_", "-")),
                "the installed module path changed for {}".format(key),
                baseline[key],
                candidate[key],
            )
    for key in ("load_snippet_paths", "deb_nginx_constraints", "rpm_nginx_constraints"):
        if baseline[key] != candidate[key]:
            result.add(
                "packaging",
                REVIEW_REQUIRED,
                CHANGED_ID.format(key.replace("_", "-")),
                "the packaging {} contract changed".format(key),
                baseline[key],
                candidate[key],
            )
    _compare_install_paths(
        result, "deb", baseline.get("deb_install_paths") or [], candidate.get("deb_install_paths") or []
    )
    _compare_install_paths(
        result, "rpm", baseline.get("rpm_install_paths") or [], candidate.get("rpm_install_paths") or []
    )
    old_groups = _support_groups(baseline)
    new_groups = _support_groups(candidate)
    for key in sorted(set(old_groups) - set(new_groups)):
        result.add(
            "packaging",
            BREAKING,
            "support-target-removed:{}".format(key),
            "supported target {} was removed from the matrix".format(key),
            old_groups[key]["tiers"],
            None,
        )
    for key in sorted(set(new_groups) - set(old_groups)):
        result.add(
            "packaging",
            ADDITIVE,
            "support-target-added:{}".format(key),
            "supported target {} was added to the matrix".format(key),
            None,
            new_groups[key]["tiers"],
        )
    for key in sorted(set(old_groups) & set(new_groups)):
        _compare_support_target(result, key, old_groups[key], new_groups[key])
    _compare_support_rows(result, baseline, candidate)


def compare_ffi(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the bundled internal FFI boundary."""
    if baseline["abi_version"] != candidate["abi_version"]:
        result.add(
            "ffi",
            REVIEW_REQUIRED,
            "abi-version-changed",
            "the bundled internal ABI version changed; the release artifact must move with it",
            baseline["abi_version"],
            candidate["abi_version"],
        )
    old_map = _by_name(baseline["symbols"])
    new_map = _by_name(candidate["symbols"])
    for name in sorted(set(old_map) - set(new_map)):
        result.add(
            "ffi",
            REVIEW_REQUIRED,
            "symbol-removed:{}".format(name),
            "FFI export {} was removed from the generated header".format(name),
            old_map[name]["signature"],
            None,
        )
    for name in sorted(set(new_map) - set(old_map)):
        result.add(
            "ffi",
            ADDITIVE,
            "symbol-added:{}".format(name),
            "FFI export {} was added to the generated header".format(name),
            None,
            new_map[name]["signature"],
        )
    for name in sorted(set(old_map) & set(new_map)):
        if old_map[name]["signature"] != new_map[name]["signature"]:
            result.add(
                "ffi",
                REVIEW_REQUIRED,
                "signature-changed:{}".format(name),
                "FFI export {} changed signature".format(name),
                old_map[name]["signature"],
                new_map[name]["signature"],
            )
    if baseline["handshake_symbols"] != candidate["handshake_symbols"]:
        result.add(
            "ffi",
            REVIEW_REQUIRED,
            "handshake-changed",
            "the ABI handshake export set changed",
            baseline["handshake_symbols"],
            candidate["handshake_symbols"],
        )


COMPARATORS = {
    "directives": compare_directives,
    "metrics": compare_metrics,
    "reasons": compare_reasons,
    "diagnostics": compare_diagnostics,
    "http": compare_http,
    "packaging": compare_packaging,
    "ffi": compare_ffi,
}


def compare_contracts(
    baseline: Dict[str, Any],
    candidate: Dict[str, Any],
    baseline_label: str,
    candidate_label: str,
) -> Comparison:
    """Compare two manifests surface by surface."""
    result = Comparison(baseline_label=baseline_label, candidate_label=candidate_label)
    old_surfaces = baseline.get("surfaces") or {}
    new_surfaces = candidate.get("surfaces") or {}
    for surface in sorted(COMPARATORS):
        if surface not in old_surfaces or surface not in new_surfaces:
            missing = "baseline" if surface not in old_surfaces else "candidate"
            result.add(
                surface,
                UNVERIFIED,
                "surface-missing",
                "the {} manifest has no {} surface".format(missing, surface),
                None,
                None,
            )
            continue
        COMPARATORS[surface](result, old_surfaces[surface], new_surfaces[surface])
    return result


_APPROVAL_FIELDS = (
    "finding_id",
    "evidence_fingerprint",
    "reason",
    "approved_by",
    "approved_at",
)


def _approval_key(entry: Dict[str, Any], approval_path: str) -> tuple:
    """Validate one approval and return its matching key."""
    for field in _APPROVAL_FIELDS:
        value = entry.get(field)
        if not value:
            raise ValueError(
                "approval in {} is missing {}".format(approval_path, field)
            )
        if not isinstance(value, str):
            raise ValueError(
                "approval in {} has a non-string {}".format(approval_path, field)
            )
    return (entry["finding_id"], entry["evidence_fingerprint"])


def apply_approvals(
    result: Comparison, approvals: Dict[str, Any], approval_path: str
) -> List[str]:
    """Convert approved ``REVIEW_REQUIRED`` findings to ``OK``.

    Every approval must name an exact finding id, a reviewer, and a reason.
    A bare gate run passes no approvals at all, so this path cannot be reached
    by accident.
    """
    notices: List[str] = []
    entries = approvals.get("exceptions") or []
    require_shape = approvals.get("schema") == 1
    if not require_shape:
        raise ValueError("approval file must declare \"schema\": 1")
    approved: Dict[tuple, Dict[str, Any]] = {}
    for entry in entries:
        approved[_approval_key(entry, approval_path)] = entry
    used = set()
    for finding in result.findings:
        if finding.verdict != REVIEW_REQUIRED:
            continue
        key = (finding.finding_id, finding.fingerprint)
        entry = approved.get(key)
        if entry is None:
            continue
        finding.verdict = OK
        finding.message = "{} [approved by {}: {}]".format(
            finding.message, entry["approved_by"], entry["reason"]
        )
        used.add(key)
    for finding_id, fingerprint in sorted(set(approved) - used):
        notices.append(
            "approval {}({}) in {} matches no REVIEW_REQUIRED finding".format(
                finding_id, fingerprint[:19], approval_path
            )
        )
    return notices
