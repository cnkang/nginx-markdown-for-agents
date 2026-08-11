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
//!    comma chains are walked right-to-left, trusted proxy hops are stripped,
//!    and the first untrusted hop supplies the client-facing value. Bracketed
//!    IPv6 addresses are accepted for matching; bracketed IPv4 addresses are
//!    not trusted.
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
    /// `forwarded_malformed` — the RFC 7239 `Forwarded` source is malformed
    /// (grammar, duplicate parameters, or invalid value); the entire
    /// forwarded set is discarded and direct peer/direct request metadata is
    /// used. X-Forwarded-* is never retried after a malformed Forwarded.
    ForwardedMalformed = 7,
    /// `xforwarded_mismatch` — X-Forwarded-* metadata lists are partial or
    /// have mismatched lengths; the entire forwarded set is discarded.
    XForwardedMismatch = 8,
    /// `chain_exhausted` — right-to-left trusted-hop stripping removed every
    /// address in the chain; the forwarded set is discarded and direct
    /// peer/direct request metadata is used.
    ChainExhausted = 9,
    /// `forwarded_invalid_value` — an address/scheme/host/port value in the
    /// forwarded set failed validation (unknown, obfuscated, userinfo,
    /// control character, malformed IPv6, zone ID, invalid scheme/port/host);
    /// the entire forwarded set is discarded.
    ForwardedInvalidValue = 10,
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
    let trimmed = source_ip.trim();
    /* validate_forwarded_addr returns bracketed IPv6 literals (e.g.
     * "[2001:db8::1]") as-is.  IpAddr::parse rejects the bracketed form,
     * so strip a single matching pair of brackets before parsing.
     * Only strip when the inner content is a valid IPv6 address; a
     * bracketed IPv4 literal (e.g. "[1.2.3.4]") is not a valid forwarded
     * address form and must not be trusted. */
    let normalized: &str =
        if trimmed.len() >= 2 && trimmed.starts_with('[') && trimmed.ends_with(']') {
            let inner = &trimmed[1..trimmed.len() - 1];
            if inner.parse::<Ipv6Addr>().is_err() {
                return false;
            }
            inner
        } else {
            trimmed
        };
    let ip: IpAddr = match normalized.parse() {
        Ok(ip) => ip,
        Err(_) => return false,
    };
    trusted.iter().any(|cidr| cidr.contains(ip))
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
    /// `X-Forwarded-For` header value (address chain), if present.  Contains
    /// only addresses, never host/scheme/port.
    pub x_forwarded_for: Option<&'a str>,
    /// `X-Forwarded-Proto` header value, if present.
    pub x_forwarded_proto: Option<&'a str>,
    /// `X-Forwarded-Host` header value, if present.
    pub x_forwarded_host: Option<&'a str>,
    /// `X-Forwarded-Port` header value, if present.
    pub x_forwarded_port: Option<&'a str>,
    /// `Host` request header value, if present.
    pub host: Option<&'a str>,
    /// The direct connection scheme from `r->schema` (e.g. "https"),
    /// used as the base URL scheme when falling back to the Host header
    /// or the safe default.  This preserves the actual connection
    /// protocol for direct (non-proxied) HTTPS requests so relative
    /// links are not erroneously resolved as http://.
    pub direct_scheme: Option<&'a str>,
}

/// Pure base-URL trust decision for the multi-hop forwarding algorithm.
///
/// Same input → same output.  Implements the authoritative trusted-proxy
/// decision algorithm of Requirement 13:
///
/// 1. Trust not configured → ignore every forwarded header.
/// 2. Direct peer not trusted → ignore every forwarded header.
/// 3. Any `Forwarded` field present → parse repeated fields in received
///    order and use that source as a unit; a malformed `Forwarded` source is
///    never repaired by trying `X-Forwarded-*`.
/// 4. Otherwise require `X-Forwarded-For`; optional host/proto/port metadata
///    lists are valid only when all absent or all present with the same
///    length as the address list.
/// 5. Reject `unknown`, obfuscated identifiers, userinfo, control
///    characters, malformed IPv6, zone IDs, invalid schemes, invalid ports,
///    and hosts outside the ASCII DNS-label / bracketed IPv6-literal policy.
/// 6. Walk the address chain right-to-left, strip only trusted proxy
///    addresses, select the first remaining address, and take host/proto/
///    port from that same hop index.  Chain exhaustion discards the set.
/// 7. Any parse, count, alignment, or validation failure discards the
///    complete forwarded set and returns direct peer/direct request
///    metadata.
pub fn decide_base_url(input: &BaseUrlInput, trusted: &[Cidr]) -> BaseUrlDecision {
    // Step 1: trust not configured → ignore forwarded headers.
    if !input.trusted_configured {
        return host_fallback(
            input.host,
            input.direct_scheme,
            BaseUrlReason::TrustedProxiesNotConfigured,
        );
    }

    // Step 2: untrusted source (including Unix socket) → ignore forwarded
    // headers and use the direct peer.
    if input.is_unix_socket || !is_trusted_source(input.source_ip, trusted) {
        return host_fallback(
            input.host,
            input.direct_scheme,
            BaseUrlReason::ForwardedHeaderUntrusted,
        );
    }

    // Step 3: Forwarded is a complete source; malformed Forwarded discards
    // the whole set and never falls back to X-Forwarded-*.
    if input.forwarded.is_some() {
        return decide_from_forwarded(input, trusted);
    }

    // Steps 4-6: X-Forwarded-* path (mandatory X-Forwarded-For).
    decide_from_xforwarded(input, trusted)
}

