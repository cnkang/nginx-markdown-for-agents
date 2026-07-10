//! Trusted-proxy forwarded-header trust decision (spec 47, 0.9.0).
//!
//! This module owns the **pure** decision logic that replaces the legacy
//! boolean `markdown_trust_forwarded_headers` trust model with a CIDR-based
//! `markdown_trusted_proxies` model.  All forwarded-header parsing, CIDR
//! matching, host/proto validation, multi-hop chain handling, and base-URL
//! derivation live here so the NGINX C module stays a thin wrapper that only
//! marshals request/config fields across the FFI boundary.
//!
//! # Threat model
//!
//! The forwarded headers (`Forwarded`, `X-Forwarded-Proto`,
//! `X-Forwarded-Host`) are attacker-controlled unless the request arrives
//! from a trusted proxy.  An untrusted source must never be able to poison
//! the base URL used to resolve relative links in the Markdown output
//! (host/header injection, CRLF smuggling, userinfo/path injection).
//!
//! # Decision order (mirrors design.md "安全决策流程")
//!
//! 1. If trusted proxies are not configured, the forwarded headers are
//!    ignored and the base URL falls back to the `Host` header (reason
//!    [`BaseUrlReason::TrustedProxiesNotConfigured`]).
//! 2. If the source IP does not match any trusted CIDR, the forwarded
//!    headers are ignored and the base URL falls back to the `Host` header
//!    (reason [`BaseUrlReason::ForwardedHeaderUntrusted`]).
//! 3. Otherwise the trusted forwarded data is parsed and **strictly
//!    validated** (trusted source is not blindly believed): the `Forwarded`
//!    header (RFC 7239) takes precedence over `X-Forwarded-*`; multi-hop
//!    comma chains take the **right-most** element (closest to the trusted
//!    proxy) so a client-prepended malicious `host=` cannot poison the base
//!    URL.  The left-most element is attacker-controlled when the client
//!    pre-builds the header before the trusted proxy appends its own.
//! 4. Invalid host/proto values fall back to the `Host` header or a safe
//!    default with an explicit reason code.
//!
//! # Requirements
//!
//! Validates: spec 47 Requirements 1-7.

use std::net::{IpAddr, Ipv6Addr};

/// Reason code describing why a particular base URL was chosen.
///
/// The discriminants are the single FFI source of truth for the spec 53
/// reason-code names listed in the doc comments; the C side maps them to
/// lower_snake_case metric keys.  Values are frozen for the 1.0 stability
/// contract: add new codes, never renumber.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BaseUrlReason {
    /// `forwarded_header_trusted` — source IP matched a trusted CIDR and a
    /// valid forwarded host/proto was used.
    ForwardedHeaderTrusted = 0,
    /// `forwarded_header_untrusted` — source IP did not match any trusted
    /// CIDR; forwarded headers were ignored.
    ForwardedHeaderUntrusted = 1,
    /// `trusted_proxies_not_configured` — `markdown_trusted_proxies` was not
    /// configured; forwarded headers were ignored.
    TrustedProxiesNotConfigured = 2,
    /// `forwarded_invalid_host` — a trusted forwarded host failed validation
    /// (empty / control chars / comma / userinfo / path / bad port / bracket).
    ForwardedInvalidHost = 3,
    /// `forwarded_invalid_proto` — a trusted forwarded proto was not
    /// `http`/`https`.
    ForwardedInvalidProto = 4,
    /// `fallback_to_host` — base URL was derived from the `Host` header.
    FallbackToHost = 5,
    /// `fallback_to_default` — base URL fell back to the safe default.
    FallbackToDefault = 6,
}

impl BaseUrlReason {
    /// Stable u8 discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// The header/source that produced the chosen base URL.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BaseUrlSource {
    /// Derived from the RFC 7239 `Forwarded` header.
    Forwarded = 0,
    /// Derived from `X-Forwarded-Host` / `X-Forwarded-Proto`.
    XForwarded = 1,
    /// Derived from the `Host` request header.
    Host = 2,
    /// Safe built-in default (`http://localhost`).
    Default = 3,
}

impl BaseUrlSource {
    /// Stable u8 discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// Safe default base URL used when no valid host is available.
pub const DEFAULT_BASE_URL: &str = "http://localhost";

/// A parsed, config-time-validated trusted-proxy CIDR.
///
/// Parsing happens once at config time ([`parse_cidr`]); request-time
/// matching ([`Cidr::contains`]) only performs bitwise prefix comparison.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Cidr {
    /// IPv4 network: base address octets + prefix length (0-32).
    V4 { addr: [u8; 4], prefix_len: u8 },
    /// IPv6 network: base address octets + prefix length (0-128).
    V6 { addr: [u8; 16], prefix_len: u8 },
}

