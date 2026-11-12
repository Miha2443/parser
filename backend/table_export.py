"""Bounded, stateless XLSX conversion of an already displayed table."""
from io import BytesIO
import json
import math
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect


MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 256
MAX_CELLS = 1_000_000
MAX_STRING_LENGTH = 32_767
MAX_STRING_BYTES = 12 * 1024 * 1024
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_INVALID_SHEET = re.compile(r"[\\/*?:\[\]]")


def _invalid(message):
    raise HTTPException(422, message)


def _limit(message):
    raise HTTPException(413, message)


def _constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _validate(body):
    try:
        payload = json.loads(body.decode("utf-8"), parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        _invalid("Body must be valid UTF-8 JSON with finite numbers")
    if not isinstance(payload, dict) or set(payload) - {"rows", "sheetName"}:
        _invalid("Expected an object containing rows and optional sheetName")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        _invalid("rows must be an array of arrays")
    if len(rows) > MAX_ROWS:
        _limit(f"Table exceeds {MAX_ROWS} rows")
    title = payload.get("sheetName", "Table")
    if (not isinstance(title, str) or not title.strip() or len(title) > 31
            or _INVALID_SHEET.search(title) or _INVALID_XML.search(title)
            or title.startswith("'") or title.endswith("'")):
        _invalid("sheetName must be a valid Excel worksheet name of 1-31 characters")
    width = 0
    string_bytes = 0
    widths = []
    for row in rows:
        if not isinstance(row, list):
            _invalid("Each row must be an array")
        if len(row) > MAX_COLUMNS:
            _limit(f"Table exceeds {MAX_COLUMNS} columns")
        width = max(width, len(row))
        if len(rows) * width > MAX_CELLS:
            _limit(f"Padded table exceeds {MAX_CELLS} cells")
        widths.extend([10] * (width - len(widths)))
        for column, value in enumerate(row):
            if isinstance(value, str):
                if len(value) > MAX_STRING_LENGTH:
                    _limit(f"A string cell exceeds {MAX_STRING_LENGTH} characters")
                if _INVALID_XML.search(value):
                    _invalid("String cells contain unsupported XML characters")
                string_bytes += len(value.encode("utf-8"))
                if string_bytes > MAX_STRING_BYTES:
                    _limit(f"String cells exceed {MAX_STRING_BYTES} UTF-8 bytes in total")
                display = max((len(line) for line in value.splitlines()), default=0)
                widths[column] = max(widths[column], min(60, display + 2))
            elif value is None or isinstance(value, bool):
                continue
            elif isinstance(value, (int, float)):
                try:
                    finite = math.isfinite(value)
                except OverflowError:
                    finite = False
                if not finite:
                    _invalid("Number cells must be finite and within Excel numeric range")
                widths[column] = max(widths[column], min(24, len(str(value)) + 2))
            else:
                _invalid("Cells must be strings, numbers, booleans, or null")
    return rows, title, width, widths


def _convert(body):
    rows, title, width, widths = _validate(body)
    book = Workbook(write_only=True)
    sheet = book.create_sheet(title)
    sheet.freeze_panes = "A2"
    for column, size in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(column)].width = size
    if rows and width:
        sheet.auto_filter.ref = f"A1:{get_column_letter(width)}{len(rows)}"
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="E8EDF2")
    for index, row in enumerate(rows):
        cells = []
        for value in row + [None] * (width - len(row)):
            cell = WriteOnlyCell(sheet, value=value)
            if isinstance(value, str):
                # Explicit text type prevents =, +, -, @ or whitespace-prefixed input becoming formulas.
                cell.data_type = "s"
            if index == 0:
                cell.font = header_font
                cell.fill = header_fill
            cells.append(cell)
        sheet.append(cells)
    output = BytesIO()
    try:
        book.save(output)
        return output.getvalue()
    finally:
        book.close()


def create_router():
    router = APIRouter(prefix="/api/v1/exports")

    @router.post("/table")
    async def table_export(request: Request):
        length = request.headers.get("content-length")
        if length is not None:
            try:
                size = int(length)
            except ValueError:
                _invalid("Invalid Content-Length")
            if size < 0:
                _invalid("Invalid Content-Length")
            if size > MAX_BODY_BYTES:
                _limit(f"Request body exceeds {MAX_BODY_BYTES} bytes")
        body = bytearray()
        try:
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    _limit(f"Request body exceeds {MAX_BODY_BYTES} bytes")
                body.extend(chunk)
        except ClientDisconnect:
            raise HTTPException(400, "Request body was interrupted") from None
        content = await run_in_threadpool(_convert, body)
        return Response(content, media_type=XLSX_MEDIA_TYPE,
                        headers={"Content-Disposition": 'attachment; filename="table.xlsx"',
                                 "Cache-Control": "no-store"})

    return router