/// One parsed RFC 7239 `Forwarded` element (single hop).
#[derive(Debug, Default, PartialEq, Eq)]
struct ForwardedElement {
    /// `for=` parameter value (quotes stripped), if present.
    for_addr: Option<String>,
    /// `proto=` parameter value (quotes stripped, lowercased), if present.
    proto: Option<String>,
    /// `host=` parameter value (quotes stripped), if present.
    host: Option<String>,
}

/// Parse all RFC 7239 `Forwarded` elements in received order.
///
/// Elements are comma-separated; commas inside quoted strings do not split.
/// Within one element, `;`-separated `key=value` pairs are parsed; duplicate
/// parameters in one element are invalid.  Returns `None` on malformed
/// grammar (empty element, missing `=`, unbalanced quotes, duplicate
/// parameters).
fn parse_forwarded_elements(s: &str) -> Option<Vec<ForwardedElement>> {
    let elements = split_quoted(s, b',')
        .into_iter()
        .map(|raw| {
            let raw = raw.trim();
            if raw.is_empty() {
                None
            } else {
                parse_forwarded_element(raw)
            }
        })
        .collect::<Option<Vec<_>>>()?;
    (!elements.is_empty()).then_some(elements)
}

fn parse_forwarded_element(raw: &str) -> Option<ForwardedElement> {
    let mut element = ForwardedElement::default();
    let mut seen: Vec<String> = Vec::new();
    let mut any_param = false;
    for pair in split_quoted(raw, b';') {
        let pair = pair.trim();
        if pair.is_empty() {
            if any_param {
                return None;
            }
            continue;
        }
        any_param = true;
        parse_forwarded_pair(pair, &mut element, &mut seen)?;
    }
    Some(element)
}

fn parse_forwarded_pair(
    pair: &str,
    element: &mut ForwardedElement,
    seen: &mut Vec<String>,
) -> Option<()> {
    let (key, value) = pair.split_once('=')?;
    let key = key.trim().to_ascii_lowercase();
    if key.is_empty() || seen.contains(&key) {
        return None;
    }
    seen.push(key.clone());
    let value = unquote(value.trim())?;
    match key.as_str() {
        "for" => element.for_addr = Some(value.to_string()),
        "proto" => element.proto = Some(value.to_ascii_lowercase()),
        "host" => element.host = Some(value.to_string()),
        _ => {}
    }
    Some(())
}

/// Split a value on `sep`, honoring double-quoted strings.
///
/// Returns the unquoted member slices.  `None` is never returned: unbalanced
/// quotes are surfaced by the caller through `unquote` returning `None`.
fn split_quoted(s: &str, sep: u8) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    let mut in_quote = false;
    let mut chars = s.chars().peekable();
    while let Some(c) = chars.next() {
        if in_quote {
            current.push(c);
            if c == '\\' {
                if let Some(esc) = chars.next() {
                    current.push(esc);
                }
            } else if c == '"' {
                in_quote = false;
            }
        } else if c == '"' {
            in_quote = true;
            current.push(c);
        } else if c as u32 == sep as u32 {
            parts.push(current.clone());
            current.clear();
        } else {
            current.push(c);
        }
    }
    parts.push(current);
    parts
}

/// Strip a single layer of surrounding double quotes from a token.
///
/// Returns `None` when the token has unbalanced quotes (a `"` appears
/// without a matching closer, or a closer appears without an opener).
fn unquote(value: &str) -> Option<String> {
    if !value.starts_with('"') {
        if value.contains('"') {
            return None;
        }
        return Some(value.to_string());
    }

    let mut out = String::with_capacity(value.len());
    let mut chars = value.chars();
    let _opening_quote = chars.next();
    let mut closed = false;
    while let Some(c) = chars.next() {
        if closed {
            if c != ' ' && c != '\t' {
                return None;
            }
            continue;
        }

        match c {
            '"' => closed = true,
            '\\' => out.push(chars.next()?),
            _ => out.push(c),
        }
    }
    closed.then_some(out)
}

