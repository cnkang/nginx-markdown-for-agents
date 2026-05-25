//! HTTP conditional request handling for ETag and If-Modified-Since.
//!
//! Implements RFC 7232 conditional request semantics:
//! - If-None-Match: ETag comparison (strong and weak)
//! - If-Modified-Since: Last-Modified timestamp comparison
//!
//! # Examples
//!
//! ```
//! use nginx_markdown_converter::conditional::{evaluate_conditional, ConditionalResult};
//!
//! // Strong ETag match → 304 Not Modified
//! let result = evaluate_conditional(
//!     Some("W/\"abc123\""),  // if_none_match
//!     Some("W/\"abc123\""),  // entity_etag
//!     None,                   // if_modified_since
//!     None,                   // last_modified
//! );
//! assert!(matches!(result, ConditionalResult::NotModified));
//! ```

/// Result of conditional request evaluation.
#[derive(Debug, Clone, PartialEq)]
pub enum ConditionalResult {
    /// Conditions indicate the resource has not been modified.
    /// The server should respond with 304 Not Modified.
    NotModified,

    /// Conditions do not preclude a normal response.
    /// The server should proceed with full content generation.
    Proceed,
}

/// Parse an ETag value, handling both strong and weak forms.
///
/// Returns (is_weak, opaque_value) or None if malformed.
/// Strong: `"abc123"` → (false, "abc123")
/// Weak:   `W/"abc123"` → (true, "abc123")
fn parse_etag(etag: &str) -> Option<(bool, &str)> {
    let etag = etag.trim();

    if etag.starts_with("W/\"") && etag.ends_with('"') && etag.len() > 4 {
        Some((true, &etag[3..etag.len() - 1]))
    } else if etag.starts_with('"') && etag.ends_with('"') && etag.len() > 1 {
        Some((false, &etag[1..etag.len() - 1]))
    } else {
        None
    }
}

/// Parse the If-None-Match header into a list of ETags.
///
/// Per RFC 7232 §3.2, the value is a comma-separated list of ETags
/// or `*` (match any).
fn parse_if_none_match(header: &str) -> Vec<(bool, String)> {
    let trimmed = header.trim();

    if trimmed == "*" {
        return vec![(false, "*".to_string())];
    }

    let mut etags = Vec::new();
    // Simple comma-split; ETags containing commas are not supported
    // (they would require quoted-string parsing per RFC 7230 §3.2.6).
    for part in trimmed.split(',') {
        let part = part.trim();
        if let Some((weak, value)) = parse_etag(part) {
            etags.push((weak, value.to_string()));
        }
    }
    etags
}

/// Compare two ETags according to RFC 7232 §2.3.3.
///
/// Strong comparison: both must be strong and values match.
/// Weak comparison: values match regardless of strength.
///
/// Note: strong comparison is used for If-Match (PUT/DELETE) which
/// this module does not yet implement. Kept in test scope for
/// completeness and future use.
#[cfg(test)]
fn etag_strong_match(a: (bool, &str), b: (bool, &str)) -> bool {
    !a.0 && !b.0 && a.1 == b.1
}

fn etag_weak_match(a: (bool, &str), b: (bool, &str)) -> bool {
    a.1 == b.1
}

/// Evaluate conditional request headers.
///
/// Per RFC 7232 §6:
/// 1. If If-None-Match is present and matches the entity ETag,
///    return NotModified (304).
/// 2. If If-Modified-Since is present and the resource has not been
///    modified since that date, return NotModified (304).
/// 3. Otherwise, return Proceed.
///
/// # Arguments
///
/// * `if_none_match` - The If-None-Match header value (may be None).
/// * `entity_etag` - The current entity's ETag (may be None).
/// * `if_modified_since` - The If-Modified-Since header value (may be None).
/// * `last_modified` - The resource's Last-Modified date (may be None).
///
/// # Returns
///
/// `ConditionalResult::NotModified` if a 304 response is appropriate,
/// `ConditionalResult::Proceed` otherwise.
pub fn evaluate_conditional(
    if_none_match: Option<&str>,
    entity_etag: Option<&str>,
    if_modified_since: Option<&str>,
    last_modified: Option<&str>,
) -> ConditionalResult {
    // Step 1: If-None-Match (per RFC 7232 §3.2, takes precedence)
    if let Some(inm_header) = if_none_match {
        let inm_etags = parse_if_none_match(inm_header);

        // `*` matches any entity
        if inm_etags.iter().any(|(_, v)| v == "*") {
            return ConditionalResult::NotModified;
        }

        if let Some((etag_weak, etag_val)) = entity_etag.and_then(parse_etag) {
            // If-None-Match uses weak comparison for GET/HEAD
            for (inm_weak, inm_val) in &inm_etags {
                if etag_weak_match((*inm_weak, inm_val.as_str()), (etag_weak, etag_val)) {
                    return ConditionalResult::NotModified;
                }
            }
        }

        // If-None-Match was present but did not match → proceed
        return ConditionalResult::Proceed;
    }

    // Step 2: If-Modified-Since (only if If-None-Match is absent)
    if let (Some(ims), Some(lm)) = (if_modified_since, last_modified)
        && !is_modified_since(ims, lm)
    {
        return ConditionalResult::NotModified;
    }

    ConditionalResult::Proceed
}

