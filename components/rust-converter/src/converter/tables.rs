//! HTML table to GFM Markdown table conversion.
//!
//! This module handles conversion of HTML `<table>` elements into GitHub
//! Flavored Markdown (GFM) pipe tables. Table conversion is only active when
//! the converter is configured with [`MarkdownFlavor::GitHubFlavoredMarkdown`]
//! and `preserve_tables` is enabled; otherwise, table content is traversed
//! as plain inline content.
//!
//! # GFM Table Format
//!
//! The output follows the GFM pipe-table specification:
//!
//! ```markdown
//! | Header 1 | Header 2 | Header 3 |
//! | --- |:---:| ---:|
//! | Cell 1   | Cell 2   | Cell 3   |
//! ```
//!
//! # Alignment
//!
//! Column alignment is extracted from `<colgroup>/<col>` elements and
//! `<th>` style/align attributes:
//! - `style="text-align: left"` or `align="left"` → `---`
//! - `style="text-align: center"` or `align="center"` → `:---:`
//! - `style="text-align: right"` or `align="right"` → `---:`
//! - Default (no alignment) → `---`
//!
//! `<colgroup>/<col>` alignment takes precedence over `<th>` alignment
//! when both are present, because `<col>` is a column-level declaration
//! that applies to all rows in the column.
//!
//! # Edge Cases
//!
//! - Tables without `<thead>` use the first `<tr>` as the header row.
//! - Tables with mismatched column counts are padded with empty cells.
//! - Empty tables (no rows) produce no output.
//! - Cell content is recursively converted, allowing inline Markdown within cells.

use super::traversal::{append_char_with_context, append_str_with_context};
use super::{
    ConversionContext, ConversionError, Handle, MarkdownConverter, MarkdownFlavor, NodeData,
    TableAlignment,
};
use html5ever::Attribute;
use std::cell::Ref;

impl MarkdownConverter {
    fn extract_table_body(
        &self,
        tbody: &Handle,
        ctx: &mut Option<&mut ConversionContext>,
        headers: &mut Vec<String>,
        alignments: &mut Vec<TableAlignment>,
        rows: &mut Vec<Vec<String>>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        if !headers.is_empty() {
            return self.extract_table_rows(tbody, ctx.as_deref_mut(), rows, table_scratch);
        }

        let children = tbody.children.borrow();
        let first_tr = children.iter().find(|candidate| {
            matches!(
                candidate.data,
                NodeData::Element { ref name, .. } if name.local.as_ref() == "tr"
            )
        });
        let Some(first_tr) = first_tr else {
            return self.extract_table_rows(tbody, ctx.as_deref_mut(), rows, table_scratch);
        };

        self.extract_table_row_as_header(
            first_tr,
            ctx.as_deref_mut(),
            headers,
            alignments,
            table_scratch,
        )?;

        let mut is_first = true;
        for tbody_child in children.iter() {
            if let NodeData::Element { ref name, .. } = tbody_child.data
                && name.local.as_ref() == "tr"
            {
                if is_first {
                    is_first = false;
                    continue;
                }

                let mut row_cells = Vec::new();
                self.extract_table_row(
                    tbody_child,
                    ctx.as_deref_mut(),
                    &mut row_cells,
                    table_scratch,
                )?;
                Self::charge_vec_growth(&mut *rows, ctx, table_scratch)?;
                rows.push(row_cells);
            }
        }
        Ok(())
    }