/// Validate a `for=` forwarded address: a literal IPv4 or bracketed IPv6.
///
/// Rejects `unknown`, obfuscated identifiers beginning with `_`, control
/// characters, malformed IPv6, and zone IDs.  A trailing dot is removed only
/// for comparison, never emitted as metadata (applies to host forms).
fn validate_forwarded_addr(addr: &str) -> Option<String> {
    if addr.is_empty() || has_invalid_forwarded_characters(addr) {
        return None;
    }
    let lowered = addr.trim().to_ascii_lowercase();
    if lowered == "unknown" {
        return None;
    }
    if lowered.starts_with('_') {
        return None;
    }
    /* Bare IPv4 or IPv6 literal. */
    if lowered.parse::<IpAddr>().is_ok() {
        return Some(lowered);
    }
    /* Bracketed IPv6. */
    if let Some(rest) = lowered.strip_prefix('[')
        && let Some(close) = rest.find(']')
        && rest[close + 1..].is_empty()
        && rest[..close].parse::<Ipv6Addr>().is_ok()
    {
        return Some(lowered);
    }
    None
}

/// Strict forwarded-host validator: ASCII DNS-label or bracketed
/// IPv6-literal form, output lowercase.
///
/// A trailing dot is removed only for comparison.  An optional `:port`
/// (1-65535) is accepted after the host form.  IDNA conversion is not
/// performed.
fn validate_forwarded_host(host: &str) -> Option<String> {
    if host.is_empty() || has_invalid_forwarded_characters(host) {
        return None;
    }
    let lowered = host.trim().to_ascii_lowercase();

    if let Some(rest) = lowered.strip_prefix('[') {
        return validate_bracketed_host(rest);
    }

    if lowered.contains('[') || lowered.contains(']') {
        return None;
    }

    /* Optional :port after the host. */
    let (name, port) = match lowered.split_once(':') {
        Some((n, p)) => (n, Some(p)),
        None => (lowered.as_str(), None),
    };
    if name.is_empty() {
        return None;
    }
    if let Some(p) = port {
        if p.contains(':') {
            return None;
        }
        validate_port(p)?;
    }

    let trimmed = validate_dns_name(name)?;

    match port {
        Some(p) => Some(format!("{trimmed}:{p}")),
        None => Some(trimmed.to_string()),
    }
}

fn has_invalid_forwarded_characters(value: &str) -> bool {
    value.bytes().any(|b| b < 0x20 || b == 0x7f)
}

fn validate_bracketed_host(rest: &str) -> Option<String> {
    let close = rest.find(']')?;
    let addr = &rest[..close];
    let after = &rest[close + 1..];
    addr.parse::<Ipv6Addr>().ok()?;
    if after.is_empty() {
        return Some(format!("[{addr}]"));
    }
    let port = after.strip_prefix(':')?;
    validate_port(port)?;
    Some(format!("[{addr}]:{port}"))
}