/// Compare If-Modified-Since with Last-Modified.
///
/// Returns true if the resource has been modified since the IMS date.
/// Returns false if the resource has NOT been modified (or dates are
/// unparseable, in which case we conservatively assume modified).
fn is_modified_since(if_modified_since: &str, last_modified: &str) -> bool {
    let ims_time = parse_http_date(if_modified_since);
    let lm_time = parse_http_date(last_modified);

    match (ims_time, lm_time) {
        (Some(ims), Some(lm)) => lm > ims,
        _ => true, // Conservatively assume modified on parse failure
    }
}

/// Parse an HTTP date (RFC 7231 §7.1.1.1).
///
/// Supports IMF-fixdate format: "Sun, 06 Nov 1994 08:49:37 GMT"
/// Returns Unix timestamp or None if unparseable.
fn parse_http_date(date: &str) -> Option<i64> {
    let date = date.trim();

    // Quick validation: must end with "GMT" and have expected length
    if !date.ends_with("GMT") {
        return None;
    }

    // Expected format: "Day, DD Mon YYYY HH:MM:SS GMT"
    // Total length: 29 characters
    if date.len() != 29 {
        return None;
    }

    let bytes = date.as_bytes();

    // Validate weekday (positions 0..3)
    let weekday = &date[0..3];
    match weekday {
        "Mon" | "Tue" | "Wed" | "Thu" | "Fri" | "Sat" | "Sun" => {}
        _ => return None,
    }

    // Validate fixed separators: ", " at pos 3..5, spaces at 7,11,16, colons at 19,22
    if bytes[3] != b',' || bytes[4] != b' ' {
        return None;
    }
    if bytes[7] != b' ' || bytes[11] != b' ' || bytes[16] != b' ' {
        return None;
    }
    if bytes[19] != b':' || bytes[22] != b':' {
        return None;
    }
    // Space before "GMT" at position 25
    if bytes[25] != b' ' {
        return None;
    }

    // Extract components
    let day_str = &date[5..7];
    let month_str = &date[8..11];
    let year_str = &date[12..16];
    let hour_str = &date[17..19];
    let min_str = &date[20..22];
    let sec_str = &date[23..25];

    let day: i64 = day_str.parse().ok()?;
    let year: i64 = year_str.parse().ok()?;
    let hour: i64 = hour_str.parse().ok()?;
    let min: i64 = min_str.parse().ok()?;
    let sec: i64 = sec_str.parse().ok()?;

    let month: i64 = match month_str {
        "Jan" => 1,
        "Feb" => 2,
        "Mar" => 3,
        "Apr" => 4,
        "May" => 5,
        "Jun" => 6,
        "Jul" => 7,
        "Aug" => 8,
        "Sep" => 9,
        "Oct" => 10,
        "Nov" => 11,
        "Dec" => 12,
        _ => return None,
    };

    // Convert to Unix timestamp using simplified formula
    // (valid for 1970-2100, sufficient for HTTP date ranges)
    if !(1970..=2100).contains(&year)
        || !(1..=12).contains(&month)
        || day < 1
        || hour > 23
        || min > 59
        || sec > 60
    {
        return None;
    }

    // Per-month day validation
    let max_day = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            let leap = (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
            if leap { 29 } else { 28 }
        }
        _ => return None,
    };
    if day > max_day {
        return None;
    }

    // Days from 1970-01-01 to start of given year
    let mut days: i64 = 0;
    for y in 1970..year {
        days += if is_leap_year(y) { 366 } else { 365 };
    }

    // Days from start of year to start of given month
    let days_in_months = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    for m in 1..month {
        days += days_in_months[m as usize];
        if m == 2 && is_leap_year(year) {
            days += 1;
        }
    }

    // Days from start of month to given day (1-indexed)
    days += day - 1;

    Some(days * 86400 + hour * 3600 + min * 60 + sec)
}