    fn extract_table_child(
        &self,
        child: &Handle,
        ctx: &mut Option<&mut ConversionContext>,
        headers: &mut Vec<String>,
        alignments: &mut Vec<TableAlignment>,
        rows: &mut Vec<Vec<String>>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        let NodeData::Element { ref name, .. } = child.data else {
            return Ok(());
        };

        match name.local.as_ref() {
            "thead" => self.extract_table_header(
                child,
                ctx.as_deref_mut(),
                headers,
                alignments,
                table_scratch,
            ),
            "tbody" => {
                self.extract_table_body(child, ctx, headers, alignments, rows, table_scratch)
            }
            "tr" if headers.is_empty() => self.extract_table_row_as_header(
                child,
                ctx.as_deref_mut(),
                headers,
                alignments,
                table_scratch,
            ),
            "tr" => {
                let mut row_cells = Vec::new();
                self.extract_table_row(child, ctx.as_deref_mut(), &mut row_cells, table_scratch)?;
                Self::charge_vec_growth(&mut *rows, ctx, table_scratch)?;
                rows.push(row_cells);
                Ok(())
            }
            _ => Ok(()),
        }
    }

    /// Handle table elements (GFM only) with optional timeout context.
    pub(super) fn handle_table_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        if !(matches!(self.options.flavor, MarkdownFlavor::GitHubFlavoredMarkdown)
            && self.options.preserve_tables)
        {
            return self.traverse_children(node, output, depth + 1, ctx);
        }

        let mut ctx = ctx;
        Self::ensure_blank_line_before(output, &mut ctx)?;

        let output_capacity = output.capacity();
        let mut output_charge_active = false;
        if let Some(context) = ctx.as_deref_mut() {
            context.reserve_working_set(output_capacity)?;
            output_charge_active = true;
        }
        let mut output_charge_released = false;
        let mut table_scratch = 0usize;

        let result = (|| {
            let mut headers: Vec<String> = Vec::new();
            let mut alignments: Vec<TableAlignment> = Vec::new();
            let mut rows: Vec<Vec<String>> = Vec::new();

            let mut col_alignments: Vec<Option<TableAlignment>> = Vec::new();
            self.extract_colgroup_alignments_from(
                node,
                &mut ctx,
                &mut col_alignments,
                &mut table_scratch,
            )?;

            for child in node.children.borrow().iter() {
                self.extract_table_child(
                    child,
                    &mut ctx,
                    &mut headers,
                    &mut alignments,
                    &mut rows,
                    &mut table_scratch,
                )?;
            }

            if headers.is_empty() {
                return Ok(());
            }

            while alignments.len() < headers.len() {
                Self::charge_vec_growth(&mut alignments, &mut ctx, &mut table_scratch)?;
                alignments.push(TableAlignment::Left);
            }

            Self::apply_col_alignments(
                &mut alignments,
                &col_alignments,
                &mut ctx,
                &mut table_scratch,
            )?;

            // The final output buffer was retained as working-set charge
            // while cells were collected.  Release that charge before the
            // table writer runs so capacity growth is checked against the
            // actual live cell scratch only, not counted twice.
            if output_charge_active {
                if let Some(context) = ctx.as_deref_mut() {
                    context.release_working_set(output_capacity);
                }
                output_charge_released = true;
            }

            self.write_gfm_table(output, &headers, &alignments, &rows, ctx.as_deref_mut())?;

            if !output.ends_with("\n\n") {
                append_char_with_context(output, '\n', &mut ctx)?;
            }

            Ok(())
        })();

