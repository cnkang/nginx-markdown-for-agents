//! SHA-256 digest computation for dynconf.
//!
//! Provides two digest functions:
//! - `compute_source_digest`: SHA-256 over raw file bytes
//! - `compute_active_digest`: SHA-256 over canonical normalized JSON
//!
//! The canonical form uses fixed key order and normalized typed values.
//! Absent optional keys are NOT included in the canonical representation.

use super::schema::DynconfValue;

/// Compute SHA-256 digest over raw input bytes.
///
/// This produces the `source_digest` value that detects any byte-level
/// change to the dynconf file, including formatting-only edits.
///
/// # Arguments
///
/// * `raw_bytes` - The raw file content as read from disk
///
/// # Returns
///
/// Lowercase hex-encoded SHA-256 digest string (64 characters).
pub fn compute_source_digest(raw_bytes: &[u8]) -> String {
    use std::fmt::Write;

    let hash = sha256(raw_bytes);
    let mut hex = String::with_capacity(64);
    for byte in &hash {
        write!(hex, "{:02x}", byte).unwrap();
    }
    hex
}

/// Compute SHA-256 digest over the canonical normalized JSON representation.
///
/// The canonical form is UTF-8 JSON with:
/// - Fixed key order: schema_version, filter, prune_noise, log_verbosity,
///   error_policy, streaming_buffer
/// - Only explicitly present keys are included (absent keys omitted)
/// - `schema_version` is always included (value 1)
/// - String values use their canonical lowercase form
/// - Integer values use their decimal representation without leading zeros
/// - Compact JSON (no extra whitespace)
///
/// # Arguments
///
/// * `value` - The validated dynconf value set
///
/// # Returns
///
/// Lowercase hex-encoded SHA-256 digest string (64 characters).
pub fn compute_active_digest(value: &DynconfValue) -> String {
    let canonical = build_canonical_json(value);
    compute_source_digest(canonical.as_bytes())
}

/// Build the canonical JSON representation for active_digest computation.
///
/// Key order (only present keys included):
/// 1. schema_version (always present, value 1)
/// 2. filter
/// 3. prune_noise
/// 4. log_verbosity
/// 5. error_policy
/// 6. streaming_buffer
fn build_canonical_json(value: &DynconfValue) -> String {
    let mut parts: Vec<String> = Vec::new();

    // schema_version is always present
    parts.push("\"schema_version\":1".to_string());

    if let Some(filter) = &value.filter {
        parts.push(format!("\"filter\":\"{}\"", filter.as_str()));
    }

    if let Some(prune_noise) = &value.prune_noise {
        parts.push(format!("\"prune_noise\":\"{}\"", prune_noise.as_str()));
    }

    if let Some(log_verbosity) = &value.log_verbosity {
        parts.push(format!("\"log_verbosity\":\"{}\"", log_verbosity.as_str()));
    }

    if let Some(error_policy) = &value.error_policy {
        parts.push(format!("\"error_policy\":\"{}\"", error_policy.as_str()));
    }

    if let Some(streaming_buffer) = &value.streaming_buffer {
        parts.push(format!("\"streaming_buffer\":{}", streaming_buffer));
    }

    format!("{{{}}}", parts.join(","))
}

