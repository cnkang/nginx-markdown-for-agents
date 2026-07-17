//! Streaming-policy representation shared by Rust configuration code.
//!
//! Runtime streaming eligibility is decided by the NGINX module. Keeping only
//! the policy enum here avoids presenting the former, unused Rust decision
//! model as a second source of truth.

/// `markdown_streaming` policy — the streaming *enablement* selector.
///
/// Discriminants are frozen for the 1.0 stability contract.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StreamingPolicy {
    /// `off` — never stream.
    Off = 0,
    /// `auto` — stream large responses, full-buffer small ones.
    Auto = 1,
    /// `force` — always stream (subject to the hard blocks above).
    Force = 2,
}

impl StreamingPolicy {
    /// Construct from the FFI `u8`; unknown values fall back to the safe
    /// `Auto` default (the `balanced` profile default).
    pub fn from_u8(value: u8) -> Self {
        match value {
            0 => StreamingPolicy::Off,
            2 => StreamingPolicy::Force,
            _ => StreamingPolicy::Auto,
        }
    }

    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn enum_from_u8_roundtrips() {
        for p in [
            StreamingPolicy::Off,
            StreamingPolicy::Auto,
            StreamingPolicy::Force,
        ] {
            assert_eq!(StreamingPolicy::from_u8(p.as_u8()), p);
        }
        assert_eq!(StreamingPolicy::from_u8(99), StreamingPolicy::Auto);
    }
}
