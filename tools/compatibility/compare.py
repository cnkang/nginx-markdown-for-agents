"""Compare two contract manifests under the 1.0 compatibility policy.

The policy has five verdicts.  ``BREAKING`` is a change to published stable
behaviour.  ``REVIEW_REQUIRED`` is a change the policy permits only with a
recorded maintainer decision, so a bare gate run fails closed on it.
``UNVERIFIED`` means the evidence needed to decide is missing, which is never
treated as a pass.  ``ADDITIVE`` and ``OK`` are compatible outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
                finding_id=f"{surface}:{finding_id}",
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


def _sort_mixed_values(values: Iterable[Any]) -> List[Any]:
    """Sort JSON enum values without comparing unlike Python types."""
    return sorted(values, key=lambda value: (value is not None, str(value)))


def _json_equality_key(value: Any) -> Any:
    """Return a hashable key that follows JSON Schema instance equality.

    Booleans stay distinct from numbers, numerically equal numbers such as 1
    and 1.0 share one key, and arrays and objects compare by content.
    """
    if value is None:
        return ("null", None)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        return ("number", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(_json_equality_key(item) for item in value))
    if isinstance(value, dict):
        return (
            "object",
            tuple(
                sorted(
                    (key, _json_equality_key(item)) for key, item in value.items()
                )
            ),
        )
    raise TypeError(f"enum value is not JSON data: {value!r}")


def _enum_difference(
    old_values: List[Any], new_values: List[Any]
) -> Tuple[List[Any], List[Any]]:
    """Return the enum values only the baseline or only the candidate allows."""
    old_keys = {_json_equality_key(value): value for value in old_values}
    new_keys = {_json_equality_key(value): value for value in new_values}
    removed = [old_keys[key] for key in old_keys.keys() - new_keys.keys()]
    added = [new_keys[key] for key in new_keys.keys() - old_keys.keys()]
    return _sort_mixed_values(removed), _sort_mixed_values(added)


_SCHEMA_SEGMENT_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _schema_segment(name: str) -> str:
    """Percent-encode one property name for a diagnostics schema path.

    Paths join property segments with ``.``, mark array items with ``[]``, and
    separate a parent from its child with ``:``.  Encoding every other
    character keeps a property named ``a.b`` distinct from ``a`` -> ``b``,
    while ordinary names keep their plain spelling.
    """
    return "".join(
        char
        if char in _SCHEMA_SEGMENT_SAFE
        else "".join(f"%{byte:02X}" for byte in char.encode("utf-8"))
        for char in name
    )


def _by_segment(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index projected schema fields by their encoded path segment."""
    return {_schema_segment(entry["name"]): entry for entry in entries}


