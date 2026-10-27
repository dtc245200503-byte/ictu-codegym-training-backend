"""Generate a small, dependency-free PDF payment receipt."""

import unicodedata


def _pdf_text(value: str) -> bytes:
    value = value.replace("đ", "d").replace("Đ", "D")
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    escaped = ascii_value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return escaped.encode("ascii")


def build_receipt_pdf(lines: list[str]) -> bytes:
    """Return a valid one-page PDF containing readable ASCII receipt text."""
    commands = [b"BT", b"/F1 16 Tf", b"50 790 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.extend([b"/F1 11 Tf", b"0 -24 Td"])
        commands.extend([b"(" + _pdf_text(line) + b") Tj"])
    commands.append(b"ET")
    stream = b"\n".join(commands)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%ICTU Receipt\n")
    offsets = [0]
    for object_number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(
            f"{object_number} 0 obj\n".encode("ascii")
            + obj
            + b"\nendobj\n"
        )
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)
