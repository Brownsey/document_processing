"""Parse document bytes into source-local text and structured records."""

import io
import json
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value")


def _paragraph_text(node: ET.Element) -> str:
    parts = []
    for element in node.iter():
        if element.tag == W + "t":
            parts.append(element.text or "")
        elif element.tag == W + "tab":
            parts.append("\t")
        elif element.tag in (W + "br", W + "cr"):
            parts.append("\n")
    return "".join(parts).strip()


def read_docx(
    raw: bytes, max_expanded_bytes: int
) -> tuple[list[tuple[str, str]], list[str]]:
    with ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if sum(i.file_size for i in infos) > max_expanded_bytes:
            raise ValueError("Expanded document exceeds size limit")
        for info in infos:
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename:
                raise ValueError("Unsafe archive member")
        xml = archive.read("word/document.xml")
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise ValueError("Unsupported XML declarations")
        root = ET.fromstring(xml)
        body = root.find(W + "body")
        if body is None:
            raise ValueError("Missing document body")
        issues = []
        unsupported = {
            W + tag
            for tag in (
                "drawing",
                "pict",
                "object",
                "altChunk",
                "sdt",
                "ins",
                "del",
                "txbxContent",
                "commentReference",
                "commentRangeStart",
            )
        }
        if any(node.tag in unsupported for node in root.iter()) or any(
            info.filename.startswith(
                (
                    "word/media/",
                    "word/embeddings/",
                    "word/header",
                    "word/footer",
                    "word/footnotes",
                    "word/endnotes",
                    "word/comments",
                )
            )
            for info in infos
        ):
            issues.append("unsupported_embedded_content")
        blocks = []
        paragraph_count = table_count = 0
        for node in body:
            if node.tag == W + "p":
                paragraph_count += 1
                text = _paragraph_text(node)
                if text:
                    blocks.append((f"body/paragraph[{paragraph_count}]", text))
            elif node.tag == W + "tbl":
                table_count += 1
                for index, row in enumerate(node.findall(W + "tr"), 1):
                    cells = [
                        "\n".join(_paragraph_text(p) for p in cell.iter(W + "p"))
                        for cell in row.findall(W + "tc")
                    ]
                    if any(cells):
                        blocks.append(
                            (
                                f"body/table[{table_count}]/row[{index}]",
                                " | ".join(cells),
                            )
                        )
            elif (
                node.tag != W + "sectPr"
                and "unsupported_embedded_content" not in issues
            ):
                issues.append("unsupported_embedded_content")
        return blocks, issues


def read_json(raw: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Preserve decimal precision and reject duplicate or non-finite values."""
    text = raw.decode("utf-8-sig")
    data = json.loads(
        text,
        parse_float=Decimal,
        object_pairs_hook=_json_object,
        parse_constant=_invalid_constant,
    )
    records = data if isinstance(data, list) else [data]
    if not all(isinstance(item, dict) for item in records):
        raise ValueError("Invalid JSON source records")
    return text, records


def read_text(raw: bytes) -> list[tuple[str, str]]:
    return [
        (f"line[{number}]", line)
        for number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1)
        if line.strip()
    ]
