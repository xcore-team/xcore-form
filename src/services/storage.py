"""
FileStorageService — stockage des fichiers uploadés via XForm.

Stratégie :
  - Sauvegarde dans plugins/xform/data/uploads/{form_id}/{file_id}_{original_name}
  - Retourne un file_id (uuid hex)
  - Vérifie taille max + extensions bloquées (exécutables)
  - Nettoyage du nom de fichier (sécurité path traversal)

Note : la validation se fait par EXTENSION et non par MIME strict.
Le MIME est détecté pour information mais n'est pas bloquant (trop de
faux positifs sur des fichiers légitimes selon les OS).
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

logger = logging.getLogger("xform.storage")

# Extensions autorisées par défaut (whitelist)
DEFAULT_ALLOWED_EXT = {
    # Documents
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt",
    # Tableurs
    ".xls", ".xlsx", ".csv", ".ods",
    # Images
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tiff",
    # Archives
    ".zip", ".tar", ".gz", ".rar", ".7z",
    # Présentations
    ".ppt", ".pptx", ".odp",
}

# Extensions TOUJOURS bloquées (exécutables, scripts)
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
    """Résultat d'un upload réussi."""
    def __init__(
        self,
        file_id: str,
        original_name: str,
        stored_name: str,
        form_id: str,
        field_name: str,
        size_bytes: int,
        mime_type: str,
        path: Path,
    ):
        self.file_id      = file_id
        self.original_name = original_name
        self.stored_name  = stored_name
        self.form_id      = form_id
        self.field_name   = field_name
        self.size_bytes   = size_bytes
        self.mime_type    = mime_type
        self.path         = path
        self.uploaded_at  = datetime.now(timezone.utc)

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
    Gère le stockage des fichiers uploadés.

    Validation :
      - Taille max configurable (défaut 10MB)
      - Whitelist d'extensions (défaut : documents, images, archives)
      - Blacklist d'extensions exécutables (toujours bloquée)
      - PAS de validation MIME stricte (trop de faux positifs)

    Usage :
        storage = FileStorageService(
            data_dir=Path("plugins/xform/data"),
            max_size_mb=10,
        )
        result = await storage.save(file_bytes, filename, form_id, field_name)
        content = storage.read(file_id, form_id)
        storage.delete(file_id, form_id)
    """

    def __init__(
        self,
        data_dir: Path,
        max_size_mb: int = 10,
        allowed_ext: Optional[set] = None,
        blocked_ext: Optional[set] = None,
    ) -> None:
        self._data_dir   = Path(data_dir)
        self._max_bytes  = max_size_mb * 1024 * 1024
        self._allowed    = {e.lower() for e in (allowed_ext or DEFAULT_ALLOWED_EXT)}
        self._blocked    = {e.lower() for e in (blocked_ext or BLOCKED_EXT)}
        self._data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "FileStorageService prêt — root=%s max=%dMB exts_ok=%d",
            self._data_dir, max_size_mb, len(self._allowed),
        )

    # ─────────────────────────────────────────────────────────
    # Upload
    # ─────────────────────────────────────────────────────────

    async def save(
        self,
        content: bytes,
        filename: str,
        form_id: str,
        field_name: str,
    ) -> "UploadedFile":
        """
        Sauvegarde un fichier et retourne un UploadedFile.
        Lève FileStorageError si la validation échoue.
        """
        # 1. Fichier vide
        size = len(content)
        if size == 0:
            raise FileStorageError("Le fichier est vide.")

        # 2. Taille max
        if size > self._max_bytes:
            max_mb = self._max_bytes // (1024 * 1024)
            raise FileTooLargeError(
                f"Fichier trop volumineux ({size // (1024*1024)}MB). "
                f"Maximum autorisé : {max_mb}MB."
            )

        # 3. Nettoyage du nom
        safe_name = self._sanitize_filename(filename)
        ext = Path(safe_name).suffix.lower()

        # 4. Vérification extension bloquée (exécutables)
        if ext in self._blocked:
            raise FileTypeNotAllowedError(
                f"Ce type de fichier est interdit pour des raisons de sécurité : {ext}"
            )

        # 5. Vérification whitelist (si configurée et non vide)
        if self._allowed and ext not in self._allowed:
            allowed_str = ", ".join(sorted(self._allowed))
            raise FileTypeNotAllowedError(
                f"Extension '{ext}' non autorisée. "
                f"Formats acceptés : {allowed_str}"
            )

        # 6. Détection MIME (informatif seulement)
        mime = self._detect_mime(content, safe_name)

        # 7. Stockage
        file_id     = uuid4().hex
        stored_name = f"{file_id}_{safe_name}"
        dest_dir    = self._dir_for_form(form_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path   = dest_dir / stored_name

        # Écriture atomique (tmp → rename)
        tmp_path = dest_dir / f"{file_id}.tmp"
        try:
            tmp_path.write_bytes(content)
            tmp_path.rename(dest_path)
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            raise FileStorageError(f"Erreur lors de l'écriture du fichier : {e}") from e

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
            path=dest_path,
        )

    # ─────────────────────────────────────────────────────────
    # Lecture / suppression
    # ─────────────────────────────────────────────────────────

    def get_path(self, file_id: str, form_id: str) -> Optional[Path]:
        """Trouve le chemin d'un fichier à partir de son file_id."""
        dest_dir = self._dir_for_form(form_id)
        if not dest_dir.exists():
            return None
        # Le fichier commence par file_id_
        matches = list(dest_dir.glob(f"{file_id}_*"))
        return matches[0] if matches else None

    def read(self, file_id: str, form_id: str) -> Optional[bytes]:
        """Lit le contenu d'un fichier."""
        path = self.get_path(file_id, form_id)
        return path.read_bytes() if path else None

    def get_original_name(self, file_id: str, form_id: str) -> Optional[str]:
        """Retourne le nom original du fichier."""
        path = self.get_path(file_id, form_id)
        if not path:
            return None
        # stored_name = {file_id}_{original_name}
        return path.name[len(file_id) + 1:]

    def delete(self, file_id: str, form_id: str) -> bool:
        """Supprime un fichier. Retourne True si trouvé et supprimé."""
        path = self.get_path(file_id, form_id)
        if path and path.exists():
            path.unlink()
            return True
        return False

    def delete_all_for_form(self, form_id: str) -> int:
        """Supprime tous les fichiers d'un formulaire. Retourne le nombre supprimé."""
        dest_dir = self._dir_for_form(form_id)
        if not dest_dir.exists():
            return 0
        count = 0
        for f in dest_dir.iterdir():
            f.unlink()
            count += 1
        try:
            dest_dir.rmdir()
        except OSError:
            pass
        return count

    def exists(self, file_id: str, form_id: str) -> bool:
        return self.get_path(file_id, form_id) is not None

    # ─────────────────────────────────────────────────────────
    # Helpers privés
    # ─────────────────────────────────────────────────────────

    def _dir_for_form(self, form_id: str) -> Path:
        return self._data_dir / "uploads" / form_id

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """
        Nettoie un nom de fichier :
        - Supprime les caractères dangereux (/, .., etc.)
        - Normalise unicode
        - Garde seulement alphanum + . - _
        """
        # Normalise unicode
        name = unicodedata.normalize("NFKD", filename)
        name = name.encode("ascii", "ignore").decode("ascii")
        # Garde seulement le basename (anti path traversal)
        name = Path(name).name
        # Supprime les caractères dangereux
        stem = Path(name).stem
        suffix = Path(name).suffix.lower()
        stem = re.sub(r"[^\w\s.-]", "", stem)
        stem = re.sub(r"\s+", "_", stem.strip())
        stem = stem[:200]  # longueur max
        return f"{stem}{suffix}" if stem else f"file{suffix}"

    @staticmethod
    def _detect_mime(content: bytes, filename: str) -> str:
        """Détecte le type MIME depuis les magic bytes + extension."""
        # Magic bytes pour les types courants
        MAGIC = [
            (b"%PDF",          "application/pdf"),
            (b"\xff\xd8\xff",  "image/jpeg"),
            (b"\x89PNG\r\n",   "image/png"),
            (b"GIF87a",        "image/gif"),
            (b"GIF89a",        "image/gif"),
            (b"RIFF",          "image/webp"),  # simplifié
            (b"PK\x03\x04",    "application/zip"),
            # Office Open XML (docx, xlsx) aussi zip
        ]
        for magic, mime in MAGIC:
            if content[:len(magic)] == magic:
                # Distingue zip / docx / xlsx par extension
                if mime == "application/zip":
                    ext = Path(filename).suffix.lower()
                    mime_map = {
                        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    }
                    return mime_map.get(ext, "application/zip")
                return mime

        # Fallback sur le content-type par extension
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or "application/octet-stream"