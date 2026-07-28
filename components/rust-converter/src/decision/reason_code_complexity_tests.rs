use super::ALL;

#[test]
fn test_metric_keys_prometheus_format() {
    for rc in &ALL {
        let key = rc.metric_key();
        assert!(!key.is_empty(), "{:?} has empty metric key", rc);
        assert!(
            key.starts_with("markdown_"),
            "Metric key '{}' must start with 'markdown_'",
            key
        );
        assert!(
            key.ends_with("_total"),
            "Metric key '{}' must end with '_total' (counter)",
            key
        );
        for ch in key.chars() {
            assert!(
                ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '_',
                "Metric key '{}' contains invalid char '{}'",
                key,
                ch
            );
        }
    }
}