/// Error returned when a CIDR string cannot be parsed at config time.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CidrParseError;

impl Cidr {
    /// Return `true` if `ip` falls within this CIDR network.
    ///
    /// An IPv4-mapped IPv6 source address (`::ffff:a.b.c.d`) is matched
    /// against IPv4 CIDRs by unwrapping it to its embedded IPv4 address, so
    /// `::ffff:10.0.0.1` matches `10.0.0.0/8`.
    pub fn contains(&self, ip: IpAddr) -> bool {
        match (self, ip) {
            (Cidr::V4 { addr, prefix_len }, IpAddr::V4(v4)) => {
                prefix_match(addr, &v4.octets(), *prefix_len)
            }
            (Cidr::V4 { addr, prefix_len }, IpAddr::V6(v6)) => {
                // IPv4-mapped IPv6 matches IPv4 CIDRs via the embedded IPv4.
                match v6.to_ipv4_mapped() {
                    Some(v4) => prefix_match(addr, &v4.octets(), *prefix_len),
                    None => false,
                }
            }
            (Cidr::V6 { addr, prefix_len }, IpAddr::V6(v6)) => {
                prefix_match(addr, &v6.octets(), *prefix_len)
            }
            (Cidr::V6 { .. }, IpAddr::V4(_)) => false,
        }
    }
}

/// Compare the first `prefix_len` bits of two byte arrays.
fn prefix_match(network: &[u8], addr: &[u8], prefix_len: u8) -> bool {
    debug_assert_eq!(network.len(), addr.len());
    let mut bits = prefix_len as usize;
    let mut i = 0;
    while bits >= 8 {
        if network[i] != addr[i] {
            return false;
        }
        i += 1;
        bits -= 8;
    }
    if bits > 0 {
        let mask = 0xffu8 << (8 - bits);
        if (network[i] & mask) != (addr[i] & mask) {
            return false;
        }
    }
    true
}

/// Parse a single CIDR string (or bare address) at config time.
///
/// Accepts IPv4 (`10.0.0.0/8`, `192.168.1.1`) and IPv6 (`fd00::/8`,
/// `2001:db8::/32`, `::1`) forms.  A bare address is treated as a host route
/// (`/32` for IPv4, `/128` for IPv6).  The base address bits beyond the
/// prefix are masked to zero so matching is canonical.
///
/// # Errors
///
/// Returns [`CidrParseError`] for malformed addresses, out-of-range prefix
/// lengths, or non-numeric prefixes.
pub fn parse_cidr(s: &str) -> Result<Cidr, CidrParseError> {
    let s = s.trim();
    if s.is_empty() {
        return Err(CidrParseError);
    }

    let (addr_part, prefix_part) = match s.split_once('/') {
        Some((a, p)) => (a, Some(p)),
        None => (s, None),
    };

    let addr: IpAddr = addr_part.parse().map_err(|_| CidrParseError)?;

    match addr {
        IpAddr::V4(v4) => {
            let prefix_len = match prefix_part {
                Some(p) => parse_prefix(p, 32)?,
                None => 32,
            };
            let masked = mask_v4(v4.octets(), prefix_len);
            Ok(Cidr::V4 {
                addr: masked,
                prefix_len,
            })
        }
        IpAddr::V6(v6) => {
            let prefix_len = match prefix_part {
                Some(p) => parse_prefix(p, 128)?,
                None => 128,
            };
            let masked = mask_v6(v6.octets(), prefix_len);
            Ok(Cidr::V6 {
                addr: masked,
                prefix_len,
            })
        }
    }
}

/// Parse a prefix-length token, rejecting non-numeric or out-of-range values.
fn parse_prefix(p: &str, max: u8) -> Result<u8, CidrParseError> {
    if p.is_empty() || !p.bytes().all(|b| b.is_ascii_digit()) {
        return Err(CidrParseError);
    }
    let value: u8 = p.parse().map_err(|_| CidrParseError)?;
    if value > max {
        return Err(CidrParseError);
    }
    Ok(value)
}

/// Zero the host bits of an IPv4 address beyond `prefix_len`.
fn mask_v4(mut octets: [u8; 4], prefix_len: u8) -> [u8; 4] {
    apply_mask(&mut octets, prefix_len);
    octets
}