fn is_leap_year(year: i64) -> bool {
    (year % 4 == 0 && year % 100 != 0) || year % 400 == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_strong_etag() {
        let (weak, val) = parse_etag("\"abc123\"").unwrap();
        assert!(!weak);
        assert_eq!(val, "abc123");
    }

    #[test]
    fn test_parse_weak_etag() {
        let (weak, val) = parse_etag("W/\"abc123\"").unwrap();
        assert!(weak);
        assert_eq!(val, "abc123");
    }

    #[test]
    fn test_parse_malformed_etag() {
        assert!(parse_etag("not-an-etag").is_none());
        assert!(parse_etag("\"").is_none());
        assert!(parse_etag("W/\"").is_none());
    }

    #[test]
    fn test_strong_match_both_strong() {
        assert!(etag_strong_match((false, "abc"), (false, "abc")));
    }

    #[test]
    fn test_strong_match_one_weak() {
        assert!(!etag_strong_match((true, "abc"), (false, "abc")));
        assert!(!etag_strong_match((false, "abc"), (true, "abc")));
    }

    #[test]
    fn test_weak_match_ignores_strength() {
        assert!(etag_weak_match((true, "abc"), (false, "abc")));
        assert!(etag_weak_match((false, "abc"), (true, "abc")));
        assert!(etag_weak_match((true, "abc"), (true, "abc")));
    }

    #[test]
    fn test_if_none_match_match() {
        let r = evaluate_conditional(Some("\"abc\""), Some("\"abc\""), None, None);
        assert_eq!(r, ConditionalResult::NotModified);
    }

    #[test]
    fn test_if_none_match_no_match() {
        let r = evaluate_conditional(Some("\"def\""), Some("\"abc\""), None, None);
        assert_eq!(r, ConditionalResult::Proceed);
    }

    #[test]
    fn test_if_none_match_wildcard() {
        let r = evaluate_conditional(Some("*"), Some("\"abc\""), None, None);
        assert_eq!(r, ConditionalResult::NotModified);
    }

    #[test]
    fn test_if_none_match_weak_match() {
        let r = evaluate_conditional(Some("W/\"abc\""), Some("W/\"abc\""), None, None);
        assert_eq!(r, ConditionalResult::NotModified);
    }

    #[test]
    fn test_no_conditional_headers() {
        let r = evaluate_conditional(None, None, None, None);
        assert_eq!(r, ConditionalResult::Proceed);
    }

    #[test]
    fn test_if_modified_since_not_modified() {
        let r = evaluate_conditional(
            None,
            None,
            Some("Sun, 06 Nov 1994 08:49:37 GMT"),
            Some("Fri, 04 Nov 1994 08:49:37 GMT"), // earlier → not modified
        );
        assert_eq!(r, ConditionalResult::NotModified);
    }

    #[test]
    fn test_if_modified_since_modified() {
        let r = evaluate_conditional(
            None,
            None,
            Some("Fri, 04 Nov 1994 08:49:37 GMT"),
            Some("Sun, 06 Nov 1994 08:49:37 GMT"), // later → modified
        );
        assert_eq!(r, ConditionalResult::Proceed);
    }

    #[test]
    fn test_if_none_match_takes_precedence() {
        // If-None-Match matches → NotModified even though IMS would say Proceed
        let r = evaluate_conditional(
            Some("\"abc\""),
            Some("\"abc\""),
            Some("Fri, 04 Nov 1994 08:49:37 GMT"),
            Some("Sun, 06 Nov 1994 08:49:37 GMT"),
        );
        assert_eq!(r, ConditionalResult::NotModified);
    }

    #[test]
    fn test_parse_http_date_valid() {
        let ts = parse_http_date("Sun, 06 Nov 1994 08:49:37 GMT");
        assert!(ts.is_some());
        assert_eq!(ts.unwrap(), 784111777);
    }

    #[test]
    fn test_parse_http_date_invalid() {
        assert!(parse_http_date("not a date").is_none());
        assert!(parse_http_date("").is_none());
    }

    #[test]
    fn test_parse_http_date_invalid_weekday() {
        // Invalid weekday token
        assert!(parse_http_date("Foo, 06 Nov 1994 08:49:37 GMT").is_none());
        // Missing comma after weekday
        assert!(parse_http_date("Sun  06 Nov 1994 08:49:37 GMT").is_none());
        // Missing space after comma
        assert!(parse_http_date("Sun,X06 Nov 1994 08:49:37 GMT").is_none());
    }

    #[test]
    fn test_parse_http_date_invalid_separators() {
        // Colon replaced with space in time
        assert!(parse_http_date("Sun, 06 Nov 1994 08 49 37 GMT").is_none());
        // Feb 30 (invalid day for month)
        assert!(parse_http_date("Sun, 30 Feb 1994 08:49:37 GMT").is_none());
    }

    #[test]
    fn test_invalid_weekday_does_not_match_conditional() {
        // Malformed date should cause Proceed (conservatively assume modified)
        let r = evaluate_conditional(
            None,
            None,
            Some("Foo, 06 Nov 1994 08:49:37 GMT"),
            Some("Sun, 04 Nov 1994 08:49:37 GMT"),
        );
        assert_eq!(r, ConditionalResult::Proceed);
    }

    #[test]
    fn test_multiple_etags_in_if_none_match() {
        let r = evaluate_conditional(
            Some("\"def\", \"abc\", \"ghi\""),
            Some("\"abc\""),
            None,
            None,
        );
        assert_eq!(r, ConditionalResult::NotModified);
    }
}