        if let Some(context) = ctx {
            if output_charge_active && !output_charge_released {
                context.release_working_set(output_capacity);
            }
            context.release_working_set(table_scratch);
        }
        result
    }

    fn ensure_blank_line_before(
        output: &mut String,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                append_char_with_context(output, '\n', ctx)?;
            } else {
                append_str_with_context(output, "\n\n", ctx)?;
            }
        }
        Ok(())
    }

    fn extract_colgroup_alignments_from(
        &self,
        node: &Handle,
        ctx: &mut Option<&mut ConversionContext>,
        col_alignments: &mut Vec<Option<TableAlignment>>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        for child in node.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "colgroup"
            {
                self.extract_colgroup_alignments(child, ctx, col_alignments, table_scratch)?;
            }
        }
        Ok(())
    }

    fn apply_col_alignments(
        alignments: &mut Vec<TableAlignment>,
        col_alignments: &[Option<TableAlignment>],
        ctx: &mut Option<&mut ConversionContext>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        for (i, col_align) in col_alignments.iter().enumerate() {
            if let Some(col_align) = col_align {
                if i < alignments.len() {
                    alignments[i] = *col_align;
                } else {
                    let required_len = i.checked_add(1).ok_or_else(|| {
                        ConversionError::MemoryLimit("table alignment size overflow".into())
                    })?;
                    Self::charge_vec_growth_to(alignments, required_len, ctx, table_scratch)?;
                    alignments.resize(required_len, TableAlignment::Left);
                    alignments[i] = *col_align;
                }
            }
        }
        Ok(())
    }

    /// Extract column alignments from a `<colgroup>` element.
    ///
    /// Each `<col>` child's explicit `align` attribute or
    /// `style="text-align: ..."` is resolved using the same alignment rules
    /// as header cells. Bare columns are preserved as `None`.
    pub(super) fn extract_colgroup_alignments(
        &self,
        colgroup: &Handle,
        ctx: &mut Option<&mut ConversionContext>,
        col_alignments: &mut Vec<Option<TableAlignment>>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        for child in colgroup.children.borrow().iter() {
            if let NodeData::Element {
                ref name,
                ref attrs,
                ..
            } = child.data
                && name.local.as_ref() == "col"
            {
                let attrs_borrowed = attrs.borrow();
                let span = attrs_borrowed
                    .iter()
                    .find(|attr| attr.name.local.as_ref() == "span")
                    .and_then(|attr| attr.value.parse::<usize>().ok())
                    .unwrap_or(1)
                    .clamp(1, 1_000);
                let alignment = self.extract_explicit_alignment(&attrs_borrowed);
                for _ in 0..span {
                    Self::charge_vec_growth(col_alignments, ctx, table_scratch)?;
                    col_alignments.push(alignment);
                }
            }
        }
        Ok(())
    }

    /// Extract header cells from a `<thead>` section.
    pub(super) fn extract_table_header(
        &self,
        thead: &Handle,
        ctx: Option<&mut ConversionContext>,
        headers: &mut Vec<String>,
        alignments: &mut Vec<TableAlignment>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        for child in thead.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "tr"
            {
                self.extract_table_row_as_header(
                    child,
                    ctx.as_deref_mut(),
                    headers,
                    alignments,
                    table_scratch,
                )?;
                break;
            }
        }

        Ok(())
    }

    /// Treat one `<tr>` as the table header row.
    ///
    /// This path is used for explicit `<thead>` rows and for fallback when a
    /// table omits `<thead>` but starts with header-like cells.
    pub(super) fn extract_table_row_as_header(
        &self,
        tr: &Handle,
        ctx: Option<&mut ConversionContext>,
        headers: &mut Vec<String>,
        alignments: &mut Vec<TableAlignment>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        for child in tr.children.borrow().iter() {
            if let NodeData::Element {
                ref name,
                ref attrs,
                ..
            } = child.data
            {
                let tag = name.local.as_ref();
                if tag == "th" || tag == "td" {
                    let mut cell_output = String::new();
                    for cell_child in child.children.borrow().iter() {
                        self.traverse_node_optional(
                            cell_child,
                            &mut cell_output,
                            0,
                            ctx.as_deref_mut(),
                        )?;
                    }

                    Self::trim_cell_output(&mut cell_output);
                    Self::charge_cell_output(&cell_output, &mut ctx, table_scratch)?;
                    Self::charge_vec_growth(&mut *headers, &mut ctx, table_scratch)?;
                    headers.push(cell_output);
                    let attrs_borrowed = attrs.borrow();
                    Self::charge_vec_growth(&mut *alignments, &mut ctx, table_scratch)?;
                    alignments.push(self.extract_alignment(&attrs_borrowed));
                }
            }
        }

        Ok(())
    }

    /// Extract all body rows from a `<tbody>` section.
    pub(super) fn extract_table_rows(
        &self,
        tbody: &Handle,
        ctx: Option<&mut ConversionContext>,
        rows: &mut Vec<Vec<String>>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        for child in tbody.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "tr"
            {
                let mut row_cells = Vec::new();
                self.extract_table_row(child, ctx.as_deref_mut(), &mut row_cells, table_scratch)?;
                Self::charge_vec_growth(&mut *rows, &mut ctx, table_scratch)?;
                rows.push(row_cells);
            }
        }

        Ok(())
    }

    /// Extract normalized text cells from a `<tr>` element.
    pub(super) fn extract_table_row(
        &self,
        tr: &Handle,
        ctx: Option<&mut ConversionContext>,
        cells: &mut Vec<String>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        for child in tr.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data {
                let tag = name.local.as_ref();
                if tag == "td" || tag == "th" {
                    let mut cell_output = String::new();
                    for cell_child in child.children.borrow().iter() {
                        self.traverse_node_optional(
                            cell_child,
                            &mut cell_output,
                            0,
                            ctx.as_deref_mut(),
                        )?;
                    }
                    Self::trim_cell_output(&mut cell_output);
                    Self::charge_cell_output(&cell_output, &mut ctx, table_scratch)?;
                    Self::charge_vec_growth(&mut *cells, &mut ctx, table_scratch)?;
                    cells.push(cell_output);
                }
            }
        }

        Ok(())
    }

    fn trim_cell_output(output: &mut String) {
        let start = output.len() - output.trim_start().len();
        let end = output.trim_end().len();
        if start > 0 {
            output.drain(..start);
        }
        output.truncate(end.saturating_sub(start));
    }

    fn charge_cell_output(
        output: &String,
        ctx: &mut Option<&mut ConversionContext>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        let capacity = output.capacity();
        if let Some(context) = ctx.as_deref_mut() {
            let next = table_scratch.checked_add(capacity).ok_or_else(|| {
                ConversionError::MemoryLimit("table working-set size overflow".into())
            })?;
            context.reserve_working_set(capacity)?;
            *table_scratch = next;
        }
        Ok(())
    }

    /// Charge the backing allocation of a container Vec before it grows.
    ///
    /// The table pipeline keeps several container Vecs alive while cells are
    /// collected (headers, alignments, rows, per-row cell lists).  Their
    /// backing allocations are not Markdown output, so they must be charged
    /// against the working set explicitly; otherwise a table with many empty
    /// cells can amplify memory far beyond the configured budget while
    /// `working_set_bytes` stays low.
    fn charge_vec_growth<T>(
        vec: &mut Vec<T>,
        ctx: &mut Option<&mut ConversionContext>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        let required_len = vec
            .len()
            .checked_add(1)
            .ok_or_else(|| ConversionError::MemoryLimit("table container size overflow".into()))?;
        Self::charge_vec_growth_to(vec, required_len, ctx, table_scratch)
    }

    fn charge_vec_growth_to<T>(
        vec: &mut Vec<T>,
        required_len: usize,
        ctx: &mut Option<&mut ConversionContext>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        if required_len <= vec.capacity() {
            return Ok(());
        }

        let old_capacity = vec.capacity();
        let doubled_capacity = old_capacity
            .max(1)
            .checked_mul(2)
            .ok_or_else(|| ConversionError::MemoryLimit("table container size overflow".into()))?;
        let requested_capacity = doubled_capacity.max(required_len);
        let additional = requested_capacity
            .checked_sub(vec.len())
            .ok_or_else(|| ConversionError::MemoryLimit("table container size overflow".into()))?;
        let old_bytes = old_capacity
            .checked_mul(std::mem::size_of::<T>())
            .ok_or_else(|| ConversionError::MemoryLimit("table container size overflow".into()))?;
        if old_bytes > *table_scratch {
            return Err(ConversionError::MemoryLimit(
                "table container charge is inconsistent".into(),
            ));
        }
        let requested_bytes = requested_capacity
            .checked_mul(std::mem::size_of::<T>())
            .ok_or_else(|| ConversionError::MemoryLimit("table container size overflow".into()))?;
        let requested_growth = requested_bytes
            .checked_sub(old_bytes)
            .ok_or_else(|| ConversionError::MemoryLimit("table container size overflow".into()))?;
        if let Some(context) = ctx.as_deref_mut() {
            context.reserve_working_set(requested_growth)?;
        }

        if let Err(error) = vec.try_reserve_exact(additional) {
            if let Some(context) = ctx.as_deref_mut() {
                context.release_working_set(requested_growth);
            }
            return Err(ConversionError::MemoryLimit(format!(
                "unable to reserve {} bytes for table container: {}",
                requested_bytes, error
            )));
        }

        let actual_capacity = vec.capacity();
        let actual_bytes = actual_capacity
            .checked_mul(std::mem::size_of::<T>())
            .ok_or_else(|| ConversionError::MemoryLimit("table container size overflow".into()))?;
        if actual_bytes > requested_bytes {
            let extra = actual_bytes - requested_bytes;
            if let Some(context) = ctx.as_deref_mut()
                && let Err(error) = context.reserve_working_set(extra)
            {
                context.release_working_set(requested_growth);
                return Err(ConversionError::MemoryLimit(format!(
                    "table container capacity {} bytes exceeds budget: {}",
                    actual_bytes, error
                )));
            }
        } else if let Some(context) = ctx.as_deref_mut() {
            context.release_working_set(requested_bytes - actual_bytes);
        }

        if let Some(context) = ctx.as_deref_mut() {
            // Replace the old backing-allocation charge after reallocating.
            // The provisional growth charge has already proved the budget;
            // rebuilding the charge makes the actual capacity authoritative.
            let actual_growth = actual_bytes.checked_sub(old_bytes).ok_or_else(|| {
                ConversionError::MemoryLimit("table container size overflow".into())
            })?;
            context.release_working_set(actual_growth);
            context.release_working_set(old_bytes);
            context.reserve_working_set(actual_bytes)?;
        }
        *table_scratch = table_scratch
            .checked_sub(old_bytes)
            .and_then(|base| base.checked_add(actual_bytes))
            .ok_or_else(|| ConversionError::MemoryLimit("table container size overflow".into()))?;
        Ok(())
    }

    /// Resolve column alignment from HTML attributes.
    ///
    /// Priority is `align="..."` first, then CSS `text-align` from `style`.
    /// Unknown values fall back to left alignment.
    pub(super) fn extract_alignment(&self, attrs: &Ref<Vec<Attribute>>) -> TableAlignment {
        self.extract_explicit_alignment(attrs)
            .unwrap_or(TableAlignment::Left)
    }

    fn extract_explicit_alignment(&self, attrs: &Ref<Vec<Attribute>>) -> Option<TableAlignment> {
        if let Some(Some(align)) = self.find_align_attribute(attrs) {
            return Some(align);
        }
        self.find_text_align_in_style(attrs)
    }

    fn find_align_attribute(&self, attrs: &Ref<Vec<Attribute>>) -> Option<Option<TableAlignment>> {
        for attr in attrs.iter() {
            if attr.name.local.as_ref() == "align" {
                let value = attr.value.to_string().to_lowercase();
                return Some(match value.as_str() {
                    "left" => Some(TableAlignment::Left),
                    "center" => Some(TableAlignment::Center),
                    "right" => Some(TableAlignment::Right),
                    _ => None,
                });
            }
        }
        None
    }

    fn find_text_align_in_style(&self, attrs: &Ref<Vec<Attribute>>) -> Option<TableAlignment> {
        for attr in attrs.iter() {
            if attr.name.local.as_ref() == "style"
                && let Some(align) = parse_text_align_from_style(&attr.value)
            {
                return Some(align);
            }
        }
        None
    }

    fn append_gfm_table_cell(
        &self,
        output: &mut String,
        cell: &str,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut chars = cell.chars().peekable();
        while let Some(ch) = chars.next() {
            match ch {
                '\r' => {
                    if chars.peek() == Some(&'\n') {
                        chars.next();
                    }
                    append_str_with_context(output, "<br>", ctx)?;
                }
                '\n' => append_str_with_context(output, "<br>", ctx)?,
                '|' => append_str_with_context(output, "\\|", ctx)?,
                _ => append_char_with_context(output, ch, ctx)?,
            }
        }
        Ok(())
    }

    /// Render a normalized GitHub-Flavored Markdown table.
    ///
    /// Column count is widened to the maximum width across headers and rows so
    /// ragged HTML input still produces a rectangular Markdown table.
    pub(super) fn write_gfm_table(
        &self,
        output: &mut String,
        headers: &[String],
        alignments: &[TableAlignment],
        rows: &[Vec<String>],
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        let max_cols = headers
            .len()
            .max(rows.iter().map(Vec::len).max().unwrap_or(0));

        append_char_with_context(output, '|', &mut ctx)?;
        for i in 0..max_cols {
            append_char_with_context(output, ' ', &mut ctx)?;
            let header = headers.get(i).map(String::as_str).unwrap_or("");
            self.append_gfm_table_cell(output, header, &mut ctx)?;
            append_str_with_context(output, " |", &mut ctx)?;
        }
        append_char_with_context(output, '\n', &mut ctx)?;

        append_char_with_context(output, '|', &mut ctx)?;
        for i in 0..max_cols {
            append_char_with_context(output, ' ', &mut ctx)?;
            match alignments.get(i).unwrap_or(&TableAlignment::Left) {
                TableAlignment::Left => append_str_with_context(output, "---", &mut ctx)?,
                TableAlignment::Center => append_str_with_context(output, ":---:", &mut ctx)?,
                TableAlignment::Right => append_str_with_context(output, "---:", &mut ctx)?,
            }
            append_str_with_context(output, " |", &mut ctx)?;
        }
        append_char_with_context(output, '\n', &mut ctx)?;

        for row in rows {
            append_char_with_context(output, '|', &mut ctx)?;
            for i in 0..max_cols {
                append_char_with_context(output, ' ', &mut ctx)?;
                if let Some(cell) = row.get(i) {
                    self.append_gfm_table_cell(output, cell, &mut ctx)?;
                }
                append_str_with_context(output, " |", &mut ctx)?;
            }
            append_char_with_context(output, '\n', &mut ctx)?;
        }

        Ok(())
    }
}

