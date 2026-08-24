#![no_main]

//! Fuzz target for trusted-proxy CIDR parsing (spec 47, 0.9.0).
//!
//! Feeds arbitrary bytes as a trusted-proxy CIDR configuration string into
//! [`nginx_markdown_converter::forwarded::parse_cidr`], covering strict UTF-8
//! and lossy interpretations of the input.
//!
//! Invariants checked:
//! - No panics on any input; malformed strings are rejected, never crash.
//! - Parsing is idempotent: re-parsing the same string yields the identical
//!   network.
//! - Self-containment: a parsed CIDR always contains its own base address
//!   (canonical masking keeps the network base inside the network).

use core::net::IpAddr;
use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::forwarded::parse_cidr;

/// Check the invariants for one string interpretation.
fn check_cidr(s: &str) {
    let Ok(cidr) = parse_cidr(s) else {
        return; /* Rejected input is a valid outcome. */
    };

    /* Idempotency: re-parsing yields the identical network. */
    let again = parse_cidr(s).expect("parse succeeded once before");
    assert_eq!(cidr, again, "CIDR parse not idempotent for {:?}", s);

    /* Self-containment: the network contains its own base address. */
    let addr_part = s.trim().split('/').next().unwrap_or("");
    if let Ok(ip) = addr_part.parse::<IpAddr>() {
        assert!(
            cidr.contains(ip),
            "CIDR {:?} does not contain its own address {}",
            s,
            addr_part
        );
    }
}

fuzz_target!(|data: &[u8]| {
    /* Strict UTF-8 interpretation. */
    if let Ok(s) = std::str::from_utf8(data) {
        check_cidr(s);
    }
    /* Lossy interpretation also covers non-UTF-8 byte sequences. */
    let lossy = String::from_utf8_lossy(data);
    check_cidr(&lossy);
});
