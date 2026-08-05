//! Property-based tests for trusted-proxy header extraction (Property 24).
//!
//! **Validates: Requirements 13.1, 13.7**
//!
//! Exercises the authoritative trusted-proxy decision algorithm:
//! - right-to-left trusted-hop stripping on the X-Forwarded-For address chain
//! - same-positional-index metadata selection across aligned lists
//! - all-or-none optional metadata (proto/host/port) with matching length
//! - Forwarded precedence over X-Forwarded-* and no fallback after a
//!   malformed Forwarded source
//! - partial/mismatched metadata and trusted-chain exhaustion fall back to
//!   the direct peer/direct request metadata
//! - untrusted direct peers ignore forwarded headers entirely
//! - invalid literals/obfuscated values (userinfo, control chars, malformed
//!   IPv6) are rejected; no DNS resolution is invoked
//!
//! All six positive and thirteen negative normative fixtures from
//! Requirement 13 are executed under their stable case names.

use nginx_markdown_converter::forwarded::{
    BaseUrlInput, BaseUrlReason, BaseUrlSource, Cidr, decide_base_url, parse_cidr,
};
use proptest::prelude::*;

fn cidrs(list: &[&str]) -> Vec<Cidr> {
    list.iter().map(|s| parse_cidr(s).unwrap()).collect()
}

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

fn hostname_strategy() -> impl Strategy<Value = String> {
    prop_oneof![
        Just("example.com".to_string()),
        Just("edge.example.com".to_string()),
        Just("api.example.org".to_string()),
        "[a-z]{1,12}\\.[a-z]{2,6}".prop_map(|s| s),
    ]
}

fn addr_strategy() -> impl Strategy<Value = String> {
    prop_oneof![
        Just("198.51.100.7".to_string()),
        Just("192.0.2.10".to_string()),
        Just("203.0.113.9".to_string()),
        Just("10.0.0.1".to_string()),
        Just("2001:db8::7".to_string()),
        (0u32..256, 0u32..256, 0u32..256, 0u32..256)
            .prop_map(|(a, b, c, d)| format!("{a}.{b}.{c}.{d}")),
    ]
}

/// Property: X-Forwarded-For chain stripping selects the first untrusted
/// address from the right, and metadata comes from that same index.
proptest! {
    #[test]
    fn p24_xff_chain_strip_selects_same_index_metadata(
        client in prop_oneof![
            Just("198.51.100.7".to_string()),
            Just("203.0.113.9".to_string()),
            Just("2001:db8::7".to_string()),
        ],
        host_a in hostname_strategy(),
        host_b in hostname_strategy(),
    ) {
        /* Trusted peer; chain = client, fixed trusted hop 192.0.2.10. */
        let t = cidrs(&["10.0.0.0/8", "192.0.2.0/24"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some(Box::leak(format!("{client}, 192.0.2.10").into_boxed_str()));
        input.x_forwarded_host = Some(Box::leak(format!("{host_a}, {host_b}").into_boxed_str()));
        input.x_forwarded_proto = Some("https, https");
        input.x_forwarded_port = Some("443, 443");

        /* The trusted hop is stripped; the client is selected with index-0
         * metadata, never combined with the stripped hop's metadata. */
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
        assert_eq!(d.base_url, format!("https://{host_a}:443"));
    }
}

proptest! {
    /// Property: mismatched list lengths always discard the forwarded set.
    #[test]
    fn p24_mismatched_lengths_always_discard(
        client in addr_strategy(),
        trusted_hop in addr_strategy(),
        host in hostname_strategy(),
    ) {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_for = Some(Box::leak(format!("{client}, {trusted_hop}").into_boxed_str()));
        input.x_forwarded_host = Some(Box::leak(host.into_boxed_str()));
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::XForwardedMismatch);
        assert_eq!(d.source, BaseUrlSource::Host);
        assert_eq!(d.base_url, "http://origin.example.com");
    }
}

proptest! {
    /// Property: without X-Forwarded-For, no X-Forwarded metadata is used.
    #[test]
    fn p24_no_xff_ignores_xforwarded_metadata(host in hostname_strategy()) {
        let t = cidrs(&["10.0.0.0/8"]);
        let mut input = trusted_input("10.1.2.3");
        input.x_forwarded_host = Some(Box::leak(host.into_boxed_str()));
        input.x_forwarded_proto = Some("https");
        input.x_forwarded_port = Some("443");
        let d = decide_base_url(&input, &t);
        assert_eq!(d.reason, BaseUrlReason::FallbackToHost);
        assert_eq!(d.source, BaseUrlSource::Host);
    }
}

// ─── Normative fixtures (Requirement 13) ─────────────────────────────────────
//
// The six positive and thirteen negative examples are part of the contract;
// each carries its stable case name.

/* Positive 1: aligned X-Forwarded chain. */
#[test]
fn positive_1_aligned_xff_chain() {
    /* Peer T is a trusted proxy; the chain carries client 198.51.100.7 and
     * the trusted proxy hop 192.0.2.10. */
    let t = cidrs(&["10.0.0.0/8", "192.0.2.0/24"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("198.51.100.7, 192.0.2.10");
    input.x_forwarded_proto = Some("https, https");
    input.x_forwarded_host = Some("example.com, edge.example.com");
    input.x_forwarded_port = Some("443, 443");
    let d = decide_base_url(&input, &t);
    /* Strip trusted 192.0.2.10; select client 198.51.100.7 with the
     * same-index metadata. */
    assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
    assert_eq!(d.base_url, "https://example.com:443");
}

/* Positive 2: single-hop X-Forwarded chain with direct metadata. */
#[test]
fn positive_2_single_hop_xff_with_direct_metadata() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("198.51.100.7");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
    assert_eq!(d.base_url, "http://origin.example.com");
}

/* Positive 3: Forwarded precedence. */
#[test]
fn positive_3_forwarded_precedence() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.forwarded = Some("for=198.51.100.7;proto=https;host=example.com");
    input.x_forwarded_for = Some("203.0.113.9");
    input.x_forwarded_host = Some("xfwd.example.com");
    input.x_forwarded_proto = Some("http");
    input.x_forwarded_port = Some("80");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.source, BaseUrlSource::Forwarded);
    assert_eq!(d.base_url, "https://example.com");
}

