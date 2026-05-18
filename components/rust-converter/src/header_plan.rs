//! Response header mutation plan for Markdown conversion.
//!
//! Defines a declarative plan of header operations (set, delete, modify)
//! that must be applied atomically after conversion succeeds. This
//! prevents partial header state when conversion fails mid-way.

/// A single header mutation operation.
#[derive(Debug, Clone, PartialEq)]
pub enum HeaderOp {
    /// Set a header to a specific value (adds or replaces).
    Set {
        name: String,
        value: String,
    },
    /// Delete a header by name.
    Delete {
        name: String,
    },
    /// Set ETag to a value provided by the C caller at apply time.
    ///
    /// This avoids the fragile empty-string placeholder contract:
    /// the C caller recognizes `op_type == 2` and substitutes the
    /// actual ETag from `MarkdownResult.etag` instead of relying
    /// on an empty string sentinel.
    SetEtagPlaceholder,
}

/// A plan of header mutations to apply atomically.
#[derive(Debug, Clone, PartialEq)]
pub struct HeaderPlan {
    /// Ordered list of header operations.
    pub ops: Vec<HeaderOp>,
}

impl HeaderPlan {
    /// Create an empty header plan.
    pub fn new() -> Self {
        Self { ops: Vec::new() }
    }

    /// Add a "set header" operation.
    pub fn set(&mut self, name: &str, value: &str) {
        self.ops.push(HeaderOp::Set {
            name: name.to_string(),
            value: value.to_string(),
        });
    }

    /// Add a "delete header" operation.
    pub fn delete(&mut self, name: &str) {
        self.ops.push(HeaderOp::Delete {
            name: name.to_string(),
        });
    }

    /// Build the standard header plan for a successful Markdown conversion.
    ///
    /// Sets Content-Type to text/markdown, deletes Content-Encoding
    /// (since the response is no longer compressed), and sets Vary.
    pub fn for_markdown_conversion(content_type: &str, has_etag: bool) -> Self {
        let mut plan = Self::new();
        plan.set("Content-Type", content_type);
        plan.delete("Content-Encoding");
        plan.set("Vary", "Accept");

        if has_etag {
            plan.ops.push(HeaderOp::SetEtagPlaceholder);
        }

        plan
    }

    /// Returns the number of operations in the plan.
    pub fn len(&self) -> usize {
        self.ops.len()
    }

    /// Returns true if the plan has no operations.
    pub fn is_empty(&self) -> bool {
        self.ops.is_empty()
    }
}

impl Default for HeaderPlan {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_plan() {
        let plan = HeaderPlan::new();
        assert!(plan.is_empty());
        assert_eq!(plan.len(), 0);
    }

    #[test]
    fn test_set_and_delete() {
        let mut plan = HeaderPlan::new();
        plan.set("Content-Type", "text/markdown");
        plan.delete("Content-Encoding");
        assert_eq!(plan.len(), 2);
        assert_eq!(plan.ops[0], HeaderOp::Set {
            name: "Content-Type".to_string(),
            value: "text/markdown".to_string(),
        });
        assert_eq!(plan.ops[1], HeaderOp::Delete {
            name: "Content-Encoding".to_string(),
        });
    }

    #[test]
    fn test_for_markdown_conversion() {
        let plan = HeaderPlan::for_markdown_conversion("text/markdown; charset=utf-8", true);
        assert_eq!(plan.len(), 4);
        assert_eq!(plan.ops[3], HeaderOp::SetEtagPlaceholder);
    }

    #[test]
    fn test_for_markdown_conversion_no_etag() {
        let plan = HeaderPlan::for_markdown_conversion("text/markdown", false);
        assert_eq!(plan.len(), 3); // Content-Type, delete Content-Encoding, Vary
    }

    #[test]
    fn test_default() {
        let plan = HeaderPlan::default();
        assert!(plan.is_empty());
    }
}
