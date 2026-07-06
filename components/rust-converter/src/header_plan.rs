//! Response header mutation plan for Markdown conversion.
//!
//! Defines a declarative plan of header operations (set, delete, delete-all)
//! that must be applied atomically after conversion succeeds. This
//! prevents partial header state when conversion fails mid-way.

/// A single header mutation operation.
#[derive(Debug, Clone, PartialEq)]
pub enum HeaderOp {
    /// Set a header to a specific value (adds or replaces).
    Set { name: String, value: String },
    /// Delete a header by name.
    Delete { name: String },
    /// Delete every active header with this name.
    DeleteAll { name: String },
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

    /// Add a "delete all headers with this name" operation.
    pub fn delete_all(&mut self, name: &str) {
        self.ops.push(HeaderOp::DeleteAll {
            name: name.to_string(),
        });
    }

    /// Build the standard header plan for a successful Markdown conversion.
    ///
    /// Operations included:
    /// - Set Content-Type to the Markdown content type
    /// - Delete all Content-Encoding entries (response is no longer compressed)
    /// - Delete all Content-Length entries (original length is invalid after conversion;
    ///   the C caller sets the new Content-Length after atomic plan application)
    /// - Optionally set ETag placeholder (resolved by C caller)
    ///
    /// Note: Vary: Accept is handled as a post-plan operation by the C module
    /// using `ngx_http_markdown_add_vary_accept()`, not by this plan, because
    /// it requires NGINX-specific `ngx_list_push` which cannot be expressed
    /// in the platform-independent plan structure.
    ///
    /// The plan is applied atomically: all operations succeed or all are
    /// rolled back.  Content-Length is deleted here to invalidate the stale
    /// original value; the correct post-conversion length is set by the C
    /// caller immediately after successful plan application.
    pub fn for_markdown_conversion(content_type: &str, has_etag: bool) -> Self {
        let mut plan = Self::new();
        plan.set("Content-Type", content_type);
        plan.delete_all("Content-Encoding");
        plan.delete_all("Content-Length");

        if has_etag {
            plan.ops.push(HeaderOp::SetEtagPlaceholder);
        }

        plan
    }

    /// Build a header plan for a pre-commit error response.
    ///
    /// Used when the module generates an error response (e.g., 429/502/503)
    /// before the conversion commit phase. The plan:
    /// - Sets Content-Type to the given value (e.g., "text/plain")
    /// - Removes Content-Length (will be set by the actual error body)
    /// - Removes ETag (error response has no entity ETag)
    /// - Removes Content-Encoding (error body is not compressed)
    ///
    /// # Arguments
    ///
    /// * `content_type` - Content-Type for the error response body
    pub fn for_error_pre_commit(content_type: &str) -> Self {
        let mut plan = Self::new();
        plan.set("Content-Type", content_type);
        plan.delete_all("Content-Length");
        plan.delete_all("ETag");
        plan.delete_all("Content-Encoding");
        plan
    }

    /// Build a header plan for bypass/pass-through scenarios.
    ///
    /// When the module decides NOT to convert (e.g., the response is not
    /// eligible or the client does not accept Markdown), all original
    /// headers are preserved untouched. This produces an empty plan,
    /// documenting the intent that no header mutation is needed.
    pub fn for_bypass() -> Self {
        Self::new()
    }

    /// Alias for [`Self::for_bypass`] — documents pass-through intent.
    ///
    /// Semantically identical to `for_bypass`: produces an empty plan
    /// preserving all original headers.
    pub fn for_pass_through() -> Self {
        Self::for_bypass()
    }

    /// Build a header plan for a 304 Not Modified response.
    ///
    /// A 304 response has no body, so Content-Length and Content-Encoding
    /// are removed. ETag and Last-Modified are preserved (they confirm
    /// the entity the client already has).
    pub fn for_304() -> Self {
        let mut plan = Self::new();
        plan.delete_all("Content-Length");
        plan.delete_all("Content-Encoding");
        plan
    }

    /// Build a header plan for a HEAD response.
    ///
    /// HEAD responses carry the same headers that a GET would produce
    /// (including the converted Content-Type, ETag, etc.) but no body.
    /// This is equivalent to the conversion plan since the C module
    /// suppresses the body independently.
    ///
    /// # Arguments
    ///
    /// * `content_type` - The Content-Type that a GET conversion would set
    /// * `has_etag` - Whether an ETag placeholder should be included
    pub fn for_head(content_type: &str, has_etag: bool) -> Self {
        Self::for_markdown_conversion(content_type, has_etag)
    }

