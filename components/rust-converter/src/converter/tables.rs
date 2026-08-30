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
            self.extract_colgroup_alignments_from(node, &mut col_alignments);

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
                alignments.push(TableAlignment::Left);
            }

            Self::apply_col_alignments(&mut alignments, &col_alignments);

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
        col_alignments: &mut Vec<Option<TableAlignment>>,
    ) {
        for child in node.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "colgroup"
            {
                self.extract_colgroup_alignments(child, col_alignments);
            }
        }
    }

    fn apply_col_alignments(
        alignments: &mut Vec<TableAlignment>,
        col_alignments: &[Option<TableAlignment>],
    ) {
        for (i, col_align) in col_alignments.iter().enumerate() {
            if let Some(col_align) = col_align {
                if i < alignments.len() {
                    alignments[i] = *col_align;
                } else {
                    alignments.resize(i + 1, TableAlignment::Left);
                    alignments[i] = *col_align;
                }
            }
        }
    }

    /// Extract column alignments from a `<colgroup>` element.
    ///
    /// Each `<col>` child's explicit `align` attribute or
    /// `style="text-align: ..."` is resolved using the same alignment rules
    /// as header cells. Bare columns are preserved as `None`.
    pub(super) fn extract_colgroup_alignments(
        &self,
        colgroup: &Handle,
        col_alignments: &mut Vec<Option<TableAlignment>>,
    ) {
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
                    col_alignments.push(alignment);
                }
            }
        }
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
                    Self::charge_vec_growth(headers, &mut ctx, table_scratch)?;
                    headers.push(cell_output);
                    let attrs_borrowed = attrs.borrow();
                    Self::charge_vec_growth(alignments, &mut ctx, table_scratch)?;
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
                Self::charge_vec_growth(rows, &mut ctx, table_scratch)?;
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
                    Self::charge_vec_growth(cells, &mut ctx, table_scratch)?;
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
        vec: &Vec<T>,
        ctx: &mut Option<&mut ConversionContext>,
        table_scratch: &mut usize,
    ) -> Result<(), ConversionError> {
        if vec.capacity() > vec.len() {
            return Ok(());
        }
        let growth = vec.capacity().max(1).saturating_mul(2);
        if let Some(context) = ctx.as_deref_mut() {
            let next = table_scratch.checked_add(growth).ok_or_else(|| {
                ConversionError::MemoryLimit("table container size overflow".into())
            })?;
            context.reserve_working_set(growth)?;
            *table_scratch = next;
        }
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
