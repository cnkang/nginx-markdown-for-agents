//! RFC 3986 reference resolution shared by every URL-emitting path.
//!
//! One implementation serves the four exits that write a URL into the
//! generated Markdown: full-buffer body links/images, streaming body
//! links/images, and both metadata extractors (canonical, `og:url`,
//! `og:image`, media). Keeping a single resolver prevents the same document
//! from producing different URLs depending on the processing path.
//!
//! Implements the reference-resolution algorithm of RFC 3986 section 5.2:
//! reference forms (absolute with scheme, network-path, absolute path, relative
//! path, query-only, fragment-only), path merging (5.2.3), and dot-segment
//! removal (5.2.4), which preserves empty segments. A network-path reference
//! keeps its own authority and inherits the base scheme. Only `http`/`https`
//! bases are resolved; any other base returns `None` so the caller can keep the
//! original text.

/// Resolve `reference` against `base`, or return `None` when the base cannot
/// be used (not an absolute `http`/`https` URL, or malformed).
///
/// Callers must sanitize the reference first (for example with
/// `crate::security::sanitize_url_value`); this resolver performs no scheme
/// or character safety filtering of its own.
pub(crate) fn resolve_reference(base: &str, reference: &str) -> Option<String> {
    let (scheme, authority, base_path, base_query) = split_absolute(base)?;

    if reference.is_empty() {
        return Some(assemble(
            &scheme,
            &authority,
            &base_path,
            base_query.as_deref(),
            None,
        ));
    }

    let (ref_scheme, ref_rest) = split_scheme(reference);

    if let Some(ref_scheme) = ref_scheme {
        // An absolute reference keeps its own scheme and authority, so it is
        // never merged with the base.  A hierarchical `http`/`https`
        // reference still normalizes its path: RFC 3986 section 5.2.2
        // recomputes `T.path` as `remove_dot_segments(R.path)` for the
        // absolute form, so `https://h/a/b/../c` becomes `https://h/a/c`.
        // Every other scheme — opaque ones such as `mailto:`/`tel:`, and
        // non-http hierarchical ones — is returned verbatim: rewriting a
        // non-http reference would mangle an address the resolver does not
        // own, and the caller's sanitizer decides whether it may be emitted
        // at all.  A reference with a scheme but no `//authority` (RFC 3986
        // section 5.4 abnormal form, e.g. `http:g`) is also returned
        // verbatim, since there is no authority to reassemble.
        if !matches!(ref_scheme.as_str(), s if s.eq_ignore_ascii_case("http") || s.eq_ignore_ascii_case("https"))
        {
            return Some(reference.to_string());
        }
        let (ref_authority, path, query, fragment) = split_rest(ref_rest);
        if ref_authority.is_empty() {
            return Some(reference.to_string());
        }
        let target_path = if path.is_empty() {
            path
        } else {
            remove_dot_segments(&path)
        };
        return Some(assemble(
            &ref_scheme,
            &ref_authority,
            &target_path,
            query.as_deref(),
            fragment.as_deref(),
        ));
    }

    let (ref_authority, path, query, fragment) = split_rest(ref_rest);

    if !ref_authority.is_empty() {
        // A network-path reference keeps its own authority and inherits the base
        // scheme (RFC 3986 section 5.2, second reference form), so
        // `//cdn.example.com/x` becomes `https://cdn.example.com/x`. Emitting it
        // unchanged would hand a Markdown consumer a scheme-relative string it
        // cannot fetch or compare.
        let target_path = if path.is_empty() {
            String::new()
        } else {
            remove_dot_segments(&path)
        };
        return Some(assemble(
            &scheme,
            &ref_authority,
            &target_path,
            query.as_deref(),
            fragment.as_deref(),
        ));
    }

    // Every other form keeps the base authority: only the base is a full URL.
    let target_path = if path.is_empty() {
        // Same-document reference: keep the base path, and the base query
        // unless the reference carries its own.
        let query = query.clone().or_else(|| base_query.clone());
        return Some(assemble(
            &scheme,
            &authority,
            &base_path,
            query.as_deref(),
            fragment.as_deref(),
        ));
    } else if path.starts_with('/') {
        remove_dot_segments(&path)
    } else {
        remove_dot_segments(&merge(&base_path, &path))
    };

    Some(assemble(
        &scheme,
        &authority,
        &target_path,
        query.as_deref(),
        fragment.as_deref(),
    ))
}