def _compare_directive(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare one directive that exists on both sides."""
    if old["handler"] != new["handler"]:
        result.add(
            "directives",
            BREAKING,
            f"handler-changed:{name}",
            f"directive {name} changed handler, which changes whether it is active",
            old["handler"],
            new["handler"],
        )
    if old["args"] != new["args"]:
        result.add(
            "directives",
            BREAKING,
            f"args-changed:{name}",
            f"directive {name} changed its argument contract",
            old["args"],
            new["args"],
        )
    old_context = set(old["context"])
    new_context = set(new["context"])
    if new_context - old_context:
        result.add(
            "directives",
            ADDITIVE,
            f"context-widened:{name}",
            f"directive {name} accepts an additional context",
            sorted(old_context),
            sorted(new_context),
        )
    if old_context - new_context:
        result.add(
            "directives",
            BREAKING,
            f"context-narrowed:{name}",
            f"directive {name} no longer accepts a context it used to accept",
            sorted(old_context),
            sorted(new_context),
        )
    if old["default"] != new["default"]:
        result.add(
            "directives",
            BREAKING,
            f"default-changed:{name}",
            f"directive {name} changed its documented default",
            old["default"],
            new["default"],
        )
    if old["syntax"] != new["syntax"]:
        result.add(
            "directives",
            BREAKING,
            f"syntax-changed:{name}",
            f"directive {name} changed its accepted value syntax",
            old["syntax"],
            new["syntax"],
        )
    if old["documented_context"] != new["documented_context"]:
        result.add(
            "directives",
            REVIEW_REQUIRED,
            f"documented-context-changed:{name}",
            f"directive {name} documentation changed its context list",
            old["documented_context"],
            new["documented_context"],
        )
    if old["documentation"] != new["documentation"]:
        result.add(
            "directives",
            REVIEW_REQUIRED,
            f"documentation-changed:{name}",
            f"directive {name} documentation text changed",
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
            f"removed:{name}",
            f"stable directive {name} is no longer registered",
            old_map[name]["syntax"],
            None,
        )
    for name in sorted(set(new_map) - set(old_map)):
        result.add(
            "directives",
            ADDITIVE,
            f"added:{name}",
            f"new directive {name} is registered",
            None,
            new_map[name]["syntax"],
        )
    for name in sorted(set(old_map) & set(new_map)):
        _compare_directive(result, name, old_map[name], new_map[name])
    old_sources = baseline.get("runtime_default_sources") or {}
    new_sources = candidate.get("runtime_default_sources") or {}
    for path in sorted(set(old_sources) | set(new_sources)):
        if old_sources.get(path) != new_sources.get(path):
            result.add(
                "directives",
                REVIEW_REQUIRED,
                f"runtime-default-source-changed:{path}",
                "directive runtime default source changed; verify documented defaults",
                old_sources.get(path),
                new_sources.get(path),
            )
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
            f"type-changed:{name}",
            f"metric family {name} changed its Prometheus type",
            old["type"],
            new["type"],
        )
    if old.get("bucket_boundaries") != new.get("bucket_boundaries"):
        result.add(
            "metrics",
            BREAKING,
            f"buckets-changed:{name}",
            f"metric family {name} changed its histogram buckets",
            old.get("bucket_boundaries"),
            new.get("bucket_boundaries"),
        )
    old_labels = _by_name(old["labels"])
    new_labels = _by_name(new["labels"])
    for label in sorted(set(old_labels) - set(new_labels)):
        result.add(
            "metrics",
            BREAKING,
            f"label-removed:{name}:{label}",
            f"metric family {name} dropped label {label}",
            label,
            None,
        )
    for label in sorted(set(new_labels) - set(old_labels)):
        result.add(
            "metrics",
            BREAKING,
            f"label-added:{name}:{label}",
            f"metric family {name} gained label {label}, which changes series identity",
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
                f"label-dynamic-changed:{name}:{label}",
                f"label {label} of {name} changed between a bounded allowlist and a dynamic value",
                old_labels[label]["dynamic"],
                new_labels[label]["dynamic"],
            )
        if old_values - new_values:
            result.add(
                "metrics",
                BREAKING,
                f"label-value-removed:{name}:{label}",
                f"label {label} of {name} lost a stable value",
                sorted(old_values - new_values),
                None,
            )
        if new_values - old_values:
            result.add(
                "metrics",
                ADDITIVE,
                f"label-value-added:{name}:{label}",
                f"label {label} of {name} gained a value",
                None,
                sorted(new_values - old_values),
            )


def _label_set_identity(label_sets: Optional[List[List[str]]]) -> set:
    """Return emitted label sets independent of the order samples appear in."""
    return {tuple(label_set) for label_set in label_sets or []}


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
            f"emitted-family-removed:{name}",
            f"the renderer no longer emits family {name}",
            old["families"][name],
            None,
        )
    for name in sorted(set(new["families"]) - set(old["families"])):
        result.add(
            "metrics",
            ADDITIVE,
            f"emitted-family-added:{name}",
            f"the renderer emits a new family {name}",
            None,
            new["families"][name],
        )
    for name in sorted(set(old["families"]) & set(new["families"])):
        if old["families"][name] != new["families"][name]:
            result.add(
                "metrics",
                BREAKING,
                f"emitted-type-changed:{name}",
                f"the renderer changed the emitted type of {name}",
                old["families"][name],
                new["families"][name],
            )
        if _label_set_identity(old["label_sets"].get(name)) != _label_set_identity(
            new["label_sets"].get(name)
        ):
            result.add(
                "metrics",
                BREAKING,
                f"emitted-labels-changed:{name}",
                f"the renderer changed the label set of {name}",
                old["label_sets"].get(name),
                new["label_sets"].get(name),
            )
        old_series = {tuple(item) for item in old["series"].get(name, [])}
        new_series = {tuple(item) for item in new["series"].get(name, [])}
        for removed in sorted(old_series - new_series):
            result.add(
                "metrics",
                BREAKING,
                f'emitted-series-removed:{name}:{",".join(removed)}',
                f"the renderer stopped emitting a series of {name}",
                list(removed),
                None,
            )
        for added in sorted(new_series - old_series):
            result.add(
                "metrics",
                ADDITIVE,
                f'emitted-series-added:{name}:{",".join(added)}',
                f"the renderer emits a new series of {name}",
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
                f"the metrics registry {key} changed",
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
            f"declared-family-removed:{name}",
            f"the registry no longer declares family {name}",
            old_map[name]["type"],
            None,
        )
    for name in sorted(set(new_map) - set(old_map)):
        result.add(
            "metrics",
            ADDITIVE,
            f"declared-family-added:{name}",
            f"the registry declares a new family {name}",
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
            f"discriminant-changed:{key}",
            f"reason {key} changed its numeric discriminant",
            old["discriminant"],
            new["discriminant"],
        )
    for field_name in ("stage", "outcome", "default_origin", "operator_visible"):
        if old[field_name] != new[field_name]:
            result.add(
                "reasons",
                BREAKING,
                f'{field_name.replace("_", "-")}-changed:{key}',
                f"reason {key} changed its {field_name}",
                old[field_name],
                new[field_name],
            )
    old_origins = set(old["allowed_origins"])
    new_origins = set(new["allowed_origins"])
    if old_origins - new_origins:
        result.add(
            "reasons",
            BREAKING,
            f"origins-removed:{key}",
            f"reason {key} no longer allows an origin it used to allow",
            sorted(old_origins - new_origins),
            None,
        )
    if new_origins - old_origins:
        result.add(
            "reasons",
            ADDITIVE,
            f"origins-added:{key}",
            f"reason {key} allows an additional origin",
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
            f"removed:{key}",
            f"reason {key} was removed from the registry",
            old_map[key]["discriminant"],
            None,
        )
    for key in sorted(set(new_map) - set(old_map)):
        result.add(
            "reasons",
            ADDITIVE,
            f"added:{key}",
            f"reason {key} was added to the registry",
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
            f"type-changed:{name}",
            f"diagnostics field {name} changed type",
            old["type"],
            new["type"],
        )


def _compare_schema_constraints(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any], key: str
) -> None:
    """Require review when a projected JSON Schema constraint changes."""
    old_constraints = old.get(key) or {}
    new_constraints = new.get(key) or {}
    if old_constraints != new_constraints:
        result.add(
            "diagnostics",
            REVIEW_REQUIRED,
            f"schema-constraints-changed:{name}",
            f"diagnostics schema constraints changed for {name}",
            old_constraints,
            new_constraints,
        )


def _compare_field_nullability(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare the nullability of one diagnostics field."""
    if old["nullable"] and not new["nullable"]:
        result.add(
            "diagnostics",
            BREAKING,
            f"nullability-narrowed:{name}",
            f"diagnostics field {name} no longer permits null",
            old["nullable"],
            new["nullable"],
        )
    if not old["nullable"] and new["nullable"]:
        result.add(
            "diagnostics",
            REVIEW_REQUIRED,
            f"nullability-widened:{name}",
            f"diagnostics field {name} now permits null",
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
            f"enum-added:{name}",
            f"diagnostics field {name} gained a closed value set",
            None,
            _sort_mixed_values(new.get("enum") or []),
        )
        return
    if old_declares and not new_declares:
        result.add(
            "diagnostics",
            ADDITIVE,
            f"enum-removed:{name}",
            f"diagnostics field {name} no longer restricts its values",
            _sort_mixed_values(old.get("enum") or []),
            None,
        )
        return
    removed, added = _enum_difference(old.get("enum") or [], new.get("enum") or [])
    if removed:
        result.add(
            "diagnostics",
            BREAKING,
            f"enum-narrowed:{name}",
            f"diagnostics field {name} lost an allowed value",
            removed,
            None,
        )
    if added:
        result.add(
            "diagnostics",
            ADDITIVE,
            f"enum-widened:{name}",
            f"diagnostics field {name} gained an allowed value",
            None,
            added,
        )


def _compare_field_closed_object(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare changes to whether an object accepts unknown properties."""
    old_value = old.get("additional_properties")
    new_value = new.get("additional_properties")
    if old_value != new_value and (old_value is False or new_value is False):
        direction = "weakened" if new_value is not False else "restricted"
        result.add(
            "diagnostics",
            BREAKING,
            f"additional-properties-{direction}:{name}",
            f"diagnostics object {name} changed its unknown-property contract",
            old_value,
            new_value,
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
            f"required-child-removed:{name}",
            f"diagnostics object {name} no longer requires a field",
            sorted(old_children - new_children),
            None,
        )
    if new_children - old_children:
        result.add(
            "diagnostics",
            REVIEW_REQUIRED,
            f"required-child-added:{name}",
            f"diagnostics object {name} requires an additional field",
            None,
            sorted(new_children - old_children),
        )


def _compare_child_enum(
    result: Comparison, name: str, child: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare the allowed values of one child field of a diagnostics object."""
    if old.get("enum") is not None and new.get("enum") is None:
        result.add(
            "diagnostics",
            ADDITIVE,
            f"child-enum-removed:{name}:{child}",
            f"diagnostics field {name} no longer restricts {child}",
            _enum_difference(old["enum"], [])[0],
            None,
        )
        return
    if new.get("enum") is not None and old.get("enum") is None:
        result.add(
            "diagnostics",
            BREAKING,
            f"child-enum-added:{name}:{child}",
            f"diagnostics field {name} of {child} gained a closed value set",
            None,
            _enum_difference([], new["enum"])[1],
        )
        return
    removed, added = _enum_difference(old.get("enum") or [], new.get("enum") or [])
    if removed:
        result.add(
            "diagnostics",
            BREAKING,
            f"child-enum-narrowed:{name}:{child}",
            f"diagnostics field {name} of {child} lost an allowed value",
            removed,
            None,
        )
    elif added:
        result.add(
            "diagnostics",
            ADDITIVE,
            f"child-enum-widened:{name}:{child}",
            f"diagnostics field {name} of {child} gained an allowed value",
            None,
            added,
        )


def _compare_retained_child(
    result: Comparison, name: str, child: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare one child field that both object projections declare."""
    if old["type"] != new["type"]:
        result.add(
            "diagnostics",
            BREAKING,
            f"child-type-changed:{name}:{child}",
            f"diagnostics object {name} changed the type of field {child}",
            old["type"],
            new["type"],
        )
    _compare_child_enum(result, name, child, old, new)
    path = f"{name}.{child}"
    _compare_schema_constraints(result, path, old, new, "constraints")
    _compare_nested_structure(result, path, old, new)


def _compare_properties(
    result: Comparison, name: str, old: List[Dict[str, Any]], new: List[Dict[str, Any]]
) -> None:
    """Compare a projected child field set, including nested child structure."""
    old_children = _by_segment(old)
    new_children = _by_segment(new)
    for child in sorted(set(old_children) - set(new_children)):
        result.add(
            "diagnostics",
            BREAKING,
            f"child-removed:{name}:{child}",
            f"diagnostics object {name} dropped field {child}",
            old_children[child]["type"],
            None,
        )
    for child in sorted(set(new_children) - set(old_children)):
        result.add(
            "diagnostics",
            ADDITIVE,
            f"child-added:{name}:{child}",
            f"diagnostics object {name} added field {child}",
            None,
            new_children[child]["type"],
        )
    for child in sorted(set(old_children) & set(new_children)):
        _compare_retained_child(
            result, name, child, old_children[child], new_children[child]
        )


def _items_enum_view(field: Dict[str, Any]) -> Dict[str, Any]:
    """Return an array's item enum in the shape the enum comparison reads."""
    return {"enum": field["items_enum"]} if "items_enum" in field else {}


def _compare_field_items(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare the item schema of an array field."""
    if old.get("items_type") != new.get("items_type"):
        result.add(
            "diagnostics",
            BREAKING,
            f"items-type-changed:{name}",
            f"diagnostics array {name} changed its item type",
            old.get("items_type"),
            new.get("items_type"),
        )
    old_required = set(old.get("items_required") or [])
    new_required = set(new.get("items_required") or [])
    if old_required - new_required:
        result.add(
            "diagnostics",
            BREAKING,
            f"items-required-removed:{name}",
            f"diagnostics array {name} no longer requires an item field",
            sorted(old_required - new_required),
            None,
        )
    if new_required - old_required:
        result.add(
            "diagnostics",
            REVIEW_REQUIRED,
            f"items-required-added:{name}",
            f"diagnostics array {name} requires an additional item field",
            None,
            sorted(new_required - old_required),
        )
    old_additional = old.get("items_additional_properties")
    new_additional = new.get("items_additional_properties")
    if old_additional != new_additional and (
        old_additional is False or new_additional is False
    ):
        direction = "weakened" if new_additional is not False else "restricted"
        result.add(
            "diagnostics",
            BREAKING,
            f"items-additional-properties-{direction}:{name}",
            f"diagnostics array items for {name} changed its unknown-property contract",
            old_additional,
            new_additional,
        )
    _compare_schema_constraints(
        result, f"{name}[]", old, new, "items_constraints"
    )
    _compare_field_enum(
        result, f"{name}[]", _items_enum_view(old), _items_enum_view(new)
    )
    _compare_properties(
        result,
        f"{name}[]",
        old.get("items_properties") or [],
        new.get("items_properties") or [],
    )


def _compare_nested_structure(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare the array-item and object structure below one projected field.

    Top-level fields and nested children share this projection, so a type
    change at any depth reaches a finding instead of only a digest change.
    """
    if "items_type" in old or "items_type" in new:
        _compare_field_items(result, name, old, new)
    if "required_children" not in old and "required_children" not in new:
        return
    _compare_field_closed_object(result, name, old, new)
    _compare_field_required_children(result, name, old, new)
    if old.get("properties") is not None and new.get("properties") is not None:
        _compare_properties(result, name, old["properties"], new["properties"])


def _compare_diagnostics_field(
    result: Comparison, name: str, old: Dict[str, Any], new: Dict[str, Any]
) -> None:
    """Compare one diagnostics schema property."""
    _compare_field_type(result, name, old, new)
    _compare_field_nullability(result, name, old, new)
    _compare_field_enum(result, name, old, new)
    _compare_schema_constraints(result, name, old, new, "constraints")
    _compare_nested_structure(result, name, old, new)


def _compare_diagnostics_header(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the root schema and endpoint-level declarations."""
    if baseline.get("type") != candidate.get("type"):
        result.add(
            "diagnostics",
            BREAKING,
            "root-type-changed",
            "the diagnostics root schema changed type",
            baseline.get("type"),
            candidate.get("type"),
        )
    _compare_schema_constraints(
        result, "root", baseline, candidate, "constraints"
    )
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
            f"required-added:{_schema_segment(name)}",
            f"diagnostics field {_schema_segment(name)} became required",
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
            f"top-level-field-added:{_schema_segment(name)}",
            f"diagnostics added top-level field {_schema_segment(name)}; "
            "the frozen contract keeps additive state in extensions",
            None,
            name,
        )
    old_additional = baseline["additional_properties"]
    new_additional = candidate["additional_properties"]
    if old_additional != new_additional and (
        old_additional is False or new_additional is False
    ):
        direction = "weakened" if new_additional is not False else "restricted"
        result.add(
            "diagnostics",
            BREAKING,
            f"root-additional-properties-{direction}",
            "the diagnostics root changed its unknown-property contract",
            old_additional,
            new_additional,
        )


def compare_diagnostics(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the diagnostics wire schema."""
    _compare_diagnostics_header(result, baseline, candidate)
    _compare_diagnostics_top_level(result, baseline, candidate)
    old_map = _by_segment(baseline["fields"])
    new_map = _by_segment(candidate["fields"])
    for name in sorted(set(old_map) - set(new_map)):
        result.add(
            "diagnostics",
            BREAKING,
            f"field-removed:{name}",
            f"diagnostics field {name} was removed",
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
            f"unlabelled-sample-removed:{name}",
            f"the renderer stopped emitting the unlabelled sample of {name}",
            name,
            None,
        )
    for name in sorted(new_samples - old_samples):
        result.add(
            "metrics",
            ADDITIVE,
            f"unlabelled-sample-added:{name}",
            f"the renderer emits an unlabelled sample of {name}",
            None,
            name,
        )


def _http_binding_digests(
    behaviour: Dict[str, Any], digests: Dict[str, str]
) -> Dict[str, Optional[str]]:
    """Return test paths bound to one behaviour and their digests."""
    return {path: digests.get(path) for path in behaviour["tests"]}


def _http_addition_evidence(
    behaviour: Dict[str, Any], digests: Dict[str, str]
) -> Dict[str, Any]:
    """Return the full binding of a new behaviour: its tests and executor.

    The approval fingerprint covers this value, so a different executor
    invalidates an earlier approval while an unchanged binding still matches.
    """
    return {
        "tests": _http_binding_digests(behaviour, digests),
        "executed_by": behaviour["executed_by"],
    }


def _compare_removed_http_behaviours(
    result: Comparison,
    old: Dict[str, Any],
    new: Dict[str, Any],
    digests: Dict[str, str],
) -> None:
    """Report behaviours that lost their contract-test binding."""
    for name in sorted(set(old) - set(new)):
        result.add(
            "http",
            BREAKING,
            f"behaviour-removed:{name}",
            f"HTTP behaviour {name} is no longer bound to a contract test",
            _http_binding_digests(old[name], digests),
            None,
        )


def _compare_added_http_behaviours(
    result: Comparison,
    old: Dict[str, Any],
    new: Dict[str, Any],
    digests: Dict[str, str],
) -> None:
    """Require review when a behaviour is newly bound to tests."""
    for name in sorted(set(new) - set(old)):
        result.add(
            "http",
            REVIEW_REQUIRED,
            f"behaviour-added:{name}",
            f"HTTP behaviour {name} was added to the contract",
            None,
            _http_addition_evidence(new[name], digests),
        )


def _compare_retained_http_behaviours(
    result: Comparison,
    old: Dict[str, Any],
    new: Dict[str, Any],
    old_digests: Dict[str, str],
    new_digests: Dict[str, str],
) -> None:
    """Compare test bindings and executors for retained behaviours."""
    for name in sorted(set(old) & set(new)):
        if old[name]["tests"] != new[name]["tests"]:
            result.add(
                "http",
                REVIEW_REQUIRED,
                f"binding-changed:{name}",
                f"the test binding for HTTP behaviour {name} changed",
                _http_binding_digests(old[name], old_digests),
                _http_binding_digests(new[name], new_digests),
            )
        if old[name]["executed_by"] != new[name]["executed_by"]:
            result.add(
                "http",
                REVIEW_REQUIRED,
                f"executor-changed:{name}",
                f"the executing target for HTTP behaviour {name} changed",
                old[name]["executed_by"],
                new[name]["executed_by"],
            )


def _compare_http_test_digests(
    result: Comparison, old_digests: Dict[str, str], new_digests: Dict[str, str]
) -> None:
    """Require review when a bound HTTP contract test changes."""
    for path in sorted(set(old_digests) & set(new_digests)):
        if old_digests[path] != new_digests[path]:
            result.add(
                "http",
                REVIEW_REQUIRED,
                f"test-changed:{path}",
                f"HTTP contract test {path} changed; the behaviour it asserts needs review",
                old_digests[path],
                new_digests[path],
            )


def compare_http(
    result: Comparison, baseline: Dict[str, Any], candidate: Dict[str, Any]
) -> None:
    """Compare the behavioural HTTP contract binding."""
    old = baseline["behaviours"]
    new = candidate["behaviours"]
    old_digests = baseline["test_file_digests"]
    new_digests = candidate["test_file_digests"]
    _compare_removed_http_behaviours(result, old, new, old_digests)
    _compare_added_http_behaviours(result, old, new, new_digests)
    _compare_retained_http_behaviours(result, old, new, old_digests, new_digests)
    _compare_http_test_digests(result, old_digests, new_digests)


SUPPORT_TIER_RANK = {"experimental": 0, "best-effort": 1, "supported": 2}


def _support_groups(contract: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Group matrix rows by the target the comparison reports on.

    Every row keeps its own tier, so a group can report more than one value.
    The group takes the highest tier and the blocking flag of any member, which
    makes the comparison independent of row order and of patch progression.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for row in contract["support"]["targets"]:
        key = (
            f'{row["nginx_minor"]}/{row["artifact_type"]}/'
            f'{row["os"]}/{row["libc"]}/{row["arch"]}'
        )
        bucket = groups.setdefault(
            key,
            {
                "nginx_minor": row["nginx_minor"],
                "artifact_type": row["artifact_type"],
                "os": row["os"],
                "libc": row["libc"],
                "arch": row["arch"],
                "tiers": set(),
                "blocking": False,
                "supported_blocking": False,
                "patches": [],
            },
        )
        bucket["tiers"].add(row["support_tier"])
        bucket["blocking"] = bucket["blocking"] or row["release_blocking"]
        bucket["supported_blocking"] = bucket["supported_blocking"] or (
            row["support_tier"] == "supported" and row["release_blocking"]
        )
        bucket["patches"].append(row["nginx_version"])
    for bucket in groups.values():
        bucket["patches"].sort()
        bucket["tiers"] = sorted(
            bucket["tiers"], key=lambda tier: SUPPORT_TIER_RANK.get(tier, -1)
        )
    return groups


def _support_row_key(row: Dict[str, Any]) -> str:
    """Identify one matrix row by artifact, target, and patch version."""
    return (
        f'{row["nginx_version"]}|{row["artifact_type"]}|'
        f'{row["os"]}/{row["libc"]}/{row["arch"]}'
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
                f"row-tier-changed:{key}",
                f"support tier changed for row {key} from "
                f'{old_row["support_tier"]} to {new_row["support_tier"]}',
                old_row["support_tier"],
                new_row["support_tier"],
            )
        if old_row["release_blocking"] and not new_row["release_blocking"]:
            result.add(
                "packaging",
                BREAKING,
                f"row-release-blocking-dropped:{key}",
                f"row {key} stopped being release blocking",
                old_row["release_blocking"],
                new_row["release_blocking"],
            )
        if not old_row["release_blocking"] and new_row["release_blocking"]:
            result.add(
                "packaging",
                ADDITIVE,
                f"row-release-blocking-added:{key}",
                f"row {key} became release blocking",
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
            f"patch-progression:{key}",
            f'{key} tracks patch versions {old["patches"]} -> {new["patches"]}',
            old["patches"],
            new["patches"],
        )
    if old["supported_blocking"] and not new["supported_blocking"]:
        result.add(
            "packaging",
            BREAKING,
            f"support-blocking-coverage-dropped:{key}",
            f"{key} no longer has a supported release-blocking matrix row",
            old["supported_blocking"],
            new["supported_blocking"],
        )


def _compare_install_paths(
    result: Comparison, key: str, old: List[str], new: List[str]
) -> None:
    """Compare one family's effective install path list."""
    for path in sorted(set(old) - set(new)):
        result.add(
            "packaging",
            BREAKING,
            f"{key}-path-removed:{path}",
            f"the {key} package no longer installs {path}",
            path,
            None,
        )
    for path in sorted(set(new) - set(old)):
        result.add(
            "packaging",
            ADDITIVE,
            f"{key}-path-added:{path}",
            f"the {key} package installs {path}",
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
                f"the installed module path changed for {key}",
                baseline[key],
                candidate[key],
            )
    for key in (
        "deb_load_snippet_paths",
        "rpm_load_snippet_paths",
        "deb_nginx_constraints",
        "rpm_nginx_constraints",
    ):
        if baseline[key] != candidate[key]:
            result.add(
                "packaging",
                REVIEW_REQUIRED,
                CHANGED_ID.format(key.replace("_", "-")),
                f"the packaging {key} contract changed",
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
            f"support-target-removed:{key}",
            f"supported target {key} was removed from the matrix",
            old_groups[key]["tiers"],
            None,
        )
    for key in sorted(set(new_groups) - set(old_groups)):
        result.add(
            "packaging",
            ADDITIVE,
            f"support-target-added:{key}",
            f"supported target {key} was added to the matrix",
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
            f"symbol-removed:{name}",
            f"FFI export {name} was removed from the generated header",
            old_map[name]["signature"],
            None,
        )
    for name in sorted(set(new_map) - set(old_map)):
        result.add(
            "ffi",
            ADDITIVE,
            f"symbol-added:{name}",
            f"FFI export {name} was added to the generated header",
            None,
            new_map[name]["signature"],
        )
    for name in sorted(set(old_map) & set(new_map)):
        if old_map[name]["signature"] != new_map[name]["signature"]:
            result.add(
                "ffi",
                REVIEW_REQUIRED,
                f"signature-changed:{name}",
                f"FFI export {name} changed signature",
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
            missing = "candidate" if surface in old_surfaces else "baseline"
            result.add(
                surface,
                UNVERIFIED,
                "surface-missing",
                f"the {missing} manifest has no {surface} surface",
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
                f"approval in {approval_path} is missing {field}"
            )
        if not isinstance(value, str):
            raise ValueError(
                f"approval in {approval_path} has a non-string {field}"
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
    if not isinstance(approvals, dict):
        raise ValueError("approval file must be a JSON object")
    notices: List[str] = []
    entries = approvals.get("exceptions", [])
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict) for entry in entries
    ):
        raise ValueError("approval exceptions must be a list of JSON objects")
    require_shape = approvals.get("schema") == 1
    if not require_shape:
        raise ValueError("approval file must declare \"schema\": 1")
    approved: Dict[tuple, Dict[str, Any]] = {
        _approval_key(entry, approval_path): entry for entry in entries
    }
    used = set()
    for finding in result.findings:
        if finding.verdict != REVIEW_REQUIRED:
            continue
        key = (finding.finding_id, finding.fingerprint)
        entry = approved.get(key)
        if entry is None:
            continue
        finding.verdict = OK
        finding.message = (
            f'{finding.message} [approved by {entry["approved_by"]}: '
            f'{entry["reason"]}]'
        )
        used.add(key)
    notices.extend(
        f"approval {finding_id}({fingerprint[:19]}) in {approval_path} "
        "matches no REVIEW_REQUIRED finding"
        for finding_id, fingerprint in sorted(set(approved) - used)
    )
    return notices