    /// Build a header plan for a generic no-body response.
    ///
    /// Removes Content-Length since there is no body. Other headers
    /// are preserved as-is.
    pub fn for_no_body() -> Self {
        let mut plan = Self::new();
        plan.delete_all("Content-Length");
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
        plan.delete_all("Content-Length");
        assert_eq!(plan.len(), 3);
        assert_eq!(
            plan.ops[0],
            HeaderOp::Set {
                name: "Content-Type".to_string(),
                value: "text/markdown".to_string(),
            }
        );
        assert_eq!(
            plan.ops[1],
            HeaderOp::Delete {
                name: "Content-Encoding".to_string(),
            }
        );
        assert_eq!(
            plan.ops[2],
            HeaderOp::DeleteAll {
                name: "Content-Length".to_string(),
            }
        );
    }

    #[test]
    fn test_for_markdown_conversion() {
        let plan = HeaderPlan::for_markdown_conversion("text/markdown; charset=utf-8", true);
        assert_eq!(plan.len(), 4);
        assert_eq!(
            plan.ops[1],
            HeaderOp::DeleteAll {
                name: "Content-Encoding".to_string(),
            }
        );
        assert_eq!(
            plan.ops[2],
            HeaderOp::DeleteAll {
                name: "Content-Length".to_string(),
            }
        );
        assert_eq!(plan.ops[3], HeaderOp::SetEtagPlaceholder);
    }

    #[test]
    fn test_for_markdown_conversion_no_etag() {
        let plan = HeaderPlan::for_markdown_conversion("text/markdown", false);
        assert_eq!(plan.len(), 3); // CT, del CE, del CL
    }

    #[test]
    fn test_default() {
        let plan = HeaderPlan::default();
        assert!(plan.is_empty());
    }

    #[test]
    fn test_for_error_pre_commit() {
        let plan = HeaderPlan::for_error_pre_commit("text/plain");
        assert_eq!(plan.len(), 4);
        assert_eq!(
            plan.ops[0],
            HeaderOp::Set {
                name: "Content-Type".to_string(),
                value: "text/plain".to_string(),
            }
        );
        assert_eq!(
            plan.ops[1],
            HeaderOp::DeleteAll {
                name: "Content-Length".to_string(),
            }
        );
        assert_eq!(
            plan.ops[2],
            HeaderOp::DeleteAll {
                name: "ETag".to_string(),
            }
        );
        assert_eq!(
            plan.ops[3],
            HeaderOp::DeleteAll {
                name: "Content-Encoding".to_string(),
            }
        );
    }

    #[test]
    fn test_for_error_pre_commit_429() {
        let plan = HeaderPlan::for_error_pre_commit("text/html; charset=utf-8");
        assert_eq!(plan.len(), 4);
        assert_eq!(
            plan.ops[0],
            HeaderOp::Set {
                name: "Content-Type".to_string(),
                value: "text/html; charset=utf-8".to_string(),
            }
        );
    }

    #[test]
    fn test_for_bypass() {
        let plan = HeaderPlan::for_bypass();
        assert!(plan.is_empty());
        assert_eq!(plan.len(), 0);
    }

    #[test]
    fn test_for_pass_through() {
        let plan = HeaderPlan::for_pass_through();
        assert!(plan.is_empty());
        assert_eq!(plan.len(), 0);
    }

    #[test]
    fn test_bypass_and_pass_through_are_equivalent() {
        let bypass = HeaderPlan::for_bypass();
        let pass_through = HeaderPlan::for_pass_through();
        assert_eq!(bypass, pass_through);
    }

    #[test]
    fn test_for_304() {
        let plan = HeaderPlan::for_304();
        assert_eq!(plan.len(), 2);
        assert_eq!(
            plan.ops[0],
            HeaderOp::DeleteAll {
                name: "Content-Length".to_string(),
            }
        );
        assert_eq!(
            plan.ops[1],
            HeaderOp::DeleteAll {
                name: "Content-Encoding".to_string(),
            }
        );
    }

    #[test]
    fn test_for_head_with_etag() {
        let plan = HeaderPlan::for_head("text/markdown; charset=utf-8", true);
        let conversion = HeaderPlan::for_markdown_conversion("text/markdown; charset=utf-8", true);
        assert_eq!(plan, conversion);
    }

    #[test]
    fn test_for_head_without_etag() {
        let plan = HeaderPlan::for_head("text/markdown", false);
        let conversion = HeaderPlan::for_markdown_conversion("text/markdown", false);
        assert_eq!(plan, conversion);
    }

    #[test]
    fn test_for_no_body() {
        let plan = HeaderPlan::for_no_body();
        assert_eq!(plan.len(), 1);
        assert_eq!(
            plan.ops[0],
            HeaderOp::DeleteAll {
                name: "Content-Length".to_string(),
            }
        );
    }
}