/// Split an absolute `http(s)` URL into scheme, authority, path, and query.
fn split_absolute(url: &str) -> Option<(String, String, String, Option<String>)> {
    let (scheme, rest) = split_scheme(url);
    let scheme = scheme?;

    if !scheme.eq_ignore_ascii_case("http") && !scheme.eq_ignore_ascii_case("https") {
        return None;
    }

    let (authority, path, query, _fragment) = split_rest(rest);

    if authority.is_empty() {
        return None;
    }

    Some((scheme, authority, path, query))
}

/// Split a leading `scheme:` off, when the text starts with a valid scheme.
fn split_scheme(text: &str) -> (Option<String>, &str) {
    for (index, byte) in text.as_bytes().iter().enumerate() {
        let continues = match *byte {
            b'a'..=b'z' | b'A'..=b'Z' => true,
            b'0'..=b'9' | b'+' | b'-' | b'.' => index > 0,
            b':' => {
                if index == 0 {
                    return (None, text);
                }
                return (Some(text[..index].to_string()), &text[index + 1..]);
            }
            _ => false,
        };

        if !continues {
            break;
        }
    }

    (None, text)
}

/// Split `//authority`, path, query, and fragment out of the text after a scheme.
fn split_rest(rest: &str) -> (String, String, Option<String>, Option<String>) {
    let (without_fragment, fragment) = match rest.find('#') {
        Some(pos) => (&rest[..pos], Some(rest[pos + 1..].to_string())),
        None => (rest, None),
    };

    let (without_query, query) = match without_fragment.find('?') {
        Some(pos) => (
            &without_fragment[..pos],
            Some(without_fragment[pos + 1..].to_string()),
        ),
        None => (without_fragment, None),
    };

    if let Some(body) = without_query.strip_prefix("//") {
        let end = body.find(['/', '?', '#']).unwrap_or(body.len());
        return (
            body[..end].to_string(),
            body[end..].to_string(),
            query,
            fragment,
        );
    }

    (String::new(), without_query.to_string(), query, fragment)
}

/// Merge a relative path with the base path (RFC 3986 section 5.2.3).
fn merge(base_path: &str, path: &str) -> String {
    match base_path.rfind('/') {
        Some(pos) => format!("{}{}", &base_path[..=pos], path),
        None => format!("/{}", path),
    }
}

/// Remove `.` and `..` segments (RFC 3986 section 5.2.4).
///
/// The trailing slashes belong to the path: `/a//` keeps both of them, and the
/// dot-suffix forms `/a/.` and `/a/..` end in exactly one.  Counting the run
/// first keeps every trailing slash, which re-attaching a single one cannot.
fn remove_last_segment(output: &mut String) {
    match output.rfind('/') {
        Some(index) => output.truncate(index),
        None => output.clear(),
    }
}

fn remove_dot_segments(path: &str) -> String {
    // The algorithm of RFC 3986 section 5.2.4, driven by an offset into the
    // input rather than by rewriting it: each step consumes a prefix or moves
    // one segment to the output, so nothing is reallocated per iteration.
    let mut rest = path;
    let mut output = String::new();

    while !rest.is_empty() {
        let consumed = if let Some(r) = rest.strip_prefix("../") {
            rest.len() - r.len()
        } else if let Some(r) = rest.strip_prefix("./") {
            rest.len() - r.len()
        } else if let Some(r) = rest.strip_prefix("/./") {
            rest.len() - r.len() - 1
        } else if rest == "/." {
            // The RFC replaces the whole input with `/`, so the loop continues
            // with that rather than consuming the dot.
            rest = "/";
            continue;
        } else if let Some(r) = rest.strip_prefix("/../") {
            remove_last_segment(&mut output);
            rest.len() - r.len() - 1
        } else if rest == "/.." {
            remove_last_segment(&mut output);
            rest = "/";
            continue;
        } else if rest == "." || rest == ".." {
            rest.len()
        } else {
            let start = usize::from(rest.starts_with('/'));
            let end = match rest[start..].find('/') {
                Some(index) => start + index,
                None => rest.len(),
            };
            output.push_str(&rest[..end]);
            end
        };
        rest = &rest[consumed..];
    }

    output
}