fn validate_dns_name(name: &str) -> Option<&str> {
    let trimmed = name.strip_suffix('.').unwrap_or(name);
    if trimmed.is_empty() {
        return None;
    }
    for label in trimmed.split('.') {
        if label.is_empty() || label.len() > 63 {
            return None;
        }
        let bytes = label.as_bytes();
        if bytes[0] == b'-' || bytes[bytes.len() - 1] == b'-' {
            return None;
        }
        if !bytes
            .iter()
            .all(|b| b.is_ascii_alphanumeric() || *b == b'-')
        {
            return None;
        }
    }
    (trimmed.len() <= 253).then_some(trimmed)
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

/// Decide from the RFC 7239 `Forwarded` source (a complete unit).
///
/// Any malformed grammar, duplicate parameter, or invalid value discards the
/// entire forwarded set; `X-Forwarded-*` is never retried.
fn decide_from_forwarded(input: &BaseUrlInput, trusted: &[Cidr]) -> BaseUrlDecision {
    let raw = input.forwarded.unwrap_or_default();
    let Some(mut elements) = parse_forwarded_elements(raw) else {
        return discard_forwarded_set(input, BaseUrlReason::ForwardedMalformed);
    };

    /* Validate every element's values; any failure discards the set. */
    for element in &mut elements {
        if let Some(for_addr) = &element.for_addr {
            let Some(normalized) = validate_forwarded_addr(for_addr) else {
                return discard_forwarded_set(input, BaseUrlReason::ForwardedInvalidValue);
            };
            element.for_addr = Some(normalized);
        }
        if let Some(proto) = &element.proto
            && validate_proto(proto).is_none()
        {
            return discard_forwarded_set(input, BaseUrlReason::ForwardedInvalidValue);
        }
        if let Some(host) = &element.host
            && validate_forwarded_host(host).is_none()
        {
            return discard_forwarded_set(input, BaseUrlReason::ForwardedInvalidValue);
        }
    }

    /* Walk right-to-left, stripping elements whose for= is a trusted proxy
     * address; select the first remaining element as a unit. */
    for element in elements.iter().rev() {
        let trusted_hop = match &element.for_addr {
            Some(addr) => is_trusted_source(addr, trusted),
            None => false,
        };
        if !trusted_hop {
            return build_forwarded_decision(element, input);
        }
    }

    /* Every element was a trusted proxy: chain exhausted. */
    discard_forwarded_set(input, BaseUrlReason::ChainExhausted)
}

/// Build a base URL from the selected forwarded element.
///
/// The client address comes from the same hop element as the host/proto
/// (never combined across elements).  When the element carries no host, the
/// direct request Host is used; when it carries no proto, `https` is used
/// for elements that declare a host (matching the existing spec 47 default).
fn build_forwarded_decision(element: &ForwardedElement, input: &BaseUrlInput) -> BaseUrlDecision {
    /* Revalidate the selected host as defense-in-depth for future callers. */
    let host = element
        .host
        .as_ref()
        .and_then(|h| validate_forwarded_host(h));

    if let Some(h) = host {
        let scheme = match &element.proto {
            Some(p) => validate_proto(p).unwrap_or_else(|| "https".to_string()),
            None => "https".to_string(),
        };
        return BaseUrlDecision {
            base_url: format!("{scheme}://{h}"),
            reason: BaseUrlReason::ForwardedHeaderTrusted,
            source: BaseUrlSource::Forwarded,
        };
    }

    /* Element has no host: fall back to direct request metadata. */
    host_fallback(
        input.host,
        input.direct_scheme,
        BaseUrlReason::FallbackToHost,
    )
}

/// Split a comma-separated forwarded list into trimmed members.
fn split_forwarded_list(value: &str) -> Vec<String> {
    split_quoted(value, b',')
        .into_iter()
        .map(|m| m.trim().to_string())
        .collect()
}

/// Decide from the X-Forwarded-* source family.
///
/// `X-Forwarded-For` is mandatory and contains only addresses.  The optional
/// proto/host/port lists are valid only when all three are absent or all
/// three are present with the same length as the address chain.  Partial or
/// mismatched metadata discards the complete forwarded set.
fn decide_from_xforwarded(input: &BaseUrlInput, trusted: &[Cidr]) -> BaseUrlDecision {
    let Some(xff) = input.x_forwarded_for else {
        /* No Forwarded and no X-Forwarded-For: no forwarded metadata at all;
         * use direct request metadata. */
        return host_fallback(
            input.host,
            input.direct_scheme,
            BaseUrlReason::FallbackToHost,
        );
    };

    let addrs = split_forwarded_list(xff);

    let protos = input.x_forwarded_proto.map(split_forwarded_list);
    let hosts = input.x_forwarded_host.map(split_forwarded_list);
    let ports = input.x_forwarded_port.map(split_forwarded_list);

    /* All-or-none metadata with matching length (Requirement 13.7); validate
     * every address and metadata value. */
    let metadata_present = protos.is_some() || hosts.is_some() || ports.is_some();
    let validated = match validate_xff_values(&addrs, &protos, &hosts, &ports, metadata_present) {
        Ok(v) => v,
        Err(reason) => return discard_forwarded_set(input, reason),
    };

    /* Walk right-to-left, stripping trusted proxy addresses; select the
     * first remaining address. */
    let mut selected_idx: Option<usize> = None;
    for (idx, addr) in validated.iter().enumerate().rev() {
        if !is_trusted_source(addr, trusted) {
            selected_idx = Some(idx);
            break;
        }
    }

    let Some(idx) = selected_idx else {
        /* Every address was a trusted proxy: chain exhausted. */
        return discard_forwarded_set(input, BaseUrlReason::ChainExhausted);
    };

    if metadata_present {
        return build_xff_decision(idx, &protos, &hosts, &ports)
            .unwrap_or_else(|| discard_forwarded_set(input, BaseUrlReason::ForwardedInvalidValue));
    }

    /* No metadata lists: use direct request metadata for scheme/host/port. */
    host_fallback(
        input.host,
        input.direct_scheme,
        BaseUrlReason::ForwardedHeaderTrusted,
    )
}

/// Validate every X-Forwarded-* value and the all-or-none list contract.
///
/// Returns the validated address chain, or the discard reason when any
/// address, metadata value, or list alignment is invalid.
fn validate_xff_values(
    addrs: &[String],
    protos: &Option<Vec<String>>,
    hosts: &Option<Vec<String>>,
    ports: &Option<Vec<String>>,
    metadata_present: bool,
) -> Result<Vec<String>, BaseUrlReason> {
    let metadata =
        validate_xff_metadata_layout(addrs.len(), protos, hosts, ports, metadata_present)?;
    let validated = addrs
        .iter()
        .map(|addr| validate_forwarded_addr(addr))
        .collect::<Option<Vec<_>>>()
        .ok_or(BaseUrlReason::ForwardedInvalidValue)?;

    if let Some((protos, hosts, ports)) = metadata
        && !validate_xff_metadata_values(protos, hosts, ports)
    {
        return Err(BaseUrlReason::ForwardedInvalidValue);
    }

    Ok(validated)
}

type ValidatedXffMetadata<'a> = (&'a [String], &'a [String], &'a [String]);

fn validate_xff_metadata_layout<'a>(
    addr_count: usize,
    protos: &'a Option<Vec<String>>,
    hosts: &'a Option<Vec<String>>,
    ports: &'a Option<Vec<String>>,
    metadata_present: bool,
) -> Result<Option<ValidatedXffMetadata<'a>>, BaseUrlReason> {
    if !metadata_present {
        return Ok(None);
    }
    let (Some(protos), Some(hosts), Some(ports)) =
        (protos.as_deref(), hosts.as_deref(), ports.as_deref())
    else {
        return Err(BaseUrlReason::XForwardedMismatch);
    };
    if protos.len() != addr_count || hosts.len() != addr_count || ports.len() != addr_count {
        return Err(BaseUrlReason::XForwardedMismatch);
    }
    Ok(Some((protos, hosts, ports)))
}

