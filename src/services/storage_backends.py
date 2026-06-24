"""
Backends de stockage pour XForm.

Interface uniforme : put / get / delete / delete_prefix / exists
Clé de stockage : {form_id}/{file_id}

Backends disponibles :
  - LocalBackend  : disque local (défaut, dev/single-instance)
  - S3Backend     : AWS S3, Cloudflare R2, MinIO (production)
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("xform.storage")


class StorageBackend(ABC):
    """Interface commune pour tous les backends de stockage."""

    @abstractmethod
    async def put(self, key: str, content: bytes) -> None:
        """Stocke `content` sous la clé `key`."""

    @abstractmethod
    async def get(self, key: str) -> Optional[bytes]:
        """Retourne le contenu ou None si absent."""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Supprime la clé. Retourne True si trouvée."""

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> int:
        """Supprime toutes les clés commençant par `prefix`. Retourne le nombre supprimé."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Retourne True si la clé existe."""

    @abstractmethod
    async def close(self) -> None:
        """Libère les ressources (connexions, sessions)."""


# ─────────────────────────────────────────────────────────────
# Backend local
# ─────────────────────────────────────────────────────────────

class LocalBackend(StorageBackend):
    """
    Stockage sur le disque local.
    root_dir/{key} → fichier physique.
    """

    def __init__(self, root_dir: Path) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        logger.info("LocalBackend prêt — root=%s", self._root)

    def _path(self, key: str) -> Path:
        # Sécurité : interdit le path traversal
        resolved = (self._root / key).resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise ValueError(f"Clé invalide (path traversal) : {key!r}")
        return resolved

    async def put(self, key: str, content: bytes) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        try:
            tmp.write_bytes(content)
            tmp.rename(dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    async def get(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.exists():
            path.unlink()
            return True
        return False

    async def delete_prefix(self, prefix: str) -> int:
        target_dir = (self._root / prefix).resolve()
        if not target_dir.exists():
            return 0
        count = 0
        for f in target_dir.iterdir():
            if f.is_file():
                f.unlink()
                count += 1
        try:
            target_dir.rmdir()
        except OSError:
            pass
        return count

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    async def close(self) -> None:
        pass


# ─────────────────────────────────────────────────────────────
# Backend S3 (AWS S3 / Cloudflare R2 / MinIO)
# ─────────────────────────────────────────────────────────────

class S3Backend(StorageBackend):
    """
    Stockage sur S3-compatible (AWS S3, Cloudflare R2, MinIO).

    Utilise aioboto3 pour les opérations async.
    La clé S3 = {prefix}{key}.
    """

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        access_key_id: str = "",
        secret_access_key: str = "",
        prefix: str = "xform/",
        endpoint_url: Optional[str] = None,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.rstrip("/") + "/" if prefix else ""
        self._session_kwargs: Dict[str, Any] = {
            "region_name": region,
        }
        if access_key_id and secret_access_key:
            self._session_kwargs["aws_access_key_id"] = access_key_id
            self._session_kwargs["aws_secret_access_key"] = secret_access_key
        self._client_kwargs: Dict[str, Any] = {}
        if endpoint_url:
            self._client_kwargs["endpoint_url"] = endpoint_url

        self._session = None
        self._client = None
        logger.info(
            "S3Backend prêt — bucket=%s prefix=%s endpoint=%s",
            bucket, self._prefix, endpoint_url or "AWS",
        )

    async def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import aioboto3
        except ImportError:
            raise RuntimeError(
                "aioboto3 requis pour le backend S3. "
                "Installez-le : pip install aioboto3"
            )
        self._session = aioboto3.Session(**self._session_kwargs)
        self._client = await self._session.client("s3", **self._client_kwargs).__aenter__()
        return self._client

    def _s3_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def put(self, key: str, content: bytes) -> None:
        client = await self._get_client()
        await client.put_object(
            Bucket=self._bucket,
            Key=self._s3_key(key),
            Body=content,
        )

    async def get(self, key: str) -> Optional[bytes]:
        client = await self._get_client()
        try:
            resp = await client.get_object(Bucket=self._bucket, Key=self._s3_key(key))
            return await resp["Body"].read()
        except client.exceptions.NoSuchKey:
            return None
        except Exception as exc:
            # ClientError pour les clés absentes selon le SDK
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                return None
            raise

    async def delete(self, key: str) -> bool:
        if not await self.exists(key):
            return False
        client = await self._get_client()
        await client.delete_object(Bucket=self._bucket, Key=self._s3_key(key))
        return True

    async def delete_prefix(self, prefix: str) -> int:
        client = await self._get_client()
        s3_prefix = self._s3_key(prefix)
        paginator = client.get_paginator("list_objects_v2")
        count = 0
        async for page in paginator.paginate(Bucket=self._bucket, Prefix=s3_prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            await client.delete_objects(
                Bucket=self._bucket,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
            )
            count += len(objects)
        return count

    async def exists(self, key: str) -> bool:
        client = await self._get_client()
        try:
            await client.head_object(Bucket=self._bucket, Key=self._s3_key(key))
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None


# ─────────────────────────────────────────────────────────────
# Backend Supabase Storage
# ─────────────────────────────────────────────────────────────

class SupabaseBackend(StorageBackend):
    """
    Stockage sur Supabase Storage via le SDK officiel supabase-py v2.

    Nécessite : pip install supabase

    La clé de stockage = {prefix}{key}  (ex: uploads/form_id/file_id)
    """

    def __init__(
        self,
        url: str,
        key: str,
        bucket: str,
        prefix: str = "uploads/",
        public: bool = False,
    ) -> None:
        if not url or not key:
            raise ValueError("storage.supabase.url et storage.supabase.key sont requis.")
        if not bucket:
            raise ValueError("storage.supabase.bucket est requis.")

        self._url    = url
        self._key    = key
        self._bucket = bucket
        self._prefix = prefix.rstrip("/") + "/" if prefix else ""
        self._public = public
        self._client = None
        logger.info("SupabaseBackend prêt — url=%s bucket=%s prefix=%s", url, bucket, self._prefix)

    async def _get_storage(self):
        if self._client is None:
            try:
                from supabase import acreate_client
            except ImportError:
                raise RuntimeError(
                    "supabase requis pour le backend Supabase. "
                    "Installez-le : pip install supabase"
                )
            self._client = await acreate_client(self._url, self._key)
        return self._client.storage.from_(self._bucket)

    def _full_path(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def put(self, key: str, content: bytes) -> None:
        storage = await self._get_storage()
        path = self._full_path(key)
        # upsert=True pour écraser si le fichier existe déjà
        await storage.upload(
            path=path,
            file=content,
            file_options={"upsert": "true"},
        )

    async def get(self, key: str) -> Optional[bytes]:
        storage = await self._get_storage()
        try:
            return await storage.download(self._full_path(key))
        except Exception as exc:
            if "not found" in str(exc).lower() or "404" in str(exc):
                return None
            raise

    async def delete(self, key: str) -> bool:
        if not await self.exists(key):
            return False
        storage = await self._get_storage()
        await storage.remove([self._full_path(key)])
        return True

    async def delete_prefix(self, prefix: str) -> int:
        storage = await self._get_storage()
        full_prefix = self._full_path(prefix)
        try:
            # list() retourne les fichiers dans ce "dossier"
            objects = await storage.list(full_prefix.rstrip("/"))
        except Exception:
            return 0
        if not objects:
            return 0
        paths = [f"{full_prefix}{obj['name']}" for obj in objects if obj.get("name")]
        if not paths:
            return 0
        await storage.remove(paths)
        return len(paths)

    async def exists(self, key: str) -> bool:
        storage = await self._get_storage()
        # list() sur le dossier parent et chercher le file_id
        path = self._full_path(key)
        parent = "/".join(path.split("/")[:-1])
        name   = path.split("/")[-1]
        try:
            objects = await storage.list(parent)
            return any(obj.get("name") == name for obj in (objects or []))
        except Exception:
            return False

    async def get_public_url(self, key: str) -> Optional[str]:
        """Retourne l'URL publique (seulement si le bucket est public)."""
        if not self._public:
            return None
        storage = await self._get_storage()
        return storage.get_public_url(self._full_path(key))

    async def get_signed_url(self, key: str, expires_in: int = 3600) -> Optional[str]:
        """Retourne une URL signée valable `expires_in` secondes."""
        storage = await self._get_storage()
        try:
            result = await storage.create_signed_url(self._full_path(key), expires_in)
            return result.get("signedURL") or result.get("signedUrl")
        except Exception as exc:
            logger.warning("Impossible de créer une URL signée : %s", exc)
            return None

    async def close(self) -> None:
        self._client = None


# ─────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────

def _resolve_env_vars(config: Dict[str, Any], plugin_dir: Path) -> Dict[str, Any]:
    """
    Résout les placeholders ${VAR} dans la config storage en lisant d'abord
    le fichier .env du plugin, puis les variables d'environnement du process.
    """
    import os
    import re

    env: Dict[str, str] = {}

    # Charger le .env du plugin si présent
    env_file = plugin_dir / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")

    # Les variables d'environnement du process ont priorité
    env.update(os.environ)

    def resolve(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        def replacer(m: re.Match) -> str:
            return env.get(m.group(1), m.group(0))
        return re.sub(r"\$\{([^}]+)\}", replacer, value)

    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(i) for i in obj]
        return resolve(obj)

    return walk(config)


def build_backend(config: Dict[str, Any], plugin_dir: Path) -> StorageBackend:
    """
    Crée le backend de stockage depuis la config plugin.yaml (section `storage:`).

    config = ctx.config.get("storage") or {}
    """
    config = _resolve_env_vars(config, plugin_dir)
    backend_name = (config.get("backend") or "local").lower()

    if backend_name == "local":
        local_cfg = config.get("local") or {}
        path_str = local_cfg.get("path") or "data/uploads"
        root = plugin_dir / path_str if not Path(path_str).is_absolute() else Path(path_str)
        return LocalBackend(root_dir=root)

    if backend_name in ("s3", "r2"):
        cfg_key = "r2" if backend_name == "r2" else "s3"
        s3_cfg = config.get(cfg_key) or {}

        bucket = s3_cfg.get("bucket") or ""
        if not bucket:
            raise ValueError(f"storage.{cfg_key}.bucket est requis pour le backend {backend_name}.")

        endpoint_url = s3_cfg.get("endpoint_url") or None
        if backend_name == "r2":
            account_id = s3_cfg.get("account_id") or ""
            if account_id and not endpoint_url:
                endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"

        return S3Backend(
            bucket=bucket,
            region=s3_cfg.get("region") or "auto",
            access_key_id=s3_cfg.get("access_key_id") or "",
            secret_access_key=s3_cfg.get("secret_access_key") or "",
            prefix=s3_cfg.get("prefix") or "xform/",
            endpoint_url=endpoint_url,
        )

    if backend_name == "supabase":
        sb_cfg = config.get("supabase") or {}
        return SupabaseBackend(
            url=sb_cfg.get("url") or "",
            key=sb_cfg.get("key") or "",
            bucket=sb_cfg.get("bucket") or "",
            prefix=sb_cfg.get("prefix") or "uploads/",
            public=bool(sb_cfg.get("public", False)),
        )

    raise ValueError(
        f"Backend de stockage inconnu : '{backend_name}'. "
        "Valeurs acceptées : local, s3, r2, supabase"
    )
