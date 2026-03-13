use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter, MarkdownFlavor};
use nginx_markdown_converter::parser::parse_html;

fn convert_table_html(html: &[u8], flavor: MarkdownFlavor) -> String {
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::with_options(ConversionOptions {
        flavor,
        ..Default::default()
    });

    converter.convert(&dom).expect("Conversion failed")
}

#[test]
fn table_should_convert_basic_gfm_layout() {
    let result = convert_table_html(
        b"<table><thead><tr><th>Header 1</th><th>Header 2</th></tr></thead><tbody><tr><td>Cell 1</td><td>Cell 2</td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Header 1 | Header 2 |"));
    assert!(result.contains("| --- | --- |"));
    assert!(result.contains("| Cell 1 | Cell 2 |"));
}

#[test]
fn table_should_fall_back_to_text_in_commonmark_mode() {
    let result = convert_table_html(
        b"<table><thead><tr><th>Header</th></tr></thead><tbody><tr><td>Cell</td></tr></tbody></table>",
        MarkdownFlavor::CommonMark,
    );

    assert!(!result.contains("|"));
    assert!(result.contains("Header"));
    assert!(result.contains("Cell"));
}

#[test]
fn table_should_emit_left_alignment_separator() {
    let result = convert_table_html(
        b"<table><thead><tr><th align=\"left\">Left</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| --- |"));
    assert!(result.contains("| Left |"));
}

#[test]
fn table_should_emit_center_alignment_separator() {
    let result = convert_table_html(
        b"<table><thead><tr><th align=\"center\">Center</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| :---: |"));
    assert!(result.contains("| Center |"));
}

#[test]
fn table_should_emit_right_alignment_separator() {
    let result = convert_table_html(
        b"<table><thead><tr><th align=\"right\">Right</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| ---: |"));
    assert!(result.contains("| Right |"));
}

#[test]
fn table_should_preserve_mixed_alignments() {
    let result = convert_table_html(
        b"<table><thead><tr><th align=\"left\">Left</th><th align=\"center\">Center</th><th align=\"right\">Right</th></tr></thead><tbody><tr><td>A</td><td>B</td><td>C</td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| --- | :---: | ---: |"));
    assert!(result.contains("| Left | Center | Right |"));
    assert!(result.contains("| A | B | C |"));
}

#[test]
fn table_should_detect_style_based_alignment() {
    let result = convert_table_html(
        b"<table><thead><tr><th style=\"text-align: center\">Styled</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| :---: |"));
}

#[test]
fn table_should_convert_without_thead() {
    let result = convert_table_html(
        b"<table><tr><th>Header 1</th><th>Header 2</th></tr><tr><td>Cell 1</td><td>Cell 2</td></tr></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Header 1 | Header 2 |"));
    assert!(result.contains("| --- | --- |"));
    assert!(result.contains("| Cell 1 | Cell 2 |"));
}

#[test]
fn table_should_include_multiple_rows() {
    let result = convert_table_html(
        b"<table><thead><tr><th>Name</th><th>Age</th></tr></thead><tbody><tr><td>Alice</td><td>30</td></tr><tr><td>Bob</td><td>25</td></tr><tr><td>Charlie</td><td>35</td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Name | Age |"));
    assert!(result.contains("| Alice | 30 |"));
    assert!(result.contains("| Bob | 25 |"));
    assert!(result.contains("| Charlie | 35 |"));
}

#[test]
fn table_should_preserve_empty_cells() {
    let result = convert_table_html(
        b"<table><thead><tr><th>Col1</th><th>Col2</th></tr></thead><tbody><tr><td>Data</td><td></td></tr><tr><td></td><td>Data</td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Col1 | Col2 |"));
    assert!(result.contains("| Data | |"));
    assert!(result.contains("| | Data |"));
}

#[test]
fn table_should_pad_uneven_rows() {
    let result = convert_table_html(
        b"<table><thead><tr><th>A</th><th>B</th><th>C</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr><tr><td>3</td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| A | B | C |"));
    assert!(result.contains("| 1 | 2 | |"));
    assert!(result.contains("| 3 | | |"));
}

#[test]
fn table_should_preserve_inline_formatting() {
    let result = convert_table_html(
        b"<table><thead><tr><th>Name</th><th>Status</th></tr></thead><tbody><tr><td><strong>Bold</strong></td><td><em>Italic</em></td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Name | Status |"));
    assert!(result.contains("| **Bold** | *Italic* |"));
}

#[test]
fn table_should_preserve_links() {
    let result = convert_table_html(
        b"<table><thead><tr><th>Site</th></tr></thead><tbody><tr><td><a href=\"https://example.com\">Example</a></td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Site |"));
    assert!(result.contains("| [Example](https://example.com) |"));
}

#[test]
fn table_should_preserve_inline_code() {
    let result = convert_table_html(
        b"<table><thead><tr><th>Function</th></tr></thead><tbody><tr><td><code>print()</code></td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Function |"));
    assert!(result.contains("| `print()` |"));
}

#[test]
fn table_should_keep_blank_lines_around_block() {
    let result = convert_table_html(
        b"<p>Before table</p><table><thead><tr><th>Header</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table><p>After table</p>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("Before table\n\n|"));
    assert!(result.contains("|\n\nAfter table"));
}

#[test]
fn table_should_render_thead_only_tables() {
    let result = convert_table_html(
        b"<table><thead><tr><th>Header</th></tr></thead></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Header |"));
    assert!(result.contains("| --- |"));
}

#[test]
fn table_should_treat_first_td_row_as_header() {
    let result = convert_table_html(
        b"<table><tr><td>Header 1</td><td>Header 2</td></tr><tr><td>Cell 1</td><td>Cell 2</td></tr></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Header 1 | Header 2 |"));
    assert!(result.contains("| --- | --- |"));
    assert!(result.contains("| Cell 1 | Cell 2 |"));
}

#[test]
fn table_should_skip_empty_table_markup() {
    let result = convert_table_html(b"<table></table>", MarkdownFlavor::GitHubFlavoredMarkdown);

    assert!(!result.contains("|"));
}

#[test]
fn table_should_normalize_cell_whitespace() {
    let result = convert_table_html(
        b"<table><thead><tr><th>  Header  </th></tr></thead><tbody><tr><td>  Data  with   spaces  </td></tr></tbody></table>",
        MarkdownFlavor::GitHubFlavoredMarkdown,
    );

    assert!(result.contains("| Header |"));
    assert!(result.contains("| Data with spaces |"));
}
