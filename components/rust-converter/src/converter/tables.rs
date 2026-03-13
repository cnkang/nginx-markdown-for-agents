use super::*;

impl MarkdownConverter {
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

        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        let mut headers: Vec<String> = Vec::new();
        let mut alignments: Vec<TableAlignment> = Vec::new();
        let mut rows: Vec<Vec<String>> = Vec::new();

        // Table cell extraction now respects the conversion budget via ctx.
        let mut ctx = ctx;
        for child in node.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data {
                match name.local.as_ref() {
                    "thead" => self.extract_table_header(child, ctx.as_deref_mut(), &mut headers, &mut alignments)?,
                    "tbody" => {
                        if headers.is_empty() {
                            let children = child.children.borrow();
                            let first_tr_opt = children.iter().find(|candidate| {
                                matches!(
                                    candidate.data,
                                    NodeData::Element { ref name, .. } if name.local.as_ref() == "tr"
                                )
                            });

                            if let Some(first_tr) = first_tr_opt {
                                self.extract_table_row_as_header(
                                    first_tr,
                                    ctx.as_deref_mut(),
                                    &mut headers,
                                    &mut alignments,
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
                                        self.extract_table_row(tbody_child, ctx.as_deref_mut(), &mut row_cells)?;
                                        rows.push(row_cells);
                                    }
                                }
                            } else {
                                self.extract_table_rows(child, ctx.as_deref_mut(), &mut rows)?;
                            }
                        } else {
                            self.extract_table_rows(child, ctx.as_deref_mut(), &mut rows)?;
                        }
                    }
                    "tr" => {
                        if headers.is_empty() {
                            self.extract_table_row_as_header(child, ctx.as_deref_mut(), &mut headers, &mut alignments)?;
                        } else {
                            let mut row_cells = Vec::new();
                            self.extract_table_row(child, ctx.as_deref_mut(), &mut row_cells)?;
                            rows.push(row_cells);
                        }
                    }
                    _ => {}
                }
            }
        }

        if headers.is_empty() {
            return Ok(());
        }

        while alignments.len() < headers.len() {
            alignments.push(TableAlignment::Left);
        }

        self.write_gfm_table(output, &headers, &alignments, &rows)?;

        if !output.ends_with("\n\n") {
            output.push('\n');
        }

        Ok(())
    }

    pub(super) fn extract_table_header(
        &self,
        thead: &Handle,
        ctx: Option<&mut ConversionContext>,
        headers: &mut Vec<String>,
        alignments: &mut Vec<TableAlignment>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        for child in thead.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "tr"
            {
                self.extract_table_row_as_header(child, ctx.as_deref_mut(), headers, alignments)?;
                break;
            }
        }

        Ok(())
    }

    pub(super) fn extract_table_row_as_header(
        &self,
        tr: &Handle,
        ctx: Option<&mut ConversionContext>,
        headers: &mut Vec<String>,
        alignments: &mut Vec<TableAlignment>,
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
                        self.traverse_node_optional(cell_child, &mut cell_output, 0, ctx.as_deref_mut())?;
                    }

                    headers.push(cell_output.trim().to_string());
                    let attrs_borrowed = attrs.borrow();
                    alignments.push(self.extract_alignment(&attrs_borrowed));
                }
            }
        }

        Ok(())
    }

    pub(super) fn extract_table_rows(
        &self,
        tbody: &Handle,
        ctx: Option<&mut ConversionContext>,
        rows: &mut Vec<Vec<String>>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        for child in tbody.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "tr"
            {
                let mut row_cells = Vec::new();
                self.extract_table_row(child, ctx.as_deref_mut(), &mut row_cells)?;
                rows.push(row_cells);
            }
        }

        Ok(())
    }

    pub(super) fn extract_table_row(
        &self,
        tr: &Handle,
        ctx: Option<&mut ConversionContext>,
        cells: &mut Vec<String>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        for child in tr.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data {
                let tag = name.local.as_ref();
                if tag == "td" || tag == "th" {
                    let mut cell_output = String::new();
                    for cell_child in child.children.borrow().iter() {
                        self.traverse_node_optional(cell_child, &mut cell_output, 0, ctx.as_deref_mut())?;
                    }
                    cells.push(cell_output.trim().to_string());
                }
            }
        }

        Ok(())
    }

    pub(super) fn extract_alignment(&self, attrs: &Ref<Vec<Attribute>>) -> TableAlignment {
        for attr in attrs.iter() {
            if attr.name.local.as_ref() == "align" {
                let value = attr.value.to_string().to_lowercase();
                return match value.as_str() {
                    "left" => TableAlignment::Left,
                    "center" => TableAlignment::Center,
                    "right" => TableAlignment::Right,
                    _ => TableAlignment::Left,
                };
            }
        }

        for attr in attrs.iter() {
            if attr.name.local.as_ref() == "style" {
                let style = attr.value.to_string();
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
                            "center" => TableAlignment::Center,
                            "right" => TableAlignment::Right,
                            "left" => TableAlignment::Left,
                            _ => TableAlignment::Left,
                        };
                    }
                }
            }
        }

        TableAlignment::Left
    }

    fn escape_gfm_table_cell(&self, cell: &str) -> String {
        cell.replace("\r\n", "\n")
            .replace('\r', "\n")
            .replace('\n', "<br>")
            .replace('|', "\\|")
    }

    pub(super) fn write_gfm_table(
        &self,
        output: &mut String,
        headers: &[String],
        alignments: &[TableAlignment],
        rows: &[Vec<String>],
    ) -> Result<(), ConversionError> {
        let max_cols = headers
            .len()
            .max(rows.iter().map(|r| r.len()).max().unwrap_or(0));

        output.push('|');
        for i in 0..max_cols {
            output.push(' ');
            let header = headers.get(i).map(|s| s.as_str()).unwrap_or("");
            output.push_str(&self.escape_gfm_table_cell(header));
            output.push_str(" |");
        }
        output.push('\n');

        output.push('|');
        for i in 0..max_cols {
            output.push(' ');
            match alignments.get(i).unwrap_or(&TableAlignment::Left) {
                TableAlignment::Left => output.push_str("---"),
                TableAlignment::Center => output.push_str(":---:"),
                TableAlignment::Right => output.push_str("---:"),
            }
            output.push_str(" |");
        }
        output.push('\n');

        for row in rows {
            output.push('|');
            for i in 0..max_cols {
                output.push(' ');
                if let Some(cell) = row.get(i) {
                    output.push_str(&self.escape_gfm_table_cell(cell));
                }
                output.push_str(" |");
            }
            output.push('\n');
        }

        Ok(())
    }
}
