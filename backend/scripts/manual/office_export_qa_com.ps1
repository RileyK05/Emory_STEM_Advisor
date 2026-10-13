param(
    [Parameter(Mandatory=$true)][string]$DocxPath,
    [Parameter(Mandatory=$true)][string]$XlsxPath,
    [Parameter(Mandatory=$true)][string]$DocxPdfPath,
    [Parameter(Mandatory=$true)][string]$XlsxPdfPath,
    [Parameter(Mandatory=$true)][string]$NativeWorkbookPath,
    [Parameter(Mandatory=$true)][string]$ReportPath
)

$ErrorActionPreference = 'Stop'
function Get-NewAppProcessId([string]$Name, [int[]]$ExistingIds) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $newProcess = Get-Process -Name $Name -ErrorAction SilentlyContinue |
            Where-Object { $ExistingIds -notcontains $_.Id } | Select-Object -First 1
        if ($null -ne $newProcess) { return [int]$newProcess.Id }
        Start-Sleep -Milliseconds 250
    }
    return $null
}

function Release-ComObject($Value) {
    if ($null -ne $Value -and [Runtime.InteropServices.Marshal]::IsComObject($Value)) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Value)
    }
}

function Get-RangeSnapshot($Sheet, [string]$Address) {
    $cell = $Sheet.Range($Address)
    try {
        $raw = $cell.Value2
        $runtimeType = if ($null -eq $raw) { $null } else { $raw.GetType().FullName }
        return @{
            address = $Address
            value2 = $raw
            displayed_text = [string]$cell.Text
            formula = [string]$cell.Formula
            has_formula = [bool]$cell.HasFormula
            number_format = [string]$cell.NumberFormat
            wrap_text = [bool]$cell.WrapText
            row_height = [double]$cell.RowHeight
            value_runtime_type = $runtimeType
        }
    }
    finally { Release-ComObject $cell }
}

$report = [ordered]@{
    status = 'started'
    word = $null
    excel = $null
    errors = @()
}
$word = $null
$wordDoc = $null
$excel = $null
$workbook = $null
$reopened = $null
$inputs = $null
$summary = $null
$ownedWord = $false
$ownedExcel = $false

try {
    $existingWordPids = @((Get-Process WINWORD -ErrorAction SilentlyContinue | ForEach-Object Id))
    $word = New-Object -ComObject Word.Application
    $wordPid = Get-NewAppProcessId 'WINWORD' $existingWordPids
    $ownedWord = $null -ne $wordPid -and $existingWordPids -notcontains $wordPid
    $report.word = @{ pid = $wordPid; version = [string]$word.Version; fresh_process = $ownedWord }
    if (-not $ownedWord) { throw 'Word activation returned a pre-existing process; no document was opened.' }
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $word.Options.UpdateLinksAtOpen = $false
    $word.Options.ConfirmConversions = $false
    $wordDoc = $word.Documents.Open($DocxPath, $false, $true, $false)
    $wordDoc.UpdateStylesOnOpen = $false
    $wordDoc.ExportAsFixedFormat($DocxPdfPath, 17, $false)
    $report.word.visible = [bool]$word.Visible
    $report.word.automation_security = [int]$word.AutomationSecurity
    $report.word.read_only = [bool]$wordDoc.ReadOnly
    $report.word.page_count = [int]$wordDoc.ComputeStatistics(2)
    $report.word.exported_pdf_bytes = (Get-Item -LiteralPath $DocxPdfPath).Length
}
catch {
    $report.errors += @{ app = 'Word'; type = $_.Exception.GetType().FullName; message = $_.Exception.Message }
}
finally {
    if ($null -ne $wordDoc) { try { $wordDoc.Close(0) } catch {} }
    Release-ComObject $wordDoc
    if ($null -ne $word -and $ownedWord) {
        try { $word.Quit(); $report.word.quit_returned = $true }
        catch { $report.word.quit_error = $_.Exception.Message }
    }
    Release-ComObject $word
    if ($ownedWord -and $null -ne $wordPid) {
        Start-Sleep -Milliseconds 750
        $ownedProcess = Get-Process -Id $wordPid -ErrorAction SilentlyContinue
        if ($null -ne $ownedProcess -and $ownedProcess.ProcessName -eq 'WINWORD') {
            Stop-Process -Id $wordPid -Force
            $report.word.owned_pid_force_closed_after_quit_attempt = $true
        }
        else { $report.word.owned_pid_force_closed_after_quit_attempt = $false }
    }
    $wordDoc = $null; $word = $null
}

