"""Exercise repo DOCX/XLSX exporters in fresh hidden Word and Excel instances.

This controlled manual QA uses only synthetic sample values, isolated output
directories, local Office COM automation, and no external links or macros.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_ROOT = (REPO_ROOT / "data" / "manual-office-qa").resolve()
EVIDENCE_PATH = REPO_ROOT / "docs" / "evidence" / "office-export-native-qa-2026-10-08.json"
COM_SCRIPT = Path(__file__).with_name("office_export_qa_com.ps1")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported JSON value {type(value).__name__}")


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=_jsonable,
                               allow_nan=False) + "\n", encoding="utf-8")


def _block(id_: str, cls, **kwargs):
    return cls(id=id_, **kwargs)


def _make_specs():
    from app.artifacts.models import (
        DocumentSpec,
        HeadingBlock,
        ListBlock,
        ParagraphBlock,
        TableBlock,
        WorkbookCell,
        WorkbookSheet,
        WorkbookSpec,
    )

    paragraphs = [
        ParagraphBlock(
            id=f"body-{index:02d}",
            text=(
                f"Sample paragraph {index:02d} checks native pagination and line wrapping. "
                "The exporter must keep the full sentence, punctuation, and adjacent content "
                "inside the intended paragraph without joining it to a neighboring block. "
                "This deliberately repeated public test text provides enough body content "
                "to exercise page breaks while keeping every assertion deterministic."
            ),
        )
        for index in range(1, 19)
    ]
    document = DocumentSpec(
        title=("Quarterly STEM Program Export QA Review for Multiline Notes, Priorities, "
               "Budget Values, Tables, Lists, Formula Safety, Typed Dates, and Cross-Sheet "
               "Calculation Results"),
        blocks=[
            _block("section", HeadingBlock, level=1, text="Controlled sample content"),
            ParagraphBlock(
                id="literal-marker-and-currency",
                text="#1 Priority: keep this literal marker as text. The sample budget is $1,234.50.",
            ),
            ParagraphBlock(
                id="hard-line-break",
                text="Line one stays in the same paragraph.\nLine two follows a hard line break.",
            ),
            ListBlock(id="ordered-list", ordered=True,
                      items=["First ordered sample step", "Second ordered sample step",
                             "Third ordered sample step"]),
            ListBlock(id="bullet-list", ordered=False,
                      items=["First bullet sample", "Second bullet sample", "Third bullet sample"]),
            TableBlock(
                id="sample-table",
                headers=["Item", "Amount", "Note"],
                rows=[
                    ["Materials", "$1,234.50", "Currency remains literal text"],
                    ["Lab kits", "$250.00", "Second sample row"],
                    ["Travel", "$98.75", "Third sample row"],
                ],
            ),
            *paragraphs,
        ],
    )

    def c(id_, row, column, *, value=None, formula=None, number_format=None):
        style = {"number_format": number_format} if number_format else {}
        return WorkbookCell(id=id_, row=row, column=column, value=value,
                            formula=formula, style=style)

    workbook = WorkbookSpec(
        title="Native Office Export QA Workbook",
        sheets=[
            WorkbookSheet(
                id="inputs", name="Inputs", freeze_panes="A2",
                column_widths={"A": 26, "B": 22},
                cells=[
                    c("input-heading-a", 1, 1, value="Input"),
                    c("input-heading-b", 1, 2, value="Value"),
                    c("unit-cost", 2, 1, value="Unit cost"),
                    c("unit-cost-value", 2, 2, value=12.5, number_format='"$"#,##0.00'),
                    c("units", 3, 1, value="Units"),
                    c("units-value", 3, 2, value=4, number_format="0"),
                    c("date-label", 4, 1, value="Effective date"),
                    c("date-value", 4, 2, value=date(2026, 10, 8), number_format="yyyy-mm-dd"),
                    c("approved-label", 5, 1, value="Approved"),
                    c("approved-value", 5, 2, value=True),
                    c("literal-label", 6, 1, value="Literal equals text"),
                    c("literal-formula-text", 6, 2, value="=1+1"),
                    c("multiline-label", 7, 1, value="Multiline note"),
                    c("multiline-text", 7, 2, value="Line one\nLine two"),
                    c("subtotal-label", 8, 1, value="Local formula subtotal"),
                    c("subtotal-formula", 8, 2, formula="=B2*B3",
                      number_format='"$"#,##0.00'),
                ],
            ),
            WorkbookSheet(
                id="summary", name="Summary", freeze_panes="A2",
                column_widths={"A": 30, "B": 22},
                cells=[
                    c("summary-heading-a", 1, 1, value="Calculation"),
                    c("summary-heading-b", 1, 2, value="Result"),
                    c("cross-sheet-label", 2, 1, value="Cross-sheet subtotal"),
                    c("cross-sheet-formula", 2, 2, formula="=Inputs!B8",
                      number_format='"$"#,##0.00'),
                    c("local-dependent-label", 3, 1, value="Local dependent formula"),
                    c("local-dependent-formula", 3, 2, formula="=B2+5",
                      number_format='"$"#,##0.00'),
                ],
            ),
        ],
    )
    return document, workbook


def _docx_import_summary(data: bytes) -> dict:
    from app.artifacts.docx import import_docx

    result = import_docx(data)
    types = [block.type for block in result.spec.blocks]
    paragraphs = [block.text for block in result.spec.blocks if block.type == "paragraph"]
    lists = [block for block in result.spec.blocks if block.type == "list"]
    tables = [block for block in result.spec.blocks if block.type == "table"]
    assertions = {
        "title_preserved": result.spec.title.startswith("Quarterly STEM Program Export QA Review"),
        "long_title_preserved": len(result.spec.title) > 120,
        "literal_priority_and_currency_preserved": any(
            "#1 Priority" in text and "$1,234.50" in text for text in paragraphs
        ),
        "multiline_paragraph_preserved": any("Line one" in text and "Line two" in text
                                               for text in paragraphs),
        "ordered_and_bullet_lists_preserved": (
            any(item.ordered for item in lists) and any(not item.ordered for item in lists)
        ),
        "table_preserved": bool(tables and tables[0].headers == ["**Item**", "**Amount**", "**Note**"]
                                 and len(tables[0].rows) == 3),
    }
    return {"accepted": True, "original_sha256_matches": result.original_sha256 == sha256(data),
            "title": result.spec.title, "block_types": types,
            "table_count": len(tables), "assertions": assertions,
            "all_assertions_pass": all(assertions.values())}


def _xlsx_import_summary(data: bytes, spec) -> dict:
    from app.artifacts.xlsx import import_xlsx

    result = import_xlsx(data)
    formula_cells = [cell.formula for sheet in result.spec.sheets for cell in sheet.cells
                     if cell.formula]
    cells = {cell.id: cell for sheet in result.spec.sheets for cell in sheet.cells}
    assertions = {
        "two_sheets": [sheet.name for sheet in result.spec.sheets] == ["Inputs", "Summary"],
        "local_and_cross_sheet_formulas_preserved": (
            "=B2*B3" in formula_cells and "=Inputs!B8" in formula_cells and "=B2+5" in formula_cells
        ),
        "typed_numeric_date_boolean_preserved": (
            isinstance(cells["cell-001-2-2"].value, (int, float))
            and cells["cell-001-4-2"].value == date(2026, 10, 8)
            and cells["cell-001-5-2"].value is True
        ),
        "multiline_newline_wrap_and_minimum_height_preserved": (
            cells["cell-001-7-2"].value == "Line one\nLine two"
            and cells["cell-001-7-2"].style.wrap_text is True
            and result.spec.sheets[0].row_heights.get(7, 0) >= 30
        ),
        "formula_like_text_preserved_as_literal": cells["cell-001-6-2"].value == "=1+1"
        and cells["cell-001-6-2"].formula is None,
    }
    return {"accepted": True, "original_sha256_matches": result.original_sha256 == sha256(data),
            "sheet_names": [sheet.name for sheet in result.spec.sheets],
            "formula_expressions": formula_cells,
            "calculated_values_are_not_in_workbook_spec": all(
                cell.formula is None or cell.value is None
                for sheet in result.spec.sheets for cell in sheet.cells
            ),
            "assertions": assertions, "all_assertions_pass": all(assertions.values()),
            "workbook_spec_sheets": len(spec.sheets)}


class _NativeExcelResultRunner:
    """Controlled evidence adapter; never installed as an application runner."""

    def __init__(self, recalculated: bytes):
        self.recalculated = recalculated

    def recalculate_xlsx(self, xlsx_bytes: bytes, *, timeout_seconds: int) -> bytes:
        if not xlsx_bytes or timeout_seconds != 60:
            raise ValueError("unexpected validator fixture request")
        return self.recalculated


def run() -> dict:
    from app.artifacts.docx import export_docx
    from app.artifacts.safety import inspect_office_archive
    from app.artifacts.xlsx import export_xlsx, recalculate_xlsx

    ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ARTIFACTS_ROOT / f"{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(exist_ok=False)
    for child in ("rendered", "pdf", "native"):
        (run_dir / child).mkdir()

    document_spec, workbook_spec = _make_specs()
    docx_bytes = export_docx(document_spec)
    xlsx_bytes = export_xlsx(workbook_spec)
    docx_path = run_dir / "repo-export.docx"
    xlsx_path = run_dir / "repo-export.xlsx"
    native_xlsx_path = run_dir / "native" / "excel-resaved.xlsx"
    docx_path.write_bytes(docx_bytes)
    xlsx_path.write_bytes(xlsx_bytes)
    (run_dir / "docx-spec.json").write_text(
        json.dumps(document_spec.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "xlsx-spec.json").write_text(
        json.dumps(workbook_spec.model_dump(mode="json"), indent=2, ensure_ascii=False,
                   default=_jsonable) + "\n", encoding="utf-8",
    )

    docx_import = _docx_import_summary(docx_bytes)
    xlsx_import = _xlsx_import_summary(xlsx_bytes, workbook_spec)
    inspect_office_archive(docx_bytes, kind="docx")
    xlsx_archive = inspect_office_archive(xlsx_bytes, kind="xlsx")
    xlsx_names = xlsx_archive.names
    safe_container = not any("vbaProject.bin" in n or n.startswith("xl/externalLinks/")
                             for n in xlsx_names)

    docx_pdf = run_dir / "pdf" / "word-render.pdf"
    xlsx_pdf = run_dir / "pdf" / "excel-render.pdf"
    com_report_path = run_dir / "native" / "com-report.json"
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError("Windows PowerShell was not found for native COM QA")
    command = [
        powershell, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(COM_SCRIPT), str(docx_path), str(xlsx_path), str(docx_pdf), str(xlsx_pdf),
        str(native_xlsx_path), str(com_report_path),
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT / "backend", capture_output=True,
                               text=True, timeout=120, check=False)
    if not com_report_path.is_file():
        raise RuntimeError(
            f"Office COM helper produced no report (exit {completed.returncode}): "
            f"{completed.stdout[-1500:]} {completed.stderr[-1500:]}"
        )
    com_report = json.loads(com_report_path.read_text(encoding="utf-8"))

    renderer = shutil.which("pdftoppm")
    if renderer is None:
        raise RuntimeError("pdftoppm is required to rasterize every native PDF page")
    pdf_pages = {}
    for label, pdf in (("word", docx_pdf), ("excel", xlsx_pdf)):
        if not pdf.is_file() or pdf.stat().st_size == 0:
            pdf_pages[label] = {"rendered": False, "reason": "native PDF is missing or empty"}
            continue
        prefix = run_dir / "rendered" / f"{label}-page"
        result = subprocess.run([renderer, "-png", "-r", "144", str(pdf), str(prefix)],
                                capture_output=True, text=True, timeout=120, check=False)
        images = sorted((run_dir / "rendered").glob(f"{label}-page-*.png"))
        page_count = len(PdfReader(str(pdf)).pages)
        pdf_pages[label] = {
            "rendered": result.returncode == 0 and len(images) == page_count,
            "page_count": page_count, "png_count": len(images),
            "images": [str(path.relative_to(REPO_ROOT)) for path in images],
            "pdf_sha256": sha256(pdf.read_bytes()),
            "renderer_returncode": result.returncode,
            "renderer_stderr": result.stderr[-1000:],
        }

    native_xlsx = None
    validator = {"status": "not_run", "message": "native workbook was not saved"}
    if native_xlsx_path.is_file():
        native_bytes = native_xlsx_path.read_bytes()
        try:
            native_import = _xlsx_import_summary(native_bytes, workbook_spec)
        except Exception as exc:  # noqa: BLE001 - report the repo importer boundary
            native_import = {"accepted": False, "error_type": type(exc).__name__,
                             "error": str(exc),
                             "limitation": "native Excel output uses a feature the repo importer does not preserve"}
        native_xlsx = {"sha256": sha256(native_bytes), "size_bytes": len(native_bytes),
                       "repo_import": native_import}
        validation = recalculate_xlsx(
            xlsx_bytes, workbook_spec, runner=_NativeExcelResultRunner(native_bytes),
        )
        validator = {"status": validation.status, "message": validation.message,
                     "returned_bytes_match_native_result": validation.xlsx_bytes == native_bytes}

    report = {
        "status": "manual_native_export_qa",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "platform": platform.platform(), "python": platform.python_version(),
        "run_directory": str(run_dir.relative_to(REPO_ROOT)),
        "source": "repository app.artifacts exporters; no third-party authoring",
        "fixture_scope": "controlled synthetic QA values; no user workbooks or documents opened",
        "exporters": {
            "docx_py_sha256": sha256((REPO_ROOT / "backend/app/artifacts/docx.py").read_bytes()),
            "xlsx_py_sha256": sha256((REPO_ROOT / "backend/app/artifacts/xlsx.py").read_bytes()),
            "docx_sha256": sha256(docx_bytes), "xlsx_sha256": sha256(xlsx_bytes),
            "docx_bytes": len(docx_bytes), "xlsx_bytes": len(xlsx_bytes),
        },
        "container_safety": {"xlsx_has_no_macros_or_external_links": safe_container,
                             "formula_sources": ["=B2*B3", "=Inputs!B8", "=B2+5"]},
        "repository_importers": {"docx_export_reimport": docx_import,
                                 "xlsx_export_reimport": xlsx_import},
        "native_office": com_report,
        "native_saved_xlsx": native_xlsx,
        "app_recalculation_validator_with_native_result_adapter": validator,
        "pdf_pages": pdf_pages,
        "visual_review": {"status": "pending_page_image_inspection",
                           "all_rendered_pngs_must_be_opened_and_reviewed": True},
        "limitations": [
            "This validates a controlled fixture in local desktop Word and Excel only.",
            "COM rendering is native Windows Office evidence, not production isolated Linux Office proof.",
            "The repository importer preserves only its explicitly supported content subset.",
            "WorkbookSpec stores formula expressions, not calculated formula values; the original exported XLSX has no calculated cache until an engine recalculates it.",
            "The native Excel result adapter is used only to exercise the app validator in this QA run; it is not configured as a production runner.",
        ],
    }
    _write_json(EVIDENCE_PATH, report)
    (run_dir / "com-stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "com-stderr.txt").write_text(completed.stderr, encoding="utf-8")
    return report


def main() -> None:
    report = run()
    print(json.dumps({
        "status": report["status"], "evidence": str(EVIDENCE_PATH),
        "run_directory": report["run_directory"], "com_status": report["native_office"]["status"],
        "word_pages": report["pdf_pages"].get("word", {}).get("page_count"),
        "excel_pages": report["pdf_pages"].get("excel", {}).get("page_count"),
        "app_formula_validator": report["app_recalculation_validator_with_native_result_adapter"]["status"],
    }, indent=2))


if __name__ == "__main__":
    main()