/// Minimal SHA-256 implementation.
///
/// Uses a pure-Rust implementation to avoid adding the `sha2` crate as a
/// mandatory dependency. This is correct for the dynconf use case where
/// performance is not critical (files ≤ 1 MiB, parsed at most once per
/// timer tick).
fn sha256(data: &[u8]) -> [u8; 32] {
    // SHA-256 constants: first 32 bits of the fractional parts of the
    // cube roots of the first 64 primes
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];

    // Initial hash values
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];

    // Pre-processing: padding
    let bit_len = (data.len() as u64) * 8;
    let mut padded = data.to_vec();
    padded.push(0x80);
    while (padded.len() % 64) != 56 {
        padded.push(0x00);
    }
    padded.extend_from_slice(&bit_len.to_be_bytes());

    // Process each 512-bit (64-byte) block
    for chunk in padded.chunks_exact(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                chunk[i * 4],
                chunk[i * 4 + 1],
                chunk[i * 4 + 2],
                chunk[i * 4 + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }

        let mut a = h[0];
        let mut b = h[1];
        let mut c = h[2];
        let mut d = h[3];
        let mut e = h[4];
        let mut f = h[5];
        let mut g = h[6];
        let mut hh = h[7];

        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let temp1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);

            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }

        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }

    let mut result = [0u8; 32];
    for (i, val) in h.iter().enumerate() {
        result[i * 4..i * 4 + 4].copy_from_slice(&val.to_be_bytes());
    }
    result
}

#[cfg(test)]
mod digest_tests {
    use super::*;
    use crate::dynconf::schema::{ErrorPolicy, FilterValue, LogVerbosity, PruneNoiseValue};

    #[test]
    fn test_sha256_empty() {
        let hash = compute_source_digest(b"");
        assert_eq!(
            hash,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
    }

    #[test]
    fn test_sha256_hello() {
        let hash = compute_source_digest(b"hello");
        assert_eq!(
            hash,
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
    }

    #[test]
    fn test_sha256_nist_abc_vector() {
        assert_eq!(
            compute_source_digest(b"abc"),
            concat!(
                "ba7816bf8f01cfea414140de5dae2223",
                "b00361a396177a9cb410ff61f20015ad"
            )
        );
    }

    #[test]
    fn test_sha256_nist_448_bit_vector() {
        assert_eq!(
            compute_source_digest(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
    }

    #[test]
    fn test_sha256_padding_block_boundaries() {
        assert_eq!(
            compute_source_digest(&[b'a'; 55]),
            "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"
        );
        assert_eq!(
            compute_source_digest(&[b'a'; 56]),
            "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"
        );
        assert_eq!(
            compute_source_digest(&[b'a'; 64]),
            "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"
        );
    }

    #[test]
    fn test_canonical_json_minimal() {
        let value = DynconfValue {
            filter: None,
            prune_noise: None,
            log_verbosity: None,
            error_policy: None,
            streaming_buffer: None,
        };
        let canonical = build_canonical_json(&value);
        assert_eq!(canonical, r#"{"schema_version":1}"#);
    }

    #[test]
    fn test_canonical_json_full() {
        let value = DynconfValue {
            filter: Some(FilterValue::On),
            prune_noise: Some(PruneNoiseValue::Off),
            log_verbosity: Some(LogVerbosity::Info),
            error_policy: Some(ErrorPolicy::Pass),
            streaming_buffer: Some(2_097_152),
        };
        let canonical = build_canonical_json(&value);
        assert_eq!(
            canonical,
            r#"{"schema_version":1,"filter":"on","prune_noise":"off","log_verbosity":"info","error_policy":"pass","streaming_buffer":2097152}"#
        );
    }

    #[test]
    fn test_canonical_json_partial() {
        let value = DynconfValue {
            filter: Some(FilterValue::Off),
            prune_noise: None,
            log_verbosity: Some(LogVerbosity::Debug),
            error_policy: None,
            streaming_buffer: None,
        };
        let canonical = build_canonical_json(&value);
        assert_eq!(
            canonical,
            r#"{"schema_version":1,"filter":"off","log_verbosity":"debug"}"#
        );
    }

    #[test]
    fn test_active_digest_deterministic() {
        let value = DynconfValue {
            filter: Some(FilterValue::On),
            prune_noise: None,
            log_verbosity: None,
            error_policy: None,
            streaming_buffer: Some(65536),
        };
        let d1 = compute_active_digest(&value);
        let d2 = compute_active_digest(&value);
        assert_eq!(d1, d2);
        assert_eq!(d1.len(), 64);
    }
}
