use super::ALL_ERROR_CLASSES;

#[test]
fn test_error_class_strings_are_snake_case() {
    for class in &ALL_ERROR_CLASSES {
        let value = class.as_str();
        assert!(!value.is_empty());
        for ch in value.chars() {
            assert!(
                ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '_',
                "String '{}' for {:?} contains invalid char '{}'",
                value,
                class,
                ch
            );
        }
    }
}
