use super::MetadataExtractor;

impl MetadataExtractor {
    /// Resolve relative URL to absolute URL.
    pub fn resolve_url(&self, url: &str) -> String {
        if !self.resolve_urls || url.is_empty() {
            return url.to_string();
        }

        if url.starts_with("http://") || url.starts_with("https://") || url.starts_with("//") {
            return url.to_string();
        }

        let Some(ref base) = self.base_url else {
            return url.to_string();
        };

        if !self.is_valid_base_url(base) {
            return url.to_string();
        }

        if url.starts_with('/') {
            return format!("{}{}", self.get_origin(base), url);
        }

        let base_dir = self.get_base_directory(base);
        format!("{}/{}", base_dir.trim_end_matches('/'), url)
    }

    /// Validate whether `url` has an absolute HTTP(S)-style base form.
    fn is_valid_base_url(&self, url: &str) -> bool {
        url.starts_with("http://") || url.starts_with("https://")
    }

    /// Extract scheme + authority origin from an absolute URL string.
    fn get_origin(&self, url: &str) -> String {
        let after_scheme = if let Some(stripped) = url.strip_prefix("https://") {
            stripped
        } else if let Some(stripped) = url.strip_prefix("http://") {
            stripped
        } else {
            return url.to_string();
        };

        if let Some(pos) = after_scheme.find('/') {
            let scheme_len = if url.starts_with("https://") { 8 } else { 7 };
            url[..scheme_len + pos].to_string()
        } else {
            url.to_string()
        }
    }

    /// Return the directory prefix used for relative-path resolution.
    fn get_base_directory(&self, url: &str) -> String {
        let trimmed = url.trim_end_matches('/');

        if let Some(pos) = trimmed.rfind('/') {
            if pos > 0 && trimmed.chars().nth(pos - 1) == Some('/') {
                return trimmed.to_string();
            }
            trimmed[..pos].to_string()
        } else {
            trimmed.to_string()
        }
    }
}
