//! Read module-level metrics from stdin and emit a threshold verdict as JSON.

use num_bigint::BigInt;
use num_rational::BigRational;
use num_traits::ToPrimitive;
use serde::Deserialize;
use serde_json::{Number, Value, json};
use std::collections::BTreeMap;
use std::io;
use std::str::FromStr;

#[derive(Deserialize)]
struct Request {
    current: BTreeMap<String, Value>,
    baseline: BTreeMap<String, Number>,
    thresholds: BTreeMap<String, Value>,
    has_baseline: bool,
}

fn integer(number: &Number) -> Option<BigInt> {
    BigInt::from_str(number.as_str()).ok()
}

fn as_float(number: &Number) -> f64 {
    number.as_str().parse().expect("JSON number")
}

fn deviation(current: &Number, baseline: &Number) -> f64 {
    let cur = as_float(current);
    let base = as_float(baseline);
    if base == 0.0 {
        return if cur == 0.0 {
            0.0
        } else {
            cur.signum() * 100.0
        };
    }
    // Python subtracts integral measurements exactly before dividing.
    if let (Some(cur), Some(base)) = (integer(current), integer(baseline)) {
        let magnitude = base.magnitude().clone().into();
        return BigRational::new_raw(cur - base, magnitude)
            .to_f64()
            .unwrap_or(f64::NAN)
            * 100.0;
    }
    (cur - base) / base.abs() * 100.0
}

fn numeric_le(left: &Number, right: &Value) -> bool {
    let left_float = as_float(left);
    let Some(right) = right.as_number() else {
        // The Python adapter encodes non-finite thresholds as strings.
        return right
            .as_str()
            .and_then(|v| v.parse::<f64>().ok())
            .is_some_and(|right| left_float <= right);
    };
    let right_float = as_float(right);
    if let (Some(left), Some(right)) = (integer(left), integer(right)) {
        return left <= right;
    }
    if let (Some(left), Some(right)) = (exact_value(left), exact_value(right)) {
        return left <= right;
    }
    left_float <= right_float
}

fn exact_value(number: &Number) -> Option<BigRational> {
    if let Some(value) = integer(number) {
        return Some(BigRational::from_integer(value));
    }
    BigRational::from_float(as_float(number))
}

fn evaluate_metric(request: &Request, metric: &str, threshold: &Value) -> Value {
    let current = match request.current.get(metric) {
        None | Some(Value::Null) => {
            return missing_current(metric, "not present in current measurements");
        }
        Some(Value::Number(current)) if as_float(current).is_finite() => current,
        Some(other) => {
            let display = other
                .get("invalid")
                .and_then(Value::as_str)
                .map_or_else(|| other.to_string(), str::to_owned);
            return missing_current(metric, &format!("not a finite number: {display}"));
        }
    };
    if metric == "fallback_rate_abs" {
        return outcome(
            metric,
            numeric_le(current, threshold),
            threshold,
            json!(current),
            None,
            current,
        );
    }
    if !request.has_baseline {
        return missing_baseline(
            metric,
            threshold,
            current,
            "cannot evaluate percentage threshold: missing baseline",
        );
    }
    let Some(baseline) = request
        .baseline
        .get(metric)
        .filter(|value| as_float(value).is_finite())
    else {
        return missing_baseline(
            metric,
            threshold,
            current,
            "baseline metric is missing or not a valid number",
        );
    };

    let actual = deviation(current, baseline);
    let passed = Number::from_f64(actual).map_or_else(
        || nonfinite_le(actual, threshold),
        |actual| numeric_le(&actual, threshold),
    );
    let reported = if actual.is_finite() {
        // Formatting to four places rounds the original binary float directly.
        json!(
            format!("{actual:.4}")
                .parse::<f64>()
                .expect("formatted float")
        )
    } else {
        json!(actual.to_string())
    };
    outcome(metric, passed, threshold, reported, Some(baseline), current)
}

fn nonfinite_le(actual: f64, threshold: &Value) -> bool {
    if let Some(threshold) = threshold
        .as_str()
        .and_then(|value| value.parse::<f64>().ok())
    {
        return actual <= threshold;
    }
    // An arbitrary-sized JSON integer is still finite.
    actual == f64::NEG_INFINITY
}

fn outcome(
    metric: &str,
    passed: bool,
    threshold: &Value,
    actual: Value,
    baseline: Option<&Number>,
    current: &Number,
) -> Value {
    json!({
        "metric": metric,
        "status": if passed { "pass" } else { "breach" },
        "threshold": threshold,
        "actual": actual,
        "baseline": baseline,
        "current": current,
    })
}

fn missing_current(metric: &str, reason: &str) -> Value {
    json!({
        "metric": metric,
        "status": "missing_evidence",
        "reason": format!("critical metric '{metric}' {reason}"),
    })
}

fn missing_baseline(metric: &str, threshold: &Value, current: &Number, reason: &str) -> Value {
    json!({
        "metric": metric,
        "status": "missing_evidence",
        "reason": reason,
        "threshold": threshold,
        "actual": null,
        "baseline": null,
        "current": current,
    })
}

fn evaluate(request: &Request) -> Value {
    let results: Vec<_> = request
        .thresholds
        .iter()
        .map(|(metric, threshold)| evaluate_metric(request, metric, threshold))
        .collect();
    let breaches: Vec<_> = results
        .iter()
        .filter(|entry| entry["status"] == "breach")
        .collect();
    let verdict = if results
        .iter()
        .any(|entry| entry["status"] == "missing_evidence")
    {
        "MISSING_EVIDENCE"
    } else if breaches.is_empty() {
        "GO"
    } else {
        "NO_GO"
    };
    json!({"verdict": verdict, "breaches": breaches, "results": results})
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let request = serde_json::from_reader(io::stdin().lock())?;
    serde_json::to_writer(io::stdout().lock(), &evaluate(&request))?;
    Ok(())
}