/// Zero the host bits of an IPv6 address beyond `prefix_len`.
fn mask_v6(mut octets: [u8; 16], prefix_len: u8) -> [u8; 16] {
    apply_mask(&mut octets, prefix_len);
    octets
}

/// Zero every bit at or beyond bit index `prefix_len`.
fn apply_mask(octets: &mut [u8], prefix_len: u8) {
    let mut bits = prefix_len as usize;
    for byte in octets.iter_mut() {
        if bits >= 8 {
            bits -= 8;
        } else if bits == 0 {
            *byte = 0;
        } else {
            let mask = 0xffu8 << (8 - bits);
            *byte &= mask;
            bits = 0;
        }
    }
}

/// Return `true` if `source_ip` matches any trusted CIDR.
///
/// The source IP is parsed from its textual form (the NGINX
/// `connection->addr_text` value, already realip/PROXY-protocol resolved).
/// A non-parseable or empty address never matches.  The `X-Forwarded-For`
/// header is intentionally **not** consulted here (spoofing avoidance).
pub fn is_trusted_source(source_ip: &str, trusted: &[Cidr]) -> bool {
    let ip: IpAddr = match source_ip.trim().parse() {
        Ok(ip) => ip,
        Err(_) => return false,
    };
    trusted.iter().any(|cidr| cidr.contains(ip))
}

/// Parsed view of the right-most element of an RFC 7239 `Forwarded` header.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct ForwardedHeader {
    /// `host=` parameter value (quotes stripped), if present.
    pub host: Option<String>,
    /// `proto=` parameter value (quotes stripped, lowercased), if present.
    pub proto: Option<String>,
}

/// Parse the right-most element of an RFC 7239 `Forwarded` header.
///
/// Multi-hop chains are comma-separated; trusted proxies append on the right,
/// so the right-most element is the value set by the trusted proxy (the
/// directly-connected source we validated).  The left-most element is
/// closest to the original client and is attacker-controlled when the
/// client pre-builds a `Forwarded` header before the trusted proxy appends
/// its own.  Using the right-most element prevents the client from
/// poisoning the base URL via a pre-pended malicious `host=`.
///
/// Each element is a list of `;`-separated `key=value` pairs.  Quoted values
/// (`host="example.com:8080"`) are unquoted.  Returns `None` when the header
/// is empty or the right-most element carries neither `host` nor `proto`.
pub fn parse_forwarded_header(s: &str) -> Option<ForwardedHeader> {
    // Use the right-most (last) comma-separated element: this is the value
    // the trusted proxy appended.  The left-most is client-controlled.
    let last = s.rsplit(',').next()?.trim();
    if last.is_empty() {
        return None;
    }

    let mut result = ForwardedHeader::default();
    for pair in last.split(';') {
        let pair = pair.trim();
        let Some((key, value)) = pair.split_once('=') else {
            continue;
        };
        let key = key.trim().to_ascii_lowercase();
        let value = unquote(value.trim());
        match key.as_str() {
            "host" if result.host.is_none() => {
                result.host = Some(value.to_string());
            }
            "proto" if result.proto.is_none() => {
                result.proto = Some(value.to_ascii_lowercase());
            }
            _ => {}
        }
    }

    if result.host.is_none() && result.proto.is_none() {
        None
    } else {
        Some(result)
    }
}

/// Strip a single layer of surrounding double quotes from a token.
fn unquote(value: &str) -> &str {
    let bytes = value.as_bytes();
    if bytes.len() >= 2 && bytes[0] == b'"' && bytes[bytes.len() - 1] == b'"' {
        &value[1..value.len() - 1]
    } else {
        value
    }
}