fn parse_text_align_from_style(style: &str) -> Option<TableAlignment> {
    for declaration in style.split(';') {
        let mut parts = declaration.splitn(2, ':');
        let key = parts
            .next()
            .map(str::trim)
            .unwrap_or_default()
            .to_lowercase();
        let value = parts
            .next()
            .map(str::trim)
            .unwrap_or_default()
            .to_lowercase();

        if key == "text-align" {
            return match value.as_str() {
                "center" => Some(TableAlignment::Center),
                "right" => Some(TableAlignment::Right),
                "left" => Some(TableAlignment::Left),
                _ => None,
            };
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::super::{
        ConversionContext, ConversionOptions, MarkdownConverter, MarkdownFlavor, TableAlignment,
    };
    use crate::error::ConversionError;
    use crate::parser::parse_html;
    use std::time::Duration;

    #[test]
    fn table_output_is_byte_identical_with_working_set_accounting() {
        let html = "<table><thead><tr><th>Header</th><th align=\"right\">Count</th></tr></thead><tbody><tr><td>Item</td><td>2</td></tr></tbody></table>";
        let dom = parse_html(html.as_bytes()).expect("test HTML should parse");
        let converter = MarkdownConverter::with_options(ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..ConversionOptions::default()
        });
        let expected = "| Header | Count |\n| --- | ---: |\n| Item | 2 |\n";

        assert_eq!(
            converter.convert(&dom).expect("conversion should succeed"),
            expected
        );

        let mut context = ConversionContext::with_output_budget(Duration::ZERO, 4096);
        let with_budget = converter
            .convert_with_context(&dom, &mut context)
            .expect("conversion should fit the budget");
        assert_eq!(with_budget, expected);
        assert_eq!(context.working_set_bytes, 0);
    }

    #[test]
    fn table_vec_growth_replaces_old_capacity_charge() {
        let element_size = std::mem::size_of::<String>();
        let mut expected_values = Vec::<String>::new();
        let mut expected_capacities = Vec::new();
        for _ in 0..10 {
            if expected_values.len() + 1 > expected_values.capacity() {
                let requested_capacity = expected_values.capacity().max(1) * 2;
                expected_values
                    .try_reserve_exact(requested_capacity - expected_values.len())
                    .expect("test capacity should be reservable");
            }
            expected_capacities.push(expected_values.capacity());
            expected_values.push(String::new());
        }
        let final_capacity = *expected_capacities
            .last()
            .expect("capacity probe should have entries");
        let initial_capacity = *expected_capacities
            .first()
            .expect("capacity probe should have an initial allocation");
        assert!(final_capacity > initial_capacity);
        let mut values = Vec::<String>::new();
        let mut context =
            ConversionContext::with_output_budget(Duration::ZERO, element_size * final_capacity);
        let mut table_scratch = 0;

        {
            let mut context_slot = Some(&mut context);
            for expected_capacity in expected_capacities {
                MarkdownConverter::charge_vec_growth(
                    &mut values,
                    &mut context_slot,
                    &mut table_scratch,
                )
                .expect("explicit table capacity should fit the budget");
                assert_eq!(values.capacity(), expected_capacity);
                assert_eq!(
                    table_scratch,
                    values.capacity() * std::mem::size_of::<String>()
                );
                values.push(String::new());
            }
        }

        assert_eq!(context.working_set_bytes, element_size * final_capacity);
    }

    #[test]
    fn table_vec_growth_to_respects_required_length_boundary() {
        let element_size = std::mem::size_of::<TableAlignment>();
        let mut expected_values = Vec::<TableAlignment>::new();
        expected_values
            .try_reserve_exact(5)
            .expect("test capacity should be reservable");
        let expected_capacity = expected_values.capacity();
        let mut values = Vec::<TableAlignment>::new();
        let mut context =
            ConversionContext::with_output_budget(Duration::ZERO, element_size * expected_capacity);
        let mut table_scratch = 0;

        {
            let mut context_slot = Some(&mut context);
            MarkdownConverter::charge_vec_growth_to(
                &mut values,
                5,
                &mut context_slot,
                &mut table_scratch,
            )
            .expect("required table capacity should fit exactly");
        }

        assert!(values.capacity() >= 5);
        assert_eq!(
            table_scratch,
            values.capacity() * std::mem::size_of::<TableAlignment>()
        );
        assert_eq!(values.capacity(), expected_capacity);
        assert_eq!(table_scratch, element_size * expected_capacity);

        assert_eq!(context.working_set_bytes, element_size * expected_capacity);
    }

    #[test]
    fn many_empty_table_cells_fail_and_release_working_set() {
        let mut html = String::from(
            "<table><colgroup><col span=\"64\" align=\"center\"></colgroup><thead><tr>",
        );
        for _ in 0..64 {
            html.push_str("<th></th>");
        }
        html.push_str("</tr></thead><tbody>");
        for _ in 0..64 {
            html.push_str("<tr>");
            for _ in 0..64 {
                html.push_str("<td></td>");
            }
            html.push_str("</tr>");
        }
        html.push_str("</tbody></table>");

        let dom = parse_html(html.as_bytes()).expect("test HTML should parse");
        let converter = MarkdownConverter::with_options(ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..ConversionOptions::default()
        });
        let mut context = ConversionContext::with_output_budget(Duration::ZERO, 32 * 1024);

        let result = converter.convert_with_context(&dom, &mut context);

        assert!(
            matches!(result, Err(ConversionError::MemoryLimit(_))),
            "large empty-cell table must fail with MemoryLimit, got: {result:?}"
        );
        assert_eq!(context.working_set_bytes, 0);
    }
}