/* Positive 4: Forwarded multi-hop stripping. */
#[test]
fn positive_4_forwarded_multi_hop_stripping() {
    let t = cidrs(&["10.0.0.0/8", "192.0.2.0/24"]);
    let mut input = trusted_input("10.0.0.1");
    input.forwarded = Some(
        "for=198.51.100.7;proto=https;host=example.com, for=192.0.2.10;proto=https;host=edge.example.com",
    );
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
    assert_eq!(d.base_url, "https://example.com");
}

/* Positive 5: bracketed IPv6. */
#[test]
fn positive_5_bracketed_ipv6() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.forwarded = Some("for=\"[2001:db8::7]\";proto=https;host=[2001:db8::1]:443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
    assert_eq!(d.base_url, "https://[2001:db8::1]:443");
}

/* Positive 6: right-to-left three-hop strip. */
#[test]
fn positive_6_right_to_left_three_hop_strip() {
    let t = cidrs(&["10.0.0.0/8", "192.0.2.0/24", "198.51.100.0/24"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("203.0.113.5, 198.51.100.1, 192.0.2.1");
    input.x_forwarded_proto = Some("https, https, https");
    input.x_forwarded_host =
        Some("client.example.com, proxy-a.example.com, proxy-b.example.com");
    input.x_forwarded_port = Some("443, 443, 443");
    let d = decide_base_url(&input, &t);
    /* Strip B then A; select the client entry at index 0. */
    assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderTrusted);
    assert_eq!(d.base_url, "https://client.example.com:443");
}

/* Negative 1: untrusted peer. */
#[test]
fn negative_1_untrusted_peer() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("203.0.113.7");
    input.x_forwarded_for = Some("198.51.100.7");
    input.x_forwarded_host = Some("evil.example.com");
    input.x_forwarded_proto = Some("https");
    input.x_forwarded_port = Some("443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedHeaderUntrusted);
    assert_eq!(d.source, BaseUrlSource::Host);
    assert_eq!(d.base_url, "http://origin.example.com");
}

/* Negative 2: malformed Forwarded with X-Forwarded fallback. */
#[test]
fn negative_2_malformed_forwarded_no_xff_fallback() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.forwarded = Some("host=a.example.com;host=b.example.com");
    input.x_forwarded_for = Some("198.51.100.7");
    input.x_forwarded_host = Some("xfwd.example.com");
    input.x_forwarded_proto = Some("https");
    input.x_forwarded_port = Some("443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedMalformed);
    assert_eq!(d.source, BaseUrlSource::Host);
    assert_eq!(d.base_url, "http://origin.example.com");
}

/* Negative 3: partial or inconsistent list lengths. */
#[test]
fn negative_3_partial_or_inconsistent_list_lengths() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("198.51.100.7, 192.0.2.10");
    input.x_forwarded_host = Some("example.com");
    input.x_forwarded_proto = Some("https");
    input.x_forwarded_port = Some("443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::XForwardedMismatch);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 4: unknown address. */
#[test]
fn negative_4_unknown_address() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("unknown");
    input.x_forwarded_host = Some("example.com");
    input.x_forwarded_proto = Some("https");
    input.x_forwarded_port = Some("443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 5: obfuscated identifier. */
#[test]
fn negative_5_obfuscated_identifier() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("_hidden, 198.51.100.7");
    input.x_forwarded_host = Some("a.example.com, b.example.com");
    input.x_forwarded_proto = Some("https, https");
    input.x_forwarded_port = Some("443, 443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 6: userinfo. */
#[test]
fn negative_6_userinfo() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.forwarded = Some("for=198.51.100.7;proto=https;host=user:pass@example.com");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 7: control character. */
#[test]
fn negative_7_control_character() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("198.51.100.7");
    input.x_forwarded_host = Some("evil.example.com\r\nInjected: x");
    input.x_forwarded_proto = Some("https");
    input.x_forwarded_port = Some("443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 8: malformed IPv6. */
#[test]
fn negative_8_malformed_ipv6() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("[2001:db8::7");
    input.x_forwarded_host = Some("example.com");
    input.x_forwarded_proto = Some("https");
    input.x_forwarded_port = Some("443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 9: zone identifier. */
#[test]
fn negative_9_zone_identifier() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("2001:db8::7%25eth0");
    input.x_forwarded_host = Some("example.com");
    input.x_forwarded_proto = Some("https");
    input.x_forwarded_port = Some("443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 10: invalid scheme. */
#[test]
fn negative_10_invalid_scheme() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("198.51.100.7");
    input.x_forwarded_host = Some("example.com");
    input.x_forwarded_proto = Some("ftp");
    input.x_forwarded_port = Some("443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 11: invalid port. */
#[test]
fn negative_11_invalid_port() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("198.51.100.7");
    input.x_forwarded_host = Some("example.com");
    input.x_forwarded_proto = Some("https");
    input.x_forwarded_port = Some("0");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 12: invalid host. */
#[test]
fn negative_12_invalid_host() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.forwarded = Some("for=198.51.100.7;proto=https;host=a..b");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/* Negative 13: trusted chain exhausted. */
#[test]
fn negative_13_trusted_chain_exhausted() {
    let t = cidrs(&["10.0.0.0/8", "192.0.2.0/24"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("192.0.2.1, 192.0.2.2");
    input.x_forwarded_host = Some("a.example.com, b.example.com");
    input.x_forwarded_proto = Some("https, https");
    input.x_forwarded_port = Some("443, 443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ChainExhausted);
    assert_eq!(d.source, BaseUrlSource::Host);
    assert_eq!(d.base_url, "http://origin.example.com");
}

// ─── DNS-resolution prohibition ──────────────────────────────────────────────

/// No forwarded value may trigger a DNS lookup: every address is parsed
/// syntactically.  A hostname in `for=` is rejected outright (it is not an
/// address literal), never resolved.
#[test]
fn no_dns_resolution_for_hostnames_in_address_chain() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.x_forwarded_for = Some("client.example.com");
    input.x_forwarded_host = Some("example.com");
    input.x_forwarded_proto = Some("https");
    input.x_forwarded_port = Some("443");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
    assert_eq!(d.source, BaseUrlSource::Host);
}

/// Obfuscated host names in Forwarded host= are rejected syntactically.
#[test]
fn obfuscated_host_rejected() {
    let t = cidrs(&["10.0.0.0/8"]);
    let mut input = trusted_input("10.0.0.1");
    input.forwarded = Some("for=198.51.100.7;proto=https;host=_evil.example.com");
    let d = decide_base_url(&input, &t);
    assert_eq!(d.reason, BaseUrlReason::ForwardedInvalidValue);
}
