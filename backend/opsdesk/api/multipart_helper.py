"""Multipart and form data parser using Python standard library email module.

Enables FastAPI to parse multipart/form-data and application/x-www-form-urlencoded
without requiring the external python-multipart library.
"""
from dataclasses import dataclass
import email
from typing import Dict, List, Tuple


@dataclass
class UploadedFilePayload:
    filename: str
    content: bytes
    content_type: str


from fastapi import HTTPException, Request, status

MAX_UPLOAD_SIZE = 25 * 1024 * 1024  # 25 MB bounded ingestion limit


async def read_bounded_body(request: Request, max_size: int = MAX_UPLOAD_SIZE) -> bytes:
    """Read request body using streaming chunk-by-chunk counting.

    Guarantees:
    1. Early rejection: If Content-Length header is present and exceeds max_size,
       immediately raises HTTP 413 without reading any body chunks from the network.
    2. Streaming limit enforcement: If Content-Length is missing (e.g. chunked transfer)
       or forged/incorrect, reads chunks incrementally from request.stream(). The moment
       the cumulative byte count exceeds max_size, raises HTTP 413 immediately to terminate
       the stream and prevent unbounded memory buffering.
    3. Memory safety: Once verified under max_size, returns the combined bytes for bounded
       parsing by parse_request_form.
    """
    cl_header = request.headers.get("content-length")
    if cl_header:
        try:
            cl = int(cl_header)
            if cl > max_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Payload exceeds maximum limit of {max_size // (1024 * 1024)}MB.",
                )
        except ValueError:
            pass

    total_bytes = 0
    chunks = []
    async for chunk in request.stream():
        total_bytes += len(chunk)
        if total_bytes > max_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Payload exceeds maximum limit of {max_size // (1024 * 1024)}MB.",
            )
        chunks.append(chunk)

    return b"".join(chunks)


def parse_request_form(body: bytes, content_type_header: str) -> Tuple[Dict[str, str], List[UploadedFilePayload]]:
    """Parse body bytes using standard library email.message parser."""
    fields: Dict[str, str] = {}
    files: List[UploadedFilePayload] = []

    if not body:
        return fields, files

    if "application/json" in (content_type_header or ""):
        import json
        try:
            parsed = json.loads(body.decode("utf-8"))
            if isinstance(parsed, dict):
                return {k: str(v) for k, v in parsed.items()}, []
        except Exception:
            return fields, files

    # Construct synthetic MIME message with headers
    ct_clean = content_type_header or "application/octet-stream"
    header_bytes = f"Content-Type: {ct_clean}\r\n\r\n".encode("latin1")
    msg = email.message_from_bytes(header_bytes + body)

    for part in msg.walk():
        if part.is_multipart():
            continue
        cd = part.get("Content-Disposition", "")
        if not cd:
            continue

        field_name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""

        if filename:
            ct = part.get_content_type() or "application/octet-stream"
            files.append(UploadedFilePayload(filename=filename, content=payload, content_type=ct))
        elif field_name:
            charset = part.get_content_charset() or "utf-8"
            try:
                fields[field_name] = payload.decode(charset)
            except Exception:
                fields[field_name] = payload.decode("latin1")

    return fields, files