/// Validate a forwarded/host-header host value, covering all known attack
/// vectors (spec 47 Requirement 6).
///
/// Returns the normalized host (unchanged on success) when valid, or `None`
/// when the value is empty, contains control characters (CRLF injection), a
/// comma (multi-hop confusion), `@` (userinfo injection), `/` or `?` (path
/// injection), an out-of-range port, or a malformed IPv6 bracket form.
///
/// IDNA handling: ASCII `xn--` punycode labels pass through unchanged;
/// non-ASCII (raw Unicode IDN) is rejected to avoid IDN homograph spoofing.
pub fn validate_host(host: &str) -> Option<String> {
    if host.is_empty() {
        return None;
    }

    // Control characters (CRLF / NUL / TAB) → reject (CRLF injection).
    if host.bytes().any(|b| b < 0x20 || b == 0x7f) {
        return None;
    }

    // Reject confusion / injection characters outright.
    if host.contains(',')
        || host.contains('@')
        || host.contains('/')
        || host.contains('?')
        || host.contains(' ')
        || host.contains('\\')
        || host.contains('#')
    {
        return None;
    }

    // Reject raw Unicode (non-ASCII) to avoid IDN homograph spoofing;
    // pre-encoded ASCII punycode (xn--) is allowed because it is already
    // unambiguous ASCII.
    if !host.is_ascii() {
        return None;
    }

    if let Some(rest) = host.strip_prefix('[') {
        return validate_bracketed_ipv6(rest);
    }

    // Reject stray brackets outside the leading-bracket IPv6 form.
    if host.contains('[') || host.contains(']') {
        return None;
    }

    // Optional port after the final colon. A bare IPv6 without brackets is
    // ambiguous (multiple colons) and therefore rejected here.
    match host.split_once(':') {
        Some((name, port)) => {
            if name.is_empty() || port.contains(':') {
                return None;
            }
            validate_port(port)?;
            Some(host.to_string())
        }
        None => Some(host.to_string()),
    }
}

/// Validate the bracketed IPv6 host form `[addr]` or `[addr]:port`.
///
/// `rest` is the substring after the leading `[`.
fn validate_bracketed_ipv6(rest: &str) -> Option<String> {
    let close = rest.find(']')?;
    let addr = &rest[..close];
    let after = &rest[close + 1..];

    // The bracketed content must be a valid IPv6 literal.
    addr.parse::<Ipv6Addr>().ok()?;

    if after.is_empty() {
        return Some(format!("[{addr}]"));
    }

    let port = after.strip_prefix(':')?;
    validate_port(port)?;
    Some(format!("[{addr}]:{port}"))
}

/// Validate a TCP port string in the range 1-65535.
fn validate_port(port: &str) -> Option<()> {
    if port.is_empty() || !port.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let value: u32 = port.parse().ok()?;
    if (1..=65535).contains(&value) {
        Some(())
    } else {
        None
    }
}

/// Validate a forwarded scheme/proto value: only `http` / `https` are valid.
pub fn validate_proto(proto: &str) -> Option<String> {
    let lower = proto.trim().to_ascii_lowercase();
    if lower == "http" || lower == "https" {
        Some(lower)
    } else {
        None
    }
}

/// Outcome of a base-URL trust decision.
#[derive(Debug, PartialEq, Eq)]
pub struct BaseUrlDecision {
    /// The chosen, validated base URL (always non-empty).
    pub base_url: String,
    /// Why this base URL was chosen.
    pub reason: BaseUrlReason,
    /// Which input produced the base URL.
    pub source: BaseUrlSource,
}

/// Inputs for [`decide_base_url`], marshaled by the C thin wrapper.
#[derive(Debug, Default)]
pub struct BaseUrlInput<'a> {
    /// Textual source IP from `r->connection->addr_text` (realip/PROXY
    /// resolved). Empty / non-parseable means "no usable source IP".
    pub source_ip: &'a str,
    /// `true` when the source is a Unix-domain socket peer (never trusted
    /// unless an explicit loopback CIDR is configured, which a Unix socket
    /// cannot match — so this forces the untrusted path).
    pub is_unix_socket: bool,
    /// `true` when `markdown_trusted_proxies` was configured (even as `off`,
    /// which yields an empty CIDR list).
    pub trusted_configured: bool,
    /// `Forwarded` header value (RFC 7239), if present.
    pub forwarded: Option<&'a str>,
    /// `X-Forwarded-Proto` header value, if present.
    pub x_forwarded_proto: Option<&'a str>,
    /// `X-Forwarded-Host` header value, if present.
    pub x_forwarded_host: Option<&'a str>,
    /// `Host` request header value, if present.
    pub host: Option<&'a str>,
    /// The direct connection scheme from `r->schema` (e.g. "https"),
    /// used as the base URL scheme when falling back to the Host header
    /// or the safe default.  This preserves the actual connection
    /// protocol for direct (non-proxied) HTTPS requests so relative
    /// links are not erroneously resolved as http://.
    pub direct_scheme: Option<&'a str>,
}

