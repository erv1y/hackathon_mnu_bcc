"""Пакет исходных документов и преобразование локальных координат."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import Field

from audit_profile import DataModel
from document_review import structure_document
from schemas import DocumentRole

MAX_DOCUMENTS = 5


class PreparedDocument(DataModel):
    text: str = Field(min_length=1, max_length=60000)
    name: str | None = Field(default=None, max_length=180)
    role: DocumentRole = "main"


def safe_file_name(value: str) -> str:
    name = PurePosixPath(value.replace("\\", "/")).name
    return "".join(char for char in name if char.isprintable()).strip()[:180] or "document"


@dataclass(frozen=True)
class DocumentSource:
    text: str
    name: str | None = None
    role: DocumentRole = "main"
    warnings: tuple[str, ...] = ()


class DocumentBundle:
    def __init__(self, sources: list[DocumentSource]):
        if not 1 <= len(sources) <= MAX_DOCUMENTS:
            raise ValueError("Пакет должен содержать от одного до пяти документов.")
        self.sources = tuple(sources)
        self.text = "\n\n".join(item.text for item in sources)
        self.manifest: list[dict] = []
        start, line = 0, 1
        for number, source in enumerate(sources, start=1):
            name = safe_file_name(source.name) if source.name else None
            self.manifest.append({
                "id": f"D{number}", "name": name, "role": source.role,
                "format": PurePosixPath(name).suffix[1:].upper() if name else "TXT",
                "start": start, "end": start + len(source.text), "line_start": line,
            })
            start += len(source.text) + 2
            line += source.text.count("\n") + 2

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(
            f"{info['name']} · {warning}" if len(self.sources) > 1 else warning
            for info, source in zip(self.manifest, self.sources, strict=True)
            for warning in source.warnings
        )

    @property
    def fingerprint(self) -> str:
        data = json.dumps([(item.role, item.text) for item in self.sources], ensure_ascii=False)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def prompt_documents(self) -> list[dict]:
        return [
            {"id": info["id"], "name": info["name"], "role": info["role"], "text": source.text}
            for info, source in zip(self.manifest, self.sources, strict=True)
        ]

    def point(self, position: int, local: dict) -> dict:
        info = self.manifest[position]
        return {
            **local,
            "start": local["start"] + info["start"],
            "end": local["end"] + info["start"],
            "line_start": local["line_start"] + info["line_start"] - 1,
            "line_end": local["line_end"] + info["line_start"] - 1,
            "file_line_start": local["line_start"],
            "file_line_end": local["line_end"],
            "file_id": info["id"], "file_name": info["name"], "file_role": info["role"],
        }

    def excerpt(self, position: int, start: int, end: int) -> dict:
        text = self.sources[position].text
        return self.point(position, {
            "text": text[start:end], "start": start, "end": end,
            "line_start": text.count("\n", 0, start) + 1,
            "line_end": text.count("\n", 0, end) + 1,
        })

    def requirements(self) -> list[dict]:
        rows = []
        for pos, source in enumerate(self.sources):
            for row in structure_document(source.text):
                rows.append(self.point(pos, {**row, "id": f"REQ-{len(rows) + 1:03d}"}))
        return rows
