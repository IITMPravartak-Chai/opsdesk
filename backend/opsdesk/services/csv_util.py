import csv
import io


def sanitize_csv_cell(val):
    """
    Prevent Spreadsheet Formula Injection (CSV Injection / CWE-1236).
    If a cell value starts with =, +, -, @, \\t, or \\r (even if preceded by leading whitespace
    or control characters), prepend a single quote (') so spreadsheet applications treat it
    as literal text rather than executing formulas, external commands, or DDE exploits.
    Note: Prepending ' converts legitimate negative numbers to literal text in spreadsheet cells,
    which is standard CWE-1236 protection to neutralize formula injection.
    """
    if val is None:
        return ""
    s = str(val)
    if s and s[0] in ("\t", "\r"):
        return f"'{s}"
    space_stripped = s.lstrip(" ")
    if space_stripped and space_stripped[0] in ("=", "+", "-", "@", "\t", "\r", "|", "%"):
        return f"'{s}"
    all_stripped = s.lstrip(" \t\r\n\v\f\xa0")
    if all_stripped and all_stripped[0] in ("=", "+", "-", "@", "|", "%"):
        return f"'{s}"
    return s


def generate_csv(headers, rows):
    """
    RFC 4180 compliant CSV generator with UTF-8 BOM for proper spreadsheet display
    and cell sanitization to prevent formula injection.
    """
    buf = io.StringIO()
    # Write UTF-8 BOM so Excel opens non-ASCII characters cleanly
    buf.write("\ufeff")
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow([sanitize_csv_cell(h) for h in headers])
    for row in rows:
        writer.writerow([sanitize_csv_cell(cell) for cell in row])
    return buf.getvalue().encode("utf-8")
