#![no_main]

use libfuzzer_sys::fuzz_target;
use markup5ever_rcdom::{Handle, NodeData};
use nginx_markdown_converter::parser::parse_html;
use nginx_markdown_converter::security::SecurityValidator;

fn walk_dom(handle: &Handle, validator: &SecurityValidator, depth: usize) {
    let _ = validator.validate_depth(depth);

    if let NodeData::Element { name, attrs, .. } = &handle.data {
        let attrs_ref = attrs.borrow();
        let _ = validator.check_element(name.local.as_ref());
        let _ = validator.check_attributes(&attrs_ref);
        let _ = validator.get_attributes_to_remove(&attrs_ref);
    }

    let children = handle.children.borrow().clone();
    for child in children {
        walk_dom(&child, validator, depth + 1);
    }
}

fuzz_target!(|data: &[u8]| {
    let input = String::from_utf8_lossy(data);
    let validator = SecurityValidator::with_max_depth((data.len() % 2048).max(1));

    let _ = validator.check_element(&input);
    let _ = validator.is_event_handler(&input);
    let _ = validator.is_dangerous_url(&input);
    let _ = validator.sanitize_url(&input);
    let _ = validator.validate_depth(data.len());

    if let Ok(dom) = parse_html(data) {
        walk_dom(&dom.document, &validator, 0);
    }
});