fn validate_xff_metadata_values(protos: &[String], hosts: &[String], ports: &[String]) -> bool {
    protos.iter().all(|proto| validate_proto(proto).is_some())
        && hosts
            .iter()
            .all(|host| validate_forwarded_host(host).is_some())
        && ports.iter().all(|port| validate_port(port).is_some())
}

/// Build the base URL from the selected hop index, taking host/proto/port
/// from that same index (never combined across indices).
fn build_xff_decision(
    idx: usize,
    protos: &Option<Vec<String>>,
    hosts: &Option<Vec<String>>,
    ports: &Option<Vec<String>>,
) -> Option<BaseUrlDecision> {
    let proto = protos.as_ref()?.get(idx)?;
    let host_value = hosts.as_ref()?.get(idx)?;
    let port = ports.as_ref()?.get(idx)?;
    let scheme = validate_proto(proto)?;
    let host = validate_forwarded_host(host_value)?;
    validate_port(port)?;

    /* Host already carries a port (host:port form): X-Forwarded-Port is
     * redundant and must not be appended. */
    let host_with_port = if host.starts_with('[') {
        if host.ends_with(']') {
            format!("{host}:{port}")
        } else {
            host
        }
    } else if host.contains(':') {
        host
    } else {
        format!("{host}:{port}")
    };

    Some(BaseUrlDecision {
        base_url: format!("{scheme}://{host_with_port}"),
        reason: BaseUrlReason::ForwardedHeaderTrusted,
        source: BaseUrlSource::XForwarded,
    })
}

/// Discard the complete forwarded set and use direct peer/direct request
/// metadata.
fn discard_forwarded_set(input: &BaseUrlInput, reason: BaseUrlReason) -> BaseUrlDecision {
    host_fallback(input.host, input.direct_scheme, reason)
}

