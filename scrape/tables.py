"""Spreadsheets and text files as plain text for the AI (stage I3: paste or drop anything). CSV / TSV / text are
decoded; XLSX is read with the standard library (it is a zip of XML): every sheet's cells as tab-separated rows."""

import csv
import io
import re
import zipfile
from xml.etree import ElementTree

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
MAX_CHARS = 60000
MAX_ROWS = 2000


def is_table(name, mime):
    name = (name or '').lower()
    return mime in ('text/csv', 'text/plain', 'text/tab-separated-values', XLSX) or name.endswith(
        ('.csv', '.tsv', '.txt', '.xlsx'))


def _decode(data):
    for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', 'replace')


def _col(ref):
    letters = re.match(r'[A-Z]+', ref or 'A').group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_rows(data):
    """{sheet name: [[cell text, …], …]}. Raises ValueError for a file that isn't a readable XLSX."""
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError('Not a readable spreadsheet.') from exc
    names = book.namelist()
    shared = []
    if 'xl/sharedStrings.xml' in names:
        root = ElementTree.fromstring(book.read('xl/sharedStrings.xml'))
        shared = [''.join(t.text or '' for t in si.iter(f'{{{NS["m"]}}}t')) for si in root.findall('m:si', NS)]
    sheets = {}
    titles = {}
    if 'xl/workbook.xml' in names:
        wb = ElementTree.fromstring(book.read('xl/workbook.xml'))
        for i, sheet in enumerate(wb.iter(f'{{{NS["m"]}}}sheet'), 1):
            titles[f'xl/worksheets/sheet{i}.xml'] = sheet.get('name') or f'Sheet {i}'
    for path in sorted(n for n in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n)):
        root = ElementTree.fromstring(book.read(path))
        rows = []
        for row in root.iter(f'{{{NS["m"]}}}row'):
            cells = {}
            for c in row.findall('m:c', NS):
                kind, value = c.get('t'), c.find('m:v', NS)
                if kind == 'inlineStr':
                    text = ''.join(t.text or '' for t in c.iter(f'{{{NS["m"]}}}t'))
                elif value is None:
                    continue
                elif kind == 's':
                    text = shared[int(value.text)] if value.text and value.text.isdigit() and int(value.text) < len(shared) else ''
                else:
                    text = value.text or ''
                cells[_col(c.get('r'))] = text.strip()
            if cells:
                rows.append([cells.get(i, '') for i in range(max(cells) + 1)])
            if len(rows) >= MAX_ROWS:
                break
        sheets[titles.get(path, path.rsplit('/', 1)[-1])] = rows
    return sheets


def to_text(name, mime, data):
    """The file as text for the AI, or None when it isn't a table / text file."""
    if not is_table(name, mime):
        return None
    if mime == XLSX or (name or '').lower().endswith('.xlsx'):
        parts = []
        for title, rows in xlsx_rows(data).items():
            parts.append(f'Sheet: {title}\n' + '\n'.join('\t'.join(r) for r in rows))
        text = '\n\n'.join(parts)
    else:
        text = _decode(data)
        if (name or '').lower().endswith(('.csv', '.tsv')) or mime in ('text/csv', 'text/tab-separated-values'):
            dialect = csv.excel_tab if '\t' in text[:2000] and ',' not in text[:200] else csv.excel
            rows = list(csv.reader(io.StringIO(text), dialect))[:MAX_ROWS]
            text = '\n'.join('\t'.join(cell.strip() for cell in row) for row in rows)
    return text[:MAX_CHARS]