/// Pure base-URL trust decision (spec 47 small API).
///
/// Same input → same output.  See the module docs for the decision order.
/// The returned base URL is always non-empty; on total failure it is
/// [`DEFAULT_BASE_URL`].
pub fn decide_base_url(input: &BaseUrlInput, trusted: &[Cidr]) -> BaseUrlDecision {
    // 1. Trust not configured → ignore forwarded headers.
    if !input.trusted_configured {
        return host_fallback(input.host, input.direct_scheme,
                              BaseUrlReason::TrustedProxiesNotConfigured);
    }

    // 2. Untrusted source (including Unix socket) → ignore forwarded headers.
    if input.is_unix_socket || !is_trusted_source(input.source_ip, trusted) {
        return host_fallback(input.host, input.direct_scheme,
                              BaseUrlReason::ForwardedHeaderUntrusted);
    }

    // 3. Trusted source: still strictly validate the forwarded data.
    //    Forwarded (RFC 7239) takes precedence over X-Forwarded-*.
    if let Some(decision) = decide_from_forwarded(input) {
        return decision;
    }

    // 4. No usable forwarded data → fall back to Host / default.
    host_fallback(input.host, input.direct_scheme,
                  BaseUrlReason::FallbackToHost)
}

/// Try to build a base URL from the trusted forwarded data.
///
/// Returns `Some(decision)` when a terminal decision is reached (valid
/// forwarded URL, or an explicit invalid-host/proto fallback).  Returns
/// `None` when no forwarded host/proto candidate was present at all, so the
/// caller should fall through to the `Host` header.
fn decide_from_forwarded(input: &BaseUrlInput) -> Option<BaseUrlDecision> {
    // Resolve the candidate host/proto, preferring the Forwarded header.
    let (raw_host, raw_proto, source) = resolve_forwarded_candidate(input)?;

    // Validate host first (strongest reason to reject).
    let Some(valid_host) = raw_host.as_deref().and_then(validate_host) else {
        // A proto-only forwarded element with no host is not actionable;
        // treat a present-but-invalid host as invalid_host, and a missing
        // host as "fall through to Host header".
        if raw_host.is_some() {
            return Some(host_fallback(
                input.host, input.direct_scheme,
                BaseUrlReason::ForwardedInvalidHost,
            ));
        }
        return Some(host_fallback(input.host, input.direct_scheme,
                                   BaseUrlReason::FallbackToHost));
    };

    // Validate proto when provided; default to https when absent.
    let scheme = match raw_proto.as_deref() {
        Some(p) => match validate_proto(p) {
            Some(s) => s,
            None => {
                return Some(host_fallback(
                    input.host, input.direct_scheme,
                    BaseUrlReason::ForwardedInvalidProto,
                ));
            }
        },
        None => "https".to_string(),
    };

    Some(BaseUrlDecision {
        base_url: format!("{scheme}://{valid_host}"),
        reason: BaseUrlReason::ForwardedHeaderTrusted,
        source,
    })
}

/// Resolve the candidate (host, proto, source) from forwarded inputs.
///
/// Prefers the RFC 7239 `Forwarded` header (right-most element, which is the
/// value set by the trusted proxy — see [`parse_forwarded_header`] for the
/// security rationale); falls back to `X-Forwarded-Host` /
/// `X-Forwarded-Proto`.  Returns `None` when neither is present.
fn resolve_forwarded_candidate(
    input: &BaseUrlInput,
) -> Option<(Option<String>, Option<String>, BaseUrlSource)> {
    if let Some(raw) = input.forwarded
        && let Some(parsed) = parse_forwarded_header(raw)
    {
        return Some((parsed.host, parsed.proto, BaseUrlSource::Forwarded));
    }

    let xfh = input
        .x_forwarded_host
        .map(str::trim)
        .filter(|s| !s.is_empty());
    let xfp = input
        .x_forwarded_proto
        .map(str::trim)
        .filter(|s| !s.is_empty());

    if xfh.is_none() && xfp.is_none() {
        return None;
    }

    Some((
        xfh.map(str::to_string),
        xfp.map(str::to_string),
        BaseUrlSource::XForwarded,
    ))
}