try {
    $existingExcelPids = @((Get-Process EXCEL -ErrorAction SilentlyContinue | ForEach-Object Id))
    $excel = New-Object -ComObject Excel.Application
    $excelPid = Get-NewAppProcessId 'EXCEL' $existingExcelPids
    $ownedExcel = $null -ne $excelPid -and $existingExcelPids -notcontains $excelPid
    $report.excel = @{ pid = $excelPid; version = [string]$excel.Version; fresh_process = $ownedExcel }
    if (-not $ownedExcel) { throw 'Excel activation returned a pre-existing process; no workbook was opened.' }
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.EnableEvents = $false
    $excel.AskToUpdateLinks = $false
    $excel.AutomationSecurity = 3
    $workbook = $excel.Workbooks.Open($XlsxPath, 0, $false)
    $report.excel.visible = [bool]$excel.Visible
    $report.excel.automation_security = [int]$excel.AutomationSecurity
    $report.excel.sheet_names = @($workbook.Worksheets | ForEach-Object { $_.Name })
    $inputs = $workbook.Worksheets.Item('Inputs')
    $summary = $workbook.Worksheets.Item('Summary')

    $excel.CalculateFullRebuild()
    $initial = @{
        local_formula = Get-RangeSnapshot $inputs 'B8'
        cross_sheet_formula = Get-RangeSnapshot $summary 'B2'
        dependent_local_formula = Get-RangeSnapshot $summary 'B3'
    }
    $originalInput = [double]$inputs.Range('B2').Value2
    $inputs.Range('B2').Value2 = 20.0
    $excel.CalculateFullRebuild()
    $changed = @{
        local_formula = Get-RangeSnapshot $inputs 'B8'
        cross_sheet_formula = Get-RangeSnapshot $summary 'B2'
        dependent_local_formula = Get-RangeSnapshot $summary 'B3'
    }
    $inputs.Range('B2').Value2 = $originalInput
    $excel.CalculateFullRebuild()
    $restored = @{
        local_formula = Get-RangeSnapshot $inputs 'B8'
        cross_sheet_formula = Get-RangeSnapshot $summary 'B2'
        dependent_local_formula = Get-RangeSnapshot $summary 'B3'
    }
    $typedCells = @{
        numeric_currency = Get-RangeSnapshot $inputs 'B2'
        numeric_units = Get-RangeSnapshot $inputs 'B3'
        date = Get-RangeSnapshot $inputs 'B4'
        boolean = Get-RangeSnapshot $inputs 'B5'
        literal_equals_text = Get-RangeSnapshot $inputs 'B6'
        multiline_text = Get-RangeSnapshot $inputs 'B7'
    }
    $workbook.SaveAs($NativeWorkbookPath, 51)
    $workbook.Close($false)
    Release-ComObject $summary; $summary = $null
    Release-ComObject $inputs; $inputs = $null
    Release-ComObject $workbook; $workbook = $null

    $reopened = $excel.Workbooks.Open($NativeWorkbookPath, 0, $true)
    $reopenInputs = $reopened.Worksheets.Item('Inputs')
    $reopenSummary = $reopened.Worksheets.Item('Summary')
    $excel.CalculateFullRebuild()
    $afterReopen = @{
        local_formula = Get-RangeSnapshot $reopenInputs 'B8'
        cross_sheet_formula = Get-RangeSnapshot $reopenSummary 'B2'
        dependent_local_formula = Get-RangeSnapshot $reopenSummary 'B3'
        date = Get-RangeSnapshot $reopenInputs 'B4'
        boolean = Get-RangeSnapshot $reopenInputs 'B5'
        literal_equals_text = Get-RangeSnapshot $reopenInputs 'B6'
        multiline_text = Get-RangeSnapshot $reopenInputs 'B7'
    }
    foreach ($sheet in @($reopenInputs, $reopenSummary)) {
        $sheet.PageSetup.PrintArea = $sheet.UsedRange.Address()
        $sheet.PageSetup.Zoom = $false
        $sheet.PageSetup.FitToPagesWide = 1
        $sheet.PageSetup.FitToPagesTall = 1
        $sheet.PageSetup.Orientation = 1
        $sheet.PageSetup.PaperSize = 1
        Release-ComObject $sheet
    }
    $reopened.ExportAsFixedFormat(0, $XlsxPdfPath, 0, $true, $false)
    $report.excel.visible = [bool]$excel.Visible
    $report.excel.calculation_mode = 'native application default; CalculateFullRebuild invoked explicitly'
    $report.excel.initial_calculation = $initial
    $report.excel.changed_input_calculation = $changed
    $report.excel.restored_calculation = $restored
    $report.excel.typed_cells = $typedCells
    $report.excel.saved_reopened = $afterReopen
    $report.excel.native_saved_bytes = (Get-Item -LiteralPath $NativeWorkbookPath).Length
    $report.excel.exported_pdf_bytes = (Get-Item -LiteralPath $XlsxPdfPath).Length
}
catch {
    $report.errors += @{ app = 'Excel'; type = $_.Exception.GetType().FullName; message = $_.Exception.Message }
}
finally {
    if ($null -ne $reopened) { try { $reopened.Close($false) } catch {} }
    if ($null -ne $workbook) { try { $workbook.Close($false) } catch {} }
    Release-ComObject $summary; Release-ComObject $inputs
    Release-ComObject $reopened; Release-ComObject $workbook
    if ($null -ne $excel -and $ownedExcel) {
        try { $excel.Quit(); $report.excel.quit_returned = $true }
        catch { $report.excel.quit_error = $_.Exception.Message }
    }
    Release-ComObject $excel
    if ($ownedExcel -and $null -ne $excelPid) {
        Start-Sleep -Milliseconds 750
        $ownedProcess = Get-Process -Id $excelPid -ErrorAction SilentlyContinue
        if ($null -ne $ownedProcess -and $ownedProcess.ProcessName -eq 'EXCEL') {
            Stop-Process -Id $excelPid -Force
            $report.excel.owned_pid_force_closed_after_quit_attempt = $true
        }
        else { $report.excel.owned_pid_force_closed_after_quit_attempt = $false }
    }
}

$report.status = if ($report.errors.Count -eq 0) { 'completed' } else { 'failed' }
$json = ConvertTo-Json -InputObject $report -Depth 30
[IO.File]::WriteAllText($ReportPath, $json, [Text.UTF8Encoding]::new($false))
if ($report.errors.Count -gt 0) { exit 2 }