/// Build a decision from the `Host` header, or the safe default when the
/// `Host` header is absent/invalid.
///
/// Uses `direct_scheme` (from `r->schema`) when provided, defaulting to
/// "http" for backward compatibility.  This preserves the actual connection
/// protocol for direct HTTPS requests so relative links are not erroneously
/// resolved as http://.
fn host_fallback(
    host: Option<&str>,
    direct_scheme: Option<&str>,
    reason: BaseUrlReason,
) -> BaseUrlDecision {
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
    fn bracketed_ipv6_trusted_hop_matches() {
        /* validate_forwarded_addr returns bracketed IPv6 literals as-is;
         * is_trusted_source must strip the brackets before parsing so
         * bracketed trusted hops are recognized in the trust chain. */
        let t = cidrs(&["2001:db8::/32", "::1/128"]);
        assert!(is_trusted_source("[2001:db8::1]", &t));
        assert!(is_trusted_source("[::1]", &t));
        assert!(!is_trusted_source("[2001:dead::1]", &t));
        assert!(!is_trusted_source("[fe80::1]", &t));
    }

    #[test]
    fn mismatched_brackets_not_trusted() {
        let t = cidrs(&["0.0.0.0/0", "::/0"]);
        assert!(!is_trusted_source("[2001:db8::1", &t));
        assert!(!is_trusted_source("2001:db8::1]", &t));
        assert!(!is_trusted_source("[]", &t));
    }

    #[test]
    fn bracketed_ipv4_not_trusted() {
        /* A bracketed IPv4 literal is not a valid forwarded address form;
         * it must not be trusted even against 0.0.0.0/0. */
        let t = cidrs(&["0.0.0.0/0"]);
        assert!(!is_trusted_source("[1.2.3.4]", &t));
    }

    #[test]
    fn bracketed_ipv4_mapped_ipv6_trusted() {
        /* [::ffff:10.0.0.1] is a valid bracketed IPv6 literal (IPv4-mapped);
         * it should match the IPv4 CIDR, consistent with the unbracketed
         * ipv4_mapped_ipv6_matches_ipv4_cidr test. */
        let t = cidrs(&["10.0.0.0/8"]);
        assert!(is_trusted_source("[::ffff:10.0.0.1]", &t));
        assert!(!is_trusted_source("[::ffff:11.0.0.1]", &t));
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

    /* ---- Forwarded element parsing ---- */

    #[test]
    fn forwarded_elements_basic() {
        let els = parse_forwarded_elements("for=1.2.3.4;host=example.com;proto=https").unwrap();
        assert_eq!(els.len(), 1);
        assert_eq!(els[0].for_addr.as_deref(), Some("1.2.3.4"));
        assert_eq!(els[0].host.as_deref(), Some("example.com"));
        assert_eq!(els[0].proto.as_deref(), Some("https"));
    }

    #[test]
    fn forwarded_elements_quoted_values() {
        let els = parse_forwarded_elements(
            "for=\"[2001:db8::7]\";host=\"example.com:8080\";proto=\"https\"",
        )
        .unwrap();
        assert_eq!(els[0].for_addr.as_deref(), Some("[2001:db8::7]"));
        assert_eq!(els[0].host.as_deref(), Some("example.com:8080"));
        assert_eq!(els[0].proto.as_deref(), Some("https"));
    }

    #[test]
    fn forwarded_elements_multi_hop_preserves_order() {
        let els =
            parse_forwarded_elements("for=198.51.100.7;proto=https;host=example.com, for=192.0.2.10;proto=https;host=edge.example.com")
                .unwrap();
        assert_eq!(els.len(), 2);
        assert_eq!(els[0].for_addr.as_deref(), Some("198.51.100.7"));
        assert_eq!(els[1].for_addr.as_deref(), Some("192.0.2.10"));
        assert_eq!(els[1].host.as_deref(), Some("edge.example.com"));
    }

    #[test]
    fn forwarded_elements_proto_lowercased() {
        let els = parse_forwarded_elements("proto=HTTPS").unwrap();
        assert_eq!(els[0].proto.as_deref(), Some("https"));
    }

    #[test]
    fn forwarded_elements_comma_inside_quotes_does_not_split() {
        let els = parse_forwarded_elements("for=\"[2001:db8::7], 10.0.0.1\";proto=https").unwrap();
        assert_eq!(els.len(), 1);
        assert_eq!(els[0].for_addr.as_deref(), Some("[2001:db8::7], 10.0.0.1"));
    }

    #[test]
    fn forwarded_elements_unknown_params_ignored() {
        let els = parse_forwarded_elements("for=1.2.3.4;by=192.0.2.1;host=example.com").unwrap();
        assert_eq!(els[0].for_addr.as_deref(), Some("1.2.3.4"));
        assert_eq!(els[0].host.as_deref(), Some("example.com"));
    }

    #[test]
    fn forwarded_elements_reject_duplicate_params() {
        assert!(parse_forwarded_elements("host=a.com;host=b.com").is_none());
        assert!(parse_forwarded_elements("for=1.2.3.4;for=5.6.7.8").is_none());
    }

    #[test]
    fn forwarded_elements_reject_malformed() {
        assert!(parse_forwarded_elements("").is_none());
        assert!(parse_forwarded_elements("   ").is_none());
        assert!(parse_forwarded_elements("host").is_none());
        assert!(parse_forwarded_elements("host=a.com;").is_none());
        assert!(parse_forwarded_elements("host=\"unterminated").is_none());
        assert!(parse_forwarded_elements("host=\"evil.example\"garbage").is_none());
        assert!(parse_forwarded_elements("for=1.2.3.4,").is_none());
        assert!(parse_forwarded_elements(",for=1.2.3.4").is_none());
    }

    /* ---- Forwarded address/host validation ---- */

    #[test]
    fn forwarded_addr_validation() {
        assert_eq!(
            validate_forwarded_addr("192.0.2.10").as_deref(),
            Some("192.0.2.10")
        );
        assert_eq!(
            validate_forwarded_addr("[2001:db8::7]").as_deref(),
            Some("[2001:db8::7]")
        );
        assert_eq!(
            validate_forwarded_addr("2001:db8::7").as_deref(),
            Some("2001:db8::7")
        );
        assert_eq!(validate_forwarded_addr("unknown"), None);
        assert_eq!(validate_forwarded_addr("_obfuscated"), None);
        assert_eq!(validate_forwarded_addr("user@host"), None);
        assert_eq!(validate_forwarded_addr("192.0.2.1\r\n"), None);
        assert_eq!(validate_forwarded_addr("2001:db8::7%eth0"), None);
        assert_eq!(validate_forwarded_addr("[2001:db8::7"), None);
        assert_eq!(validate_forwarded_addr("example.com"), None);
    }

    #[test]
    fn forwarded_host_validation_strict() {
        assert_eq!(
            validate_forwarded_host("EXAMPLE.com").as_deref(),
            Some("example.com")
        );
        assert_eq!(
            validate_forwarded_host("example.com:8080").as_deref(),
            Some("example.com:8080")
        );
        assert_eq!(
            validate_forwarded_host("xn--80ak6aa92e.com").as_deref(),
            Some("xn--80ak6aa92e.com")
        );
        assert_eq!(
            validate_forwarded_host("[2001:db8::1]:443").as_deref(),
            Some("[2001:db8::1]:443")
        );
        assert_eq!(
            validate_forwarded_host("example.com.").as_deref(),
            Some("example.com")
        );
        /* Invalid forms. */
        assert_eq!(validate_forwarded_host(""), None);
        assert_eq!(validate_forwarded_host("a..b"), None);
        assert_eq!(validate_forwarded_host("-a.com"), None);
        assert_eq!(validate_forwarded_host("a-.com"), None);
        assert_eq!(validate_forwarded_host("a b.com"), None);
        assert_eq!(validate_forwarded_host("user@evil.com"), None);
        assert_eq!(validate_forwarded_host("evil.com/path"), None);
        assert_eq!(validate_forwarded_host("evil.com\r\nx"), None);
        assert_eq!(validate_forwarded_host("example.com:0"), None);
        assert_eq!(validate_forwarded_host("example.com:70000"), None);
        assert_eq!(validate_forwarded_host("[notv6]"), None);
        assert_eq!(validate_forwarded_host("2001:db8::1"), None);
        assert_eq!(validate_forwarded_host("foo%eth0.com"), None);
    }

    /* ---- Host validation (direct Host header fallback) ---- */

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

    /* ---- decide_base_url (multi-hop algorithm) ---- */

    fn trusted_input<'a>(source_ip: &'a str) -> BaseUrlInput<'a> {
        BaseUrlInput {
            source_ip,
            is_unix_socket: false,
            trusted_configured: true,
            forwarded: None,
            x_forwarded_for: None,
            x_forwarded_proto: None,
            x_forwarded_host: None,
            x_forwarded_port: None,
            host: Some("origin.example.com"),
            direct_scheme: None,
        }
    }

    #[test]
    fn decide_not_configured_uses_host() {
        let mut input = trusted_input("10.0.0.1");
        input.trusted_configured = false;
        input.x_forwarded_host = Some("spoof.example.com");
        input.x_forwarded_for = Some("1.2.3.4");
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
        input.x_forwarded_for = Some("1.2.3.4");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderUntrusted);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "http://origin.example.com");
    }

    /* Positive 2: single-hop XFF with direct metadata. */
    #[test]
    fn decide_trusted_uses_x_forwarded() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
        assert_eq!(d.source, BaseUrlSource::XForwarded);
        assert_eq!(d.base_url, "https://api.example.com:443");
    }

    #[test]
    fn xff_ipv6_host_without_port_gets_forwarded_port() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9");
        input.x_forwarded_host = Some("[2001:db8::1]");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");

        let d = decide_base_url(&input, &t);

        assert_eq!(d.base_url, "https://[2001:db8::1]:443");
    }

    #[test]
    fn xff_ipv6_host_with_port_does_not_duplicate_port() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9");
        input.x_forwarded_host = Some("[2001:db8::1]:8443");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");

        let d = decide_base_url(&input, &t);

        assert_eq!(d.base_url, "https://[2001:db8::1]:8443");
    }

    #[test]
    fn xff_builder_revalidates_selected_port() {
        let protos = Some(vec!["https".to_string()]);
        let hosts = Some(vec!["example.com".to_string()]);
        let ports = Some(vec!["0".to_string()]);

        assert!(build_xff_decision(0, &protos, &hosts, &ports).is_none());
    }

    #[test]
    fn forwarded_builder_rejects_invalid_host_without_empty_url() {
        let input = trusted_input("10.1.2.3");
        let element = ForwardedElement {
            host: Some("invalid host".to_string()),
            proto: Some("https".to_string()),
            ..ForwardedElement::default()
        };

        let decision = build_forwarded_decision(&element, &input);

        assert_eq!(decision.base_url, "http://origin.example.com");
        assert!(!decision.base_url.contains("https://"));
    }

    /* Positive 2: metadata lists absent → direct request metadata. */
    #[test]
    fn decide_xff_without_metadata_uses_direct_metadata() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "http://origin.example.com");
    }

    /* Positive 1: aligned multi-hop XFF chain with trusted-hop stripping. */
    #[test]
    fn decide_aligned_xff_multi_hop_strips_trusted() {
        let t = cidrs(&["10.0.0.0/8", "192.0.2.0/24"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("198.51.100.7, 192.0.2.10");
        input.x_forwarded_proto = Some("https, https");
        input.x_forwarded_host = Some("example.com, edge.example.com");
        input.x_forwarded_port = Some("443, 443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
        assert_eq!(d.source, BaseUrlSource::XForwarded);
        /* 192.0.2.10 is trusted and stripped; select client at index 0 with
         * its same-index metadata. */
        assert_eq!(d.base_url, "https://example.com:443");
    }

    /* Positive 6: right-to-left three-hop strip. */
    #[test]
    fn decide_three_hop_strip_selects_client_index() {
        let t = cidrs(&["10.0.0.0/8", "192.0.2.0/24", "198.51.100.0/24"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.5, 198.51.100.1, 192.0.2.1");
        input.x_forwarded_proto = Some("https, https, https");
        input.x_forwarded_host =
            Some("client.example.com, proxy-a.example.com, proxy-b.example.com");
        input.x_forwarded_port = Some("443, 443, 443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.base_url, "https://client.example.com:443");
    }

    /* Negative 3: partial/mismatched metadata discards the set. */
    #[test]
    fn decide_partial_metadata_discards_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9, 192.0.2.1");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::XForwardedMismatch);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "http://origin.example.com");
    }

    /* Negative 3: mismatched list lengths discard the set. */
    #[test]
    fn decide_mismatched_lengths_discard_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9");
        input.x_forwarded_host = Some("api.example.com, extra.example.com");
        input.x_forwarded_proto = Some("https, https");
        input.x_forwarded_port = Some("443, 443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::XForwardedMismatch);
        assert_eq!(d.source, BaseUrlSource::Host);
    }

    /* Negative 13: trusted chain exhausted → direct peer/direct metadata. */
    #[test]
    fn decide_chain_exhausted_discards_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("10.9.9.9, 10.8.8.8");
        input.x_forwarded_host = Some("a.example.com, b.example.com");
        input.x_forwarded_proto = Some("https, https");
        input.x_forwarded_port = Some("443, 443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ChainExhausted);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "http://origin.example.com");
    }

    /* Negative 4/5: unknown / obfuscated addresses discard the set. */
    #[test]
    fn decide_unknown_address_discards_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("unknown");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
        assert_eq!(d.source, BaseUrlSource::Host);
    }

    #[test]
    fn decide_empty_xff_discards_as_invalid_value() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
        assert_eq!(d.source, BaseUrlSource::Host);
    }

    #[test]
    fn decide_obfuscated_address_discards_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("_hidden, 10.0.0.2");
        input.x_forwarded_host = Some("a.example.com, b.example.com");
        input.x_forwarded_proto = Some("https, https");
        input.x_forwarded_port = Some("443, 443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    }

    /* Negative 10/11: invalid scheme / port discard the set. */
    #[test]
    fn decide_invalid_scheme_discards_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("ftp");
        input.x_forwarded_port = Some("443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    }

    #[test]
    fn decide_invalid_port_discards_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("0");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    }

    /* Negative 6: userinfo in host discards the set. */
    #[test]
    fn decide_userinfo_host_discards_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9");
        input.x_forwarded_host = Some("user:pass@api.example.com");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    }

    /* Negative 8: malformed IPv6 discards the set. */
    #[test]
    fn decide_malformed_ipv6_discards_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("[2001:db8::7");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    }

    /* Negative 7: control characters discard the set. */
    #[test]
    fn decide_control_chars_discard_set() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some("203.0.113.9");
        input.x_forwarded_host = Some("api.example.com\r\nInjected: x");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    }

    /* Positive 3: Forwarded precedence over X-Forwarded-*. */
    #[test]
    fn decide_forwarded_precedence_over_x_forwarded() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some("for=198.51.100.7;proto=https;host=fwd.example.com");
        input.x_forwarded_host = Some("xfwd.example.com");
        input.x_forwarded_proto = Some("http");
        input.x_forwarded_for = Some("1.2.3.4");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.source, BaseUrlSource::Forwarded);
        assert_eq!(d.base_url, "https://fwd.example.com");
    }

    /* Positive 4: Forwarded multi-hop stripping. */
    #[test]
    fn decide_forwarded_multi_hop_strips_trusted() {
        let t = cidrs(&["10.0.0.0/8", "192.0.2.0/24"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some(
            "for=198.51.100.7;proto=https;host=example.com, for=192.0.2.10;proto=https;host=edge.example.com",
        );
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
        assert_eq!(d.source, BaseUrlSource::Forwarded);
        assert_eq!(d.base_url, "https://example.com");
    }

    /* Positive 5: bracketed IPv6. */
    #[test]
    fn decide_forwarded_bracketed_ipv6() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some("for=\"[2001:db8::7]\";proto=https;host=[2001:db8::1]:443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
        assert_eq!(d.base_url, "https://[2001:db8::1]:443");
    }

    /* Negative 2: malformed Forwarded never falls back to X-Forwarded-*. */
    #[test]
    fn decide_malformed_forwarded_never_falls_back_to_xff() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some("host=a.com;host=b.com");
        input.x_forwarded_host = Some("xfwd.example.com");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_for = Some("1.2.3.4");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedMalformed);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "http://origin.example.com");
    }

    /* Negative 4: unknown forwarded address discards the set. */
    #[test]
    fn decide_forwarded_unknown_address_discards() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some("for=unknown;proto=https;host=evil.example.com");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
        assert_eq!(d.source, BaseUrlSource::Host);
    }

    /* Forwarded chain exhaustion. */
    #[test]
    fn decide_forwarded_chain_exhausted_discards() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some(
            "for=10.0.0.5;proto=https;host=a.example.com, for=10.0.0.6;proto=https;host=b.example.com",
        );
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ChainExhausted);
        assert_eq!(d.source, BaseUrlSource::Host);
    }

    /* Forwarded element without for= is never stripped. */
    #[test]
    fn decide_forwarded_element_without_for_is_client() {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.forwarded = Some(
            "proto=https;host=client.example.com, for=10.0.0.5;proto=https;host=edge.example.com",
        );
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
        assert_eq!(d.base_url, "https://client.example.com");
    }

    /* XFF with no Forwarded and no X-Forwarded-For → direct metadata. */
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
        input.x_forwarded_for = Some("203.0.113.9");
        input.x_forwarded_host = Some("api.example.com");
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");
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
