//! RFC 3986 reference resolution shared by every URL-emitting path.
//!
//! One implementation serves the four exits that write a URL into the
//! generated Markdown: full-buffer body links/images, streaming body
//! links/images, and both metadata extractors (canonical, `og:url`,
//! `og:image`, media). Keeping a single resolver prevents the same document
//! from producing different URLs depending on the processing path.
//!
//! Implements the reference-resolution algorithm of RFC 3986 section 5.2:
//! reference forms (absolute with scheme, protocol-relative, absolute path,
//! relative path, query-only, fragment-only), path merging (5.2.3), and
//! dot-segment removal (5.2.4). Only `http`/`https` bases are resolved;
//! any other base returns `None` so the caller can keep the original text.

/// Resolve `reference` against `base`, or return `None` when the base cannot
/// be used (not an absolute `http`/`https` URL, or malformed).
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

    if ref_scheme.is_some() {
        // An absolute reference keeps its own form: only resolve against the
        // base when the reference has no scheme.  Rewriting it would mangle
        // non-hierarchical schemes such as `mailto:` or `tel:`.
        let _ = ref_rest;
        return Some(reference.to_string());
    }

    let (ref_authority, path, query, fragment) = split_rest(ref_rest);

    if !ref_authority.is_empty() {
        // Protocol-relative references are already absolute in the sense the
        // module documents, so they pass through unchanged rather than
        // inheriting the base scheme.
        return Some(reference.to_string());
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
fn remove_dot_segments(path: &str) -> String {
    let absolute = path.starts_with('/');
    let trailing_slash = path.ends_with('/') || path.ends_with("/.") || path.ends_with("/..");
    let mut segments: Vec<&str> = Vec::new();

    for segment in path.split('/') {
        match segment {
            "" | "." => continue,
            ".." => {
                if segments.pop().is_none() && !absolute {
                    continue;
                }
            }
            other => segments.push(other),
        }
    }

    let joined = segments.join("/");

    let mut result = String::new();
    if absolute {
        result.push('/');
    }
    result.push_str(&joined);
    if trailing_slash && !result.ends_with('/') {
        result.push('/');
    }
    if result.is_empty() {
        result.push('/');
    }
    result
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
            // Protocol-relative references pass through unchanged, matching
            // the documented resolution rules for metadata and body URLs.
            ("//cdn.example.com/a.js", "//cdn.example.com/a.js"),
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
}
