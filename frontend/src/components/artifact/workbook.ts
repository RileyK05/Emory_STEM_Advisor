/** Spreadsheet address (`A1`, `AB12`) for a 1-based row and column. */
export function address(row: number, column: number) {
  let n = column;
  let letters = '';
  while (n > 0) {
    const x = (n - 1) % 26;
    letters = String.fromCharCode(65 + x) + letters;
    n = Math.floor((n - 1) / 26);
  }
  return letters + row;
}
