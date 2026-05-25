#![no_main]

//! Fuzz target for bounded gzip decompression.
//!
//! Takes arbitrary bytes as input and attempts to decompress them as gzip
//! with a bounded budget. Verifies no panics or memory safety issues occur
//! regardless of input content.

use libfuzzer_sys::fuzz_target;
use std::io::Read;

/// Maximum decompression budget in bytes (1 MiB).
const DECOMPRESSION_BUDGET: usize = 1024 * 1024;

fuzz_target!(|data: &[u8]| {
    /* Attempt bounded gzip decompression of arbitrary input. */
    let mut decoder = flate2::read::GzDecoder::new(data);
    let mut output = Vec::new();
    let mut buf = [0u8; 4096];
    let mut total_read: usize = 0;

    loop {
        match decoder.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                total_read = total_read.saturating_add(n);
                if total_read > DECOMPRESSION_BUDGET {
                    /* Budget exceeded — stop decompressing. */
                    break;
                }
                output.extend_from_slice(&buf[..n]);
            }
            Err(_) => {
                /* Format error, truncated input, or I/O error — all acceptable. */
                break;
            }
        }
    }

    /* No assertions needed: the goal is to verify no panics or UB occur. */
    let _ = output;
});
