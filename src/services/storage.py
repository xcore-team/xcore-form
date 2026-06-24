"""
FileStorageService — validation + délégation au StorageBackend.

Responsabilités :
  - Valider taille et extension du fichier
  - Générer un file_id unique
  - Nettoyer le nom de fichier (path traversal, caractères dangereux)
  - Déléguer l'I/O au backend (local, S3, R2…)

Le backend est injecté au constructeur via storage_backends.build_backend().
Clé de stockage : {form_id}/{file_id}
"""
from __future__ import annotations

import logging
import mimetypes
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from .storage_backends import StorageBackend

logger = logging.getLogger("xform.storage")

DEFAULT_ALLOWED_EXT = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt",
    ".xls", ".xlsx", ".csv", ".ods",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tiff",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".ppt", ".pptx", ".odp",
}

BLOCKED_EXT = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".dll", ".sys", ".vbs",
    ".ps1", ".sh", ".bash", ".zsh", ".fish", ".rb", ".py", ".pl",
    ".php", ".asp", ".aspx", ".jsp", ".cgi", ".scr", ".pif", ".hta",
    ".js", ".ts", ".jar", ".class", ".war", ".ear",
}


class FileStorageError(Exception):
    pass


class FileTooLargeError(FileStorageError):
    pass


class FileTypeNotAllowedError(FileStorageError):
    pass


class UploadedFile:
    def __init__(
        self,
        file_id: str,
        original_name: str,
        stored_name: str,
        form_id: str,
        field_name: str,
        size_bytes: int,
        mime_type: str,
        key: str,
    ):
        self.file_id       = file_id
        self.original_name = original_name
        self.stored_name   = stored_name
        self.form_id       = form_id
        self.field_name    = field_name
        self.size_bytes    = size_bytes
        self.mime_type     = mime_type
        self.key           = key           # clé dans le backend : {form_id}/{file_id}
        self.uploaded_at   = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "file_id":       self.file_id,
            "original_name": self.original_name,
            "size_bytes":    self.size_bytes,
            "mime_type":     self.mime_type,
            "form_id":       self.form_id,
            "field_name":    self.field_name,
            "uploaded_at":   self.uploaded_at.isoformat(),
        }


class FileStorageService:
    """
    Valide et stocke les fichiers via le backend configuré.

    Usage :
        backend = build_backend(config, plugin_dir)
        storage = FileStorageService(backend, max_size_mb=10)
        result  = await storage.save(content, filename, form_id, field_name)
        content = await storage.read(file_id, form_id)
        await storage.delete(file_id, form_id)
    """

    def __init__(
        self,
        backend: StorageBackend,
        max_size_mb: int = 10,
        allowed_ext: Optional[set] = None,
        blocked_ext: Optional[set] = None,
    ) -> None:
        self._backend  = backend
        self._max_bytes = max_size_mb * 1024 * 1024
        self._allowed  = {e.lower() for e in (allowed_ext or DEFAULT_ALLOWED_EXT)}
        self._blocked  = {e.lower() for e in (blocked_ext or BLOCKED_EXT)}

    # ── Upload ────────────────────────────────────────────────

    async def save(
        self,
        content: bytes,
        filename: str,
        form_id: str,
        field_name: str,
    ) -> UploadedFile:
        size = len(content)
        if size == 0:
            raise FileStorageError("Le fichier est vide.")

        if size > self._max_bytes:
            raise FileTooLargeError(
                f"Fichier trop volumineux ({size // (1024*1024)}MB). "
                f"Maximum : {self._max_bytes // (1024*1024)}MB."
            )

        safe_name = self._sanitize(filename)
        ext = Path(safe_name).suffix.lower()

        if ext in self._blocked:
            raise FileTypeNotAllowedError(
                f"Ce type de fichier est interdit pour des raisons de sécurité : {ext}"
            )
        if self._allowed and ext not in self._allowed:
            raise FileTypeNotAllowedError(
                f"Extension '{ext}' non autorisée. "
                f"Formats acceptés : {', '.join(sorted(self._allowed))}"
            )

        mime        = self._detect_mime(content, safe_name)
        file_id     = uuid4().hex
        stored_name = f"{file_id}{ext}"          # clé = file_id + extension
        key         = f"{form_id}/{stored_name}"

        await self._backend.put(key, content)

        logger.info(
            "Upload OK : form=%s field=%s file_id=%s name=%s size=%d mime=%s",
            form_id, field_name, file_id, safe_name, size, mime,
        )
        return UploadedFile(
            file_id=file_id,
            original_name=filename,
            stored_name=stored_name,
            form_id=form_id,
            field_name=field_name,
            size_bytes=size,
            mime_type=mime,
            key=key,
        )

    # ── Lecture / suppression ─────────────────────────────────

    def _key(self, file_id: str, form_id: str, stored_name: Optional[str] = None) -> str:
        """Reconstruit la clé backend : {form_id}/{stored_name} ou {form_id}/{file_id}."""
        name = stored_name if stored_name else file_id
        return f"{form_id}/{name}"

    async def read(self, file_id: str, form_id: str, stored_name: Optional[str] = None) -> Optional[bytes]:
        return await self._backend.get(self._key(file_id, form_id, stored_name))

    async def delete(self, file_id: str, form_id: str, stored_name: Optional[str] = None) -> bool:
        return await self._backend.delete(self._key(file_id, form_id, stored_name))

    async def delete_all_for_form(self, form_id: str) -> int:
        return await self._backend.delete_prefix(f"{form_id}/")

    async def exists(self, file_id: str, form_id: str, stored_name: Optional[str] = None) -> bool:
        return await self._backend.exists(self._key(file_id, form_id, stored_name))

    async def close(self) -> None:
        await self._backend.close()

    # ── Helpers privés ────────────────────────────────────────

    @staticmethod
    def _sanitize(filename: str) -> str:
        name = unicodedata.normalize("NFKD", filename)
        name = name.encode("ascii", "ignore").decode("ascii")
        name = Path(name).name
        stem   = re.sub(r"[^\w\s.-]", "", Path(name).stem)
        stem   = re.sub(r"\s+", "_", stem.strip())[:200]
        suffix = Path(name).suffix.lower()
        return f"{stem}{suffix}" if stem else f"file{suffix}"

    @staticmethod
    def _detect_mime(content: bytes, filename: str) -> str:
        MAGIC = [
            (b"%PDF",         "application/pdf"),
            (b"\xff\xd8\xff", "image/jpeg"),
            (b"\x89PNG\r\n",  "image/png"),
            (b"GIF87a",       "image/gif"),
            (b"GIF89a",       "image/gif"),
            (b"PK\x03\x04",   "application/zip"),
        ]
        for magic, mime in MAGIC:
            if content[:len(magic)] == magic:
                if mime == "application/zip":
                    ext = Path(filename).suffix.lower()
                    return {
                        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    }.get(ext, "application/zip")
                return mime
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or "application/octet-stream"