/// Reassemble a resolved URL.
fn assemble(
    scheme: &str,
    authority: &str,
    path: &str,
    query: Option<&str>,
    fragment: Option<&str>,
) -> String {
    let mut out = format!("{}://{}{}", scheme, authority, path);
    if let Some(query) = query {
        out.push('?');
        out.push_str(query);
    }
    if let Some(fragment) = fragment {
        out.push('#');
        out.push_str(fragment);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::resolve_reference;

    const BASE: &str = "https://example.com/docs/page.html";

    #[test]
    fn resolves_the_reference_forms_rfc3986_requires() {
        let cases = [
            // absolute and protocol-relative references
            ("https://other.example/x", "https://other.example/x"),
            ("mailto:user@example.com", "mailto:user@example.com"),
            ("tel:+1234", "tel:+1234"),
            // A network-path reference keeps its own authority and inherits the
            // base scheme (RFC 3986 section 5.2).
            ("//cdn.example.com/a.js", "https://cdn.example.com/a.js"),
            ("//cdn.example.com", "https://cdn.example.com"),
            // Empty segments are preserved (section 5.2.4), including a run of
            // trailing slashes: `/a//` keeps both of them.
            ("/a//b", "https://example.com/a//b"),
            ("/a//b/./c", "https://example.com/a//b/c"),
            ("/a//", "https://example.com/a//"),
            ("/a///", "https://example.com/a///"),
            ("/a/b//", "https://example.com/a/b//"),
            // A trailing dot segment resolves to one slash, and `..` climbs.
            ("/a/.", "https://example.com/a/"),
            ("/a//.", "https://example.com/a//"),
            // A `..` that climbs away leaves the empty segment `//` created.
            ("/a/..//.", "https://example.com//"),
            ("/a///.", "https://example.com/a///"),
            ("/a/b/.", "https://example.com/a/b/"),
            ("/a/..", "https://example.com/"),
            ("/a/b/..", "https://example.com/a/"),
            ("//cdn.example.com/a//", "https://cdn.example.com/a//"),
            // absolute path
            ("/hero.png", "https://example.com/hero.png"),
            // relative paths, including dot segments
            ("article", "https://example.com/docs/article"),
            ("icons/logo.svg", "https://example.com/docs/icons/logo.svg"),
            ("./article", "https://example.com/docs/article"),
            ("../api", "https://example.com/api"),
            ("../../api", "https://example.com/api"),
            // `/docs/..` climbs out of the directory, `/./` is a no-op, and
            // the following `mixed/..` pair cancels itself.
            (".././mixed/../thing", "https://example.com/thing"),
            // same-document references keep the document path
            ("#section", "https://example.com/docs/page.html#section"),
            ("?print=1", "https://example.com/docs/page.html?print=1"),
            ("", "https://example.com/docs/page.html"),
        ];

        for (reference, expected) in cases {
            assert_eq!(
                resolve_reference(BASE, reference).as_deref(),
                Some(expected),
                "reference {reference:?}"
            );
        }
    }

    #[test]
    fn keeps_a_base_query_only_when_the_reference_has_none() {
        let base = "https://example.com/docs/page.html?v=2#top";
        assert_eq!(
            resolve_reference(base, "#frag").as_deref(),
            Some("https://example.com/docs/page.html?v=2#frag")
        );
        assert_eq!(
            resolve_reference(base, "?q=1").as_deref(),
            Some("https://example.com/docs/page.html?q=1")
        );
        assert_eq!(
            resolve_reference(base, "next").as_deref(),
            Some("https://example.com/docs/next")
        );
    }

    #[test]
    fn handles_directory_bases_and_ipv6_authorities() {
        assert_eq!(
            resolve_reference("https://example.com/dir/", "a.png").as_deref(),
            Some("https://example.com/dir/a.png")
        );
        assert_eq!(
            resolve_reference("https://[2001:db8::1]:8443/a/b", "c").as_deref(),
            Some("https://[2001:db8::1]:8443/a/c")
        );
        assert_eq!(
            resolve_reference("https://example.com", "a").as_deref(),
            Some("https://example.com/a")
        );
    }

    #[test]
    fn refuses_a_base_that_is_not_absolute_http() {
        for base in ["/dir/page.html", "ftp://example.com/x", "example.com/x", ""] {
            assert!(resolve_reference(base, "a").is_none(), "base {base:?}");
        }
    }

    /// Contract: the resolver is not a sanitizer. An absolute-scheme
    /// reference is returned verbatim, so callers must run references
    /// through `security::sanitize_url_value` (which rejects `javascript:`
    /// and other dangerous schemes) before emitting the resolved URL.
    #[test]
    fn resolver_defers_safety_filtering_to_callers() {
        assert_eq!(
            resolve_reference(BASE, "javascript:alert(1)").as_deref(),
            Some("javascript:alert(1)"),
            "the resolver must not silently filter schemes; callers sanitize"
        );
        assert!(
            crate::security::sanitize_url_value("javascript:alert(1)").is_none(),
            "the sanitizer is the layer that rejects the dangerous reference"
        );
        assert_eq!(
            resolve_reference(BASE, "https://example.com/x").as_deref(),
            Some("https://example.com/x")
        );
    }

    /// RFC 3986 section 5.2.2: an absolute `http`/`https` reference is not
    /// merged with the base, but its own path still goes through
    /// `remove_dot_segments`.
    #[test]
    fn absolute_http_reference_removes_dot_segments() {
        let cases = [
            (
                "https://other.example/a/b/../c",
                "https://other.example/a/c",
            ),
            ("https://other.example/a/./b", "https://other.example/a/b"),
            ("https://other.example/a/b/..", "https://other.example/a/"),
            ("https://other.example/../x", "https://other.example/x"),
            ("https://other.example/a/..//b", "https://other.example//b"),
            /* Query and fragment are preserved and are not part of the path
             * handed to remove_dot_segments. */
            (
                "https://other.example/a/b/../c?x=1#f",
                "https://other.example/a/c?x=1#f",
            ),
            /* A bare absolute authority keeps no path. */
            ("https://other.example", "https://other.example"),
            ("https://other.example/", "https://other.example/"),
            /* Scheme comparison is case-insensitive; the emitted scheme keeps
             * the reference's own spelling. */
            ("HTTP://other.example/a/b/../c", "HTTP://other.example/a/c"),
        ];

        for (reference, expected) in cases {
            assert_eq!(
                resolve_reference(BASE, reference).as_deref(),
                Some(expected),
                "reference {reference:?}"
            );
        }
    }

    /// Opaque and non-http schemes are returned byte-for-byte: the resolver
    /// has no path semantics for them and must not rewrite the address.
    #[test]
    fn opaque_schemes_are_returned_verbatim() {
        let cases = [
            "mailto:user@example.com",
            "mailto:user@example.com?subject=a..b",
            "tel:+1234",
            "javascript:alert(1)",
            "data:text/html;base64,PGI+",
            "ftp://example.com/a/../b",
            "urn:isbn:0451450523",
            /* Scheme with no `//authority` (RFC 3986 section 5.4 abnormal
             * form): cannot be reassembled, so it is left alone. */
            "http:g",
            "https:relative/../path",
        ];

        for reference in cases {
            assert_eq!(
                resolve_reference(BASE, reference).as_deref(),
                Some(reference),
                "{reference:?} must not be rewritten"
            );
        }
    }
}