/// Build a decision from the `Host` header, or the safe default when the
/// `Host` header is absent/invalid.
///
/// Uses `direct_scheme` (from `r->schema`) when provided, defaulting to
/// "http" for backward compatibility.  This preserves the actual connection
/// protocol for direct HTTPS requests so relative links are not erroneously
/// resolved as http://.
fn host_fallback(host: Option<&str>, direct_scheme: Option<&str>,
                 reason: BaseUrlReason) -> BaseUrlDecision {
    let scheme = direct_scheme
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("http");

    if let Some(h) = host
        && let Some(valid) = validate_host(h.trim())
    {
        return BaseUrlDecision {
            base_url: format!("{scheme}://{valid}"),
            reason,
            source: BaseUrlSource::Host,
        };
    }

    BaseUrlDecision {
        base_url: DEFAULT_BASE_URL.to_string(),
        reason: BaseUrlReason::FallbackToDefault,
        source: BaseUrlSource::Default,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cidrs(list: &[&str]) -> Vec<Cidr> {
        list.iter().map(|s| parse_cidr(s).unwrap()).collect()
    }

    /* ---- CIDR parsing ---- */

    #[test]
    fn parse_ipv4_cidr() {
        assert_eq!(
            parse_cidr("10.0.0.0/8").unwrap(),
            Cidr::V4 {
                addr: [10, 0, 0, 0],
                prefix_len: 8
            }
        );
    }

    #[test]
    fn parse_ipv4_bare_is_host_route() {
        assert_eq!(
            parse_cidr("192.168.1.1").unwrap(),
            Cidr::V4 {
                addr: [192, 168, 1, 1],
                prefix_len: 32
            }
        );
    }

    #[test]
    fn parse_ipv4_masks_host_bits() {
        // 10.1.2.3/8 canonicalizes to 10.0.0.0/8.
        assert_eq!(
            parse_cidr("10.1.2.3/8").unwrap(),
            Cidr::V4 {
                addr: [10, 0, 0, 0],
                prefix_len: 8
            }
        );
    }

    #[test]
    fn parse_ipv6_cidr() {
        match parse_cidr("2001:db8::/32").unwrap() {
            Cidr::V6 { prefix_len, .. } => assert_eq!(prefix_len, 32),
            other => panic!("expected V6, got {other:?}"),
        }
    }

    #[test]
    fn parse_ipv6_loopback_host_route() {
        match parse_cidr("::1/128").unwrap() {
            Cidr::V6 { prefix_len, addr } => {
                assert_eq!(prefix_len, 128);
                assert_eq!(addr[15], 1);
            }
            other => panic!("expected V6, got {other:?}"),
        }
    }

    #[test]
    fn parse_invalid_cidr_rejected() {
        for bad in [
            "",
            "not-an-ip",
            "10.0.0.0/33",
            "10.0.0.0/-1",
            "10.0.0.0/abc",
            "2001:db8::/129",
            "999.0.0.0/8",
            "10.0.0.0/",
        ] {
            assert!(parse_cidr(bad).is_err(), "should reject: {bad}");
        }
    }

    /* ---- CIDR matching ---- */

    #[test]
    fn ipv4_match_within_and_outside() {
        let t = cidrs(&["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]);
        assert!(is_trusted_source("10.5.6.7", &t));
        assert!(is_trusted_source("172.16.99.1", &t));
        assert!(is_trusted_source("192.168.1.1", &t));
        assert!(!is_trusted_source("11.0.0.1", &t));
        assert!(!is_trusted_source("8.8.8.8", &t));
    }

    #[test]
    fn ipv6_match_within_and_outside() {
        let t = cidrs(&["fd00::/8", "2001:db8::/32", "::1/128"]);
        assert!(is_trusted_source("fd00::1234", &t));
        assert!(is_trusted_source("2001:db8::dead:beef", &t));
        assert!(is_trusted_source("::1", &t));
        assert!(!is_trusted_source("2001:dead::1", &t));
        assert!(!is_trusted_source("fe80::1", &t));
    }

    #[test]
    fn ipv4_mapped_ipv6_matches_ipv4_cidr() {
        let t = cidrs(&["10.0.0.0/8"]);
        // ::ffff:10.0.0.1 is an IPv4-mapped address.
        assert!(is_trusted_source("::ffff:10.0.0.1", &t));
        assert!(!is_trusted_source("::ffff:11.0.0.1", &t));
    }

    #[test]
    fn ipv4_cidr_does_not_match_native_ipv6() {
        let t = cidrs(&["10.0.0.0/8"]);
        assert!(!is_trusted_source("2001:db8::1", &t));
    }

    #[test]
    fn unparseable_or_empty_source_never_trusted() {
        let t = cidrs(&["0.0.0.0/0"]);
        assert!(!is_trusted_source("", &t));
        assert!(!is_trusted_source("garbage", &t));
        // But a real address matches 0.0.0.0/0.
        assert!(is_trusted_source("1.2.3.4", &t));
    }

    /* ---- Forwarded header parsing ---- */

    #[test]
    fn forwarded_basic() {
        let p = parse_forwarded_header("for=1.2.3.4;host=example.com;proto=https").unwrap();
        assert_eq!(p.host.as_deref(), Some("example.com"));
        assert_eq!(p.proto.as_deref(), Some("https"));
    }

    #[test]
    fn forwarded_quoted_host() {
        let p = parse_forwarded_header("host=\"example.com:8080\";proto=\"https\"").unwrap();
        assert_eq!(p.host.as_deref(), Some("example.com:8080"));
        assert_eq!(p.proto.as_deref(), Some("https"));
    }

    #[test]
    fn forwarded_multi_hop_takes_rightmost() {
        // Trusted proxies append on the right; the right-most element is
        // the one set by the trusted proxy.  The left-most is client-
        // controlled and must not be used.
        let p = parse_forwarded_header("host=client.evil.com, host=proxy.internal").unwrap();
        assert_eq!(p.host.as_deref(), Some("proxy.internal"));
    }

    #[test]
    fn forwarded_proto_lowercased() {
        let p = parse_forwarded_header("proto=HTTPS").unwrap();
        assert_eq!(p.proto.as_deref(), Some("https"));
    }

    #[test]
    fn forwarded_empty_or_no_known_params() {
        assert!(parse_forwarded_header("").is_none());
        assert!(parse_forwarded_header("   ").is_none());
        assert!(parse_forwarded_header("for=1.2.3.4").is_none());
    }

    /* ---- Host validation ---- */

    #[test]
    fn host_valid_plain_and_port() {
        assert_eq!(validate_host("example.com").as_deref(), Some("example.com"));
        assert_eq!(
            validate_host("example.com:8080").as_deref(),
            Some("example.com:8080")
        );
    }

    #[test]
    fn host_valid_punycode() {
        assert_eq!(
            validate_host("xn--80ak6aa92e.com").as_deref(),
            Some("xn--80ak6aa92e.com")
        );
    }

    #[test]
    fn host_valid_ipv6_bracket() {
        assert_eq!(
            validate_host("[2001:db8::1]").as_deref(),
            Some("[2001:db8::1]")
        );
        assert_eq!(
            validate_host("[2001:db8::1]:8080").as_deref(),
            Some("[2001:db8::1]:8080")
        );
    }

    #[test]
    fn host_rejects_attack_vectors() {
        // empty
        assert_eq!(validate_host(""), None);
        // control chars / CRLF injection
        assert_eq!(validate_host("evil.com\r\nSet-Cookie: x"), None);
        assert_eq!(validate_host("evil.com\tx"), None);
        assert_eq!(validate_host("evil.com\0"), None);
        // comma chain confusion
        assert_eq!(validate_host("a.com,b.com"), None);
        // userinfo injection
        assert_eq!(validate_host("user@evil.com"), None);
        // path injection
        assert_eq!(validate_host("evil.com/path"), None);
        assert_eq!(validate_host("evil.com?x=1"), None);
        // raw unicode IDN (homograph)
        assert_eq!(validate_host("xn--exmple-cua.com\u{0430}"), None);
        assert_eq!(validate_host("еxample.com"), None);
        // bad port
        assert_eq!(validate_host("example.com:0"), None);
        assert_eq!(validate_host("example.com:70000"), None);
        assert_eq!(validate_host("example.com:abc"), None);
        // malformed bracket
        assert_eq!(validate_host("[2001:db8::1"), None);
        assert_eq!(validate_host("[notv6]"), None);
        // bare ipv6 without brackets (ambiguous)
        assert_eq!(validate_host("2001:db8::1"), None);
    }

    #[test]
    fn proto_validation() {
        assert_eq!(validate_proto("http").as_deref(), Some("http"));
        assert_eq!(validate_proto("HTTPS").as_deref(), Some("https"));
        assert_eq!(validate_proto("ftp"), None);
        assert_eq!(validate_proto("javascript"), None);
        assert_eq!(validate_proto(""), None);
    }

    /* ---- decide_base_url ---- */

    fn trusted_input<'a>(source_ip: &'a str) -> BaseUrlInput<'a> {
        BaseUrlInput {
            source_ip,
            is_unix_socket: false,
            trusted_configured: true,
            forwarded: None,
            x_forwarded_proto: None,
            x_forwarded_host: None,
            host: Some("origin.example.com"),
            direct_scheme: None,
        }
    }

    #[test]
    fn decide_not_configured_uses_host() {
        let mut input = trusted_input("10.0.0.1");
        input.trusted_configured = false;
        input.x_forwarded_host = Some("spoof.example.com");
        let d = decide_base_url(&input, &[]);
        assert_eq!(d.reason, BaseUrlReason::TrustedProxiesNotConfigured);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "http://origin.example.com");
    }

    #[test]
    fn decide_untrusted_source_ignores_forwarded() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("203.0.113.7");
        input.x_forwarded_host = Some("spoof.example.com");
        input.x_forwarded_proto = Some("https");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderUntrusted);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "http://origin.example.com");
    }

    #[test]
    fn decide_trusted_uses_x_forwarded() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("https");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
        assert_eq!(d.source, BaseUrlSource::XForwarded);
        assert_eq!(d.base_url, "https://api.example.com");
    }

    #[test]
    fn decide_forwarded_precedence_over_x_forwarded() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some("host=fwd.example.com;proto=https");
        input.x_forwarded_host = Some("xfwd.example.com");
        input.x_forwarded_proto = Some("http");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.source, BaseUrlSource::Forwarded);
        assert_eq!(d.base_url, "https://fwd.example.com");
    }

    #[test]
    fn decide_forwarded_multi_hop_rightmost() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some("host=client.evil.com, host=proxy.internal");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.source, BaseUrlSource::Forwarded);
        // proto absent → defaults to https
        assert_eq!(d.base_url, "https://proxy.internal");
    }

    #[test]
    fn decide_forwarded_client_prepend_rejected() {
        // A client pre-builds a Forwarded header with a malicious host,
        // then the trusted proxy appends its own element on the right.
        // The module must use the right-most (proxy) element, not the
        // client-prepended left-most one.
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some("host=evil.com;proto=http, host=api.example.com;proto=https");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.source, BaseUrlSource::Forwarded);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
        assert_eq!(d.base_url, "https://api.example.com");
    }

    #[test]
    fn decide_invalid_forwarded_host_falls_back() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_host = Some("evil.com\r\ninjection");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidHost);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "http://origin.example.com");
    }

    #[test]
    fn decide_invalid_proto_falls_back() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("javascript");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidProto);
        assert_eq!(d.source, BaseUrlSource::Host);
    }

    #[test]
    fn decide_unix_socket_is_untrusted() {
        let t = cidrs(&["127.0.0.0/8"]);
        let mut input = trusted_input("127.0.0.1");
        input.is_unix_socket = true;
        input.x_forwarded_host = Some("spoof.example.com");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderUntrusted);
    }

    #[test]
    fn decide_no_forwarded_falls_back_to_host() {
        let t = cidrs(&["10.0.0.0/8"]);
        let input = trusted_input("10.1.2.3");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::FallbackToHost);
        assert_eq!(d.source, BaseUrlSource::Host);
    }

    #[test]
    fn decide_no_host_falls_back_to_default() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("203.0.113.7");
        input.host = None;
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::FallbackToDefault);
        assert_eq!(d.source, BaseUrlSource::Default);
        assert_eq!(d.base_url, DEFAULT_BASE_URL);
    }

    #[test]
    fn decide_is_idempotent() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("https");
        assert_eq!(decide_base_url(&input, &t), decide_base_url(&input, &t));
    }

    #[test]
    fn decide_direct_https_preserves_scheme() {
        // No trusted proxies configured → Host header fallback.
        // direct_scheme=https from r->schema must produce https:// URL.
        let mut input = trusted_input("10.0.0.1");
        input.trusted_configured = false;
        input.direct_scheme = Some("https");
        let d = decide_base_url(&input, &[]);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "https://origin.example.com");
    }

    #[test]
    fn decide_untrusted_source_https_preserves_scheme() {
        // Untrusted source, no forwarded headers used.
        // Direct HTTPS connection should still use https:// scheme.
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("203.0.113.7");
        input.x_forwarded_host = Some("spoof.example.com");
        input.direct_scheme = Some("https");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderUntrusted);
        assert_eq!(d.base_url, "https://origin.example.com");
    }

    #[test]
    fn decide_no_host_https_falls_back_to_default() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("203.0.113.7");
        input.host = None;
        input.direct_scheme = Some("https");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::FallbackToDefault);
        assert_eq!(d.source, BaseUrlSource::Default);
        assert_eq!(d.base_url, DEFAULT_BASE_URL);
    }
}
