"""
Plugin : xform
==============
Build forms. Launch workflows.

Routes HTTP (publiques, sans auth) :
  GET    /public/{slug}                — structure du formulaire
  POST   /public/{slug}/upload         — uploader UN fichier → retourne file_id
  POST   /public/{slug}/submit         — soumettre JSON (+ file_ids)
  POST   /public/{slug}/submit-form    — soumettre multipart tout-en-un (HTML natif)
  GET    /files/{form_id}/{file_id}    — télécharger un fichier (admin authentifié)

Routes HTTP (authentifiées) :
  GET    /forms                        — liste des formulaires
  POST   /forms                        — créer un formulaire
  GET    /forms/{form_id}              — détail
  PUT    /forms/{form_id}              — modifier
  DELETE /forms/{form_id}              — supprimer
  GET    /forms/{form_id}/submissions  — soumissions
  GET    /forms/{form_id}/export       — export xlsx/csv/json
  GET    /forms/{form_id}/analytics    — statistiques
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes as _mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from xcore.kernel.api.rbac import AuthPayload, get_current_user
from xcore.sdk import AutoDispatchMixin, RoutedPlugin, RouterRegistry, TrustedBase

from .ipc import IPCCommands
from .models.orm import Base
from .repositories.store import XFormStore
from .schemas.form import (
    FormDefinition,
    FormField,
    FormSettings,
    FormStatus,
    FormStep,
    FormSubmission,
    FormTheme,
    SubmissionMeta,
)
from .services.export import XFormExporter
from .services.pipeline import XFormPipeline
from .services.slug import unique_slug
from .services.storage import (
    FileStorageError,
    FileStorageService,
    FileTooLargeError,
    FileTypeNotAllowedError,
)
from .services.validator import XFormValidator

logger = logging.getLogger("xform")
router = RouterRegistry()


# ─────────────────────────────────────────────────────────────
# Request bodies
# ─────────────────────────────────────────────────────────────


class CreateFormBody(BaseModel):
    title: str
    description: Optional[str] = None
    fields: List[Dict[str, Any]]
    steps: List[Dict[str, Any]] = []
    settings: Dict[str, Any] = {}
    theme: Dict[str, Any] = {}
    tags: List[str] = []


class UpdateFormBody(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    fields: Optional[List[Dict[str, Any]]] = None
    steps: Optional[List[Dict[str, Any]]] = None
    settings: Optional[Dict[str, Any]] = None
    theme: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = None


class SubmitBody(BaseModel):
    """
    Soumission JSON.
    Pour les champs fichier, la valeur = file_id retourné par /upload.
    Ex: { "data": { "nom": "Alice", "cv": "a1b2c3..." } }
    """

    data: Dict[str, Any]
    meta: Dict[str, Any] = {}


# ─────────────────────────────────────────────────────────────
# Plugin principal
# ─────────────────────────────────────────────────────────────


class Plugin(RoutedPlugin, IPCCommands, TrustedBase):
    # ── Lifecycle ─────────────────────────────────────────────

    async def on_load(self) -> None:
        logger.info("Initialisation XForm...")
        self.db = self.get_service("db")
        try:
            self.cache = self.get_service("cache")
        except KeyError:
            self.cache = None
            logger.warning("XForm : cache non disponible.")

        await self._create_tables()

        # Dossier data du plugin → plugins/xform/data/uploads/
        plugin_dir = Path(__file__).parent.parent
        data_dir = plugin_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        max_size_mb = int(self.ctx.env.get("MAX_FILE_SIZE_MB", "10"))

        self._store = XFormStore(self.db, self.cache)
        self._validator = XFormValidator()
        self._exporter = XFormExporter()
        self._storage = FileStorageService(data_dir=data_dir, max_size_mb=max_size_mb)
        self._pipeline = XFormPipeline(
            store=self._store,
            call_plugin=self.call_plugin,
            events=self.ctx.events,
        )

        @self.ctx.health.register("xform.database")
        async def check_db():
            try:
                with self.db() as db:
                    db.execute("SELECT 1")
                return True, "DB OK"
            except Exception as e:
                return False, str(e)

        self.ctx.events.on("xform.send_email")(self._on_send_email)
        logger.info("XForm prêt (uploads max=%dMB, dir=%s).", max_size_mb, data_dir)

    async def on_unload(self) -> None:
        logger.info("XForm arrêté.")

    async def _create_tables(self) -> None:
        async with self.db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # ── Event handlers ────────────────────────────────────────

    async def _on_send_email(self, event: Any) -> None:
        try:
            data = event.data if hasattr(event, "data") else event
            await self.call_plugin("mail", "send", data)
        except Exception as e:
            logger.warning("XForm : email non envoyé (%s)", e)

    # ── Helpers ───────────────────────────────────────────────

    def _ok(self, data: Optional[Dict[str, Any]] = None, **kwargs) -> dict:
        from xcore.kernel.api.contract import ok

        return ok(data, **kwargs)

    def _error(self, msg: str, code: Optional[str] = None, **kwargs) -> dict:
        from xcore.kernel.api.contract import error

        return error(msg, code, **kwargs)

    async def _build_form(
        self, payload: Dict[str, Any], owner_id: str
    ) -> FormDefinition:
        fields = [FormField.model_validate(f) for f in (payload.get("fields") or [])]
        steps = [FormStep.model_validate(s) for s in (payload.get("steps") or [])]
        slug = await unique_slug(
            payload.get("title", "form"),
            lambda s: self._store.slug_exists(s),
        )
        return FormDefinition(
            title=payload.get("title", ""),
            description=payload.get("description"),
            slug=slug,
            owner_id=owner_id,
            fields=fields,
            steps=steps,
            settings=FormSettings.model_validate(payload.get("settings") or {}),
            theme=FormTheme.model_validate(payload.get("theme") or {}),
            tags=payload.get("tags") or [],
            status=FormStatus.DRAFT,
        )

    def _get_form_or_404(self, form: Optional[FormDefinition]) -> FormDefinition:
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")
        return form

    def _require_active(self, form: FormDefinition) -> None:
        if form.status != FormStatus.ACTIVE:
            raise HTTPException(
                status_code=410, detail="Ce formulaire n'accepte plus de réponses."
            )

    async def _save_and_pipeline(
        self, form: FormDefinition, data: Dict[str, Any], meta: Dict[str, Any]
    ) -> dict:
        """Sauvegarde une soumission et lance le pipeline en arrière-plan."""
        submission = FormSubmission(
            form_id=form.id,
            data=data,
            meta=SubmissionMeta.model_validate(meta),
        )
        saved = await self._store.save_submission(submission)
        asyncio.create_task(self._pipeline.run(form, saved))
        return {
            "status": "ok",
            "submission_id": saved.id,
            "message": form.settings.confirmation_message,
            "redirect_url": form.settings.redirect_url,
        }

    async def _validate_file_refs(
        self, form: FormDefinition, data: Dict[str, Any]
    ) -> None:
        """Vérifie que tous les file_ids référencés existent sur disque."""
        for field in form.fields:
            if field.type.value != "file":
                continue
            file_id = data.get(field.name) or data.get(field.id)
            if file_id and isinstance(file_id, str):
                response = await self.call_plugin(
                    "form_files", "form.exist", {"file_id": file_id, "form_id": form.id}
                )
                if not self._storage.exists(file_id, form.id):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Fichier introuvable pour le champ « {field.label} ». "
                            "Uploadez le fichier d'abord via /public/{slug}/upload."
                        ),
                    )

    # ─────────────────────────────────────────────────────────
    # Routes HTTP — Authentifiées
    # ─────────────────────────────────────────────────────────

    @router.get("/forms", tags=["xform"])
    async def http_list_forms(
        self,
        current_user: AuthPayload = Depends(get_current_user),
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        return await self.ipc_list_forms(
            {
                "owner_id": current_user["sub"],
                "status": status,
                "limit": limit,
                "offset": offset,
            }
        )

    @router.post("/forms", tags=["xform"])
    async def http_create_form(
        self,
        body: CreateFormBody,
        current_user: AuthPayload = Depends(get_current_user),
    ) -> dict:
        payload = body.model_dump()
        payload["owner_id"] = current_user["sub"]
        return await self.ipc_create_form(payload)

    @router.get("/forms/{form_id}", tags=["xform"])
    async def http_get_form(
        self,
        form_id: str,
        current_user: AuthPayload = Depends(get_current_user),
    ) -> dict:
        return await self.ipc_get_form({"form_id": form_id})

    @router.put("/forms/{form_id}", tags=["xform"])
    async def http_update_form(
        self,
        form_id: str,
        body: UpdateFormBody,
        current_user: AuthPayload = Depends(get_current_user),
    ) -> dict:
        payload = body.model_dump(exclude_none=True)
        payload["form_id"] = form_id
        return await self.ipc_update_form(payload)

    @router.delete("/forms/{form_id}", tags=["xform"])
    async def http_delete_form(
        self,
        form_id: str,
        current_user: AuthPayload = Depends(get_current_user),
    ) -> dict:
        return await self.ipc_delete_form({"form_id": form_id})

    @router.get("/forms/{form_id}/submissions", tags=["xform"])
    async def http_list_submissions(
        self,
        form_id: str,
        current_user: AuthPayload = Depends(get_current_user),
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        return await self.ipc_list_submissions(
            {
                "form_id": form_id,
                "status": status,
                "limit": limit,
                "offset": offset,
            }
        )

    @router.get("/forms/{form_id}/analytics", tags=["xform"])
    async def http_analytics(
        self,
        form_id: str,
        current_user: AuthPayload = Depends(get_current_user),
    ) -> dict:
        return await self.ipc_analytics({"form_id": form_id})

    @router.get("/forms/{form_id}/export", tags=["xform"])
    async def http_export(
        self,
        form_id: str,
        format: str = "xlsx",
        current_user: AuthPayload = Depends(get_current_user),
    ) -> Any:
        form = await self._store.get_form(form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")
        subs = await self._store.list_submissions(form_id, limit=10000)
        if format == "xlsx":
            content = self._exporter.export_xlsx(form, subs)
            return Response(
                content=content,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": f'attachment; filename="{form.slug}_export.xlsx"'
                },
            )
        elif format == "csv":
            return Response(
                content=self._exporter.export_csv(form, subs).encode("utf-8"),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f'attachment; filename="{form.slug}_export.csv"'
                },
            )
        return Response(
            content=self._exporter.export_json(form, subs).encode("utf-8"),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{form.slug}_export.json"'
            },
        )

    # ── Télécharger un fichier uploadé ────────────────────────

    @router.get("/files/{form_id}/{file_id}", tags=["xform"])
    async def http_download_file(
        self,
        form_id: str,
        file_id: str,
        current_user: AuthPayload = Depends(get_current_user),
    ) -> Any:
        """
        Télécharge un fichier uploadé.
        Accessible uniquement par l'owner du formulaire ou un admin.
        """
        form = await self._store.get_form(form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")
        if form.owner_id != current_user["sub"] and "admin" not in current_user.get(
            "roles", []
        ):
            raise HTTPException(status_code=403, detail="Accès non autorisé.")

        content = self._storage.read(file_id, form_id)
        if content is None:
            raise HTTPException(status_code=404, detail="Fichier introuvable.")

        original_name = self._storage.get_original_name(file_id, form_id) or "file"
        mime, _ = _mimetypes.guess_type(original_name)
        mime = mime or "application/octet-stream"

        return Response(
            content=content,
            media_type=mime,
            headers={
                "Content-Disposition": f'attachment; filename="{original_name}"',
                "Content-Length": str(len(content)),
            },
        )

    # ─────────────────────────────────────────────────────────
    # Routes HTTP — Publiques (sans auth)
    # ─────────────────────────────────────────────────────────

    @router.get("/public/{slug}", tags=["xform-public"])
    async def http_public_get_form(self, slug: str, request: Request) -> dict:
        """
        Retourne la structure JSON du formulaire pour le frontend.
        Inclut `has_file_fields` pour que le frontend sache s'il doit
        gérer des uploads.
        """
        form = await self._store.get_form_by_slug(slug)
        self._get_form_or_404(form)
        self._require_active(form)

        asyncio.create_task(
            self._store.track_view(
                form_id=form.id,
                ip=request.client.host if request.client else "",
                user_agent=request.headers.get("user-agent", ""),
            )
        )

        return {
            "status": "ok",
            "form": {
                "id": form.id,
                "title": form.title,
                "description": form.description,
                "fields": [f.model_dump(mode="json") for f in form.fields],
                "steps": [s.model_dump(mode="json") for s in form.steps],
                "settings": {
                    "multi_step": form.settings.multi_step,
                    "confirmation_message": form.settings.confirmation_message,
                    "redirect_url": form.settings.redirect_url,
                },
                "theme": form.theme.model_dump(mode="json"),
                # Indique au SDK/frontend quels champs nécessitent un upload préalable
                "has_file_fields": any(f.type.value == "file" for f in form.fields),
            },
        }

    @router.post("/public/{slug}/upload", tags=["xform-public"])
    async def http_public_upload_file(
        self,
        slug: str,
        field_name: str = Form(..., description="Nom du champ fichier (field.name)"),
        file: UploadFile = File(..., description="Fichier à uploader"),
    ) -> dict:
        """
        Étape 1 — Upload d'UN fichier avant soumission.

        Retourne un `file_id` à inclure dans le corps JSON de /submit.

        Flux SDK :
            # 1. Upload
            const { file_id } = await xform.public.uploadFile(slug, 'cv', fileInput.files[0])

            # 2. Soumettre avec le file_id
            await xform.public.submit(slug, { nom: 'Alice', cv: file_id })
        """
        form = await self._store.get_form_by_slug(slug)
        self._get_form_or_404(form)
        self._require_active(form)

        # Vérifier que le champ existe et est de type file
        field = form.get_field_by_name(field_name)
        if not field:
            raise HTTPException(
                400, f"Champ '{field_name}' introuvable dans ce formulaire."
            )
        if field.type.value != "file":
            raise HTTPException(
                400, f"Le champ '{field_name}' n'est pas un champ fichier."
            )

        content = await file.read()
        if not content:
            raise HTTPException(400, "Fichier vide.")

        # Limite spécifique au champ
        if field.max_size_mb and len(content) > field.max_size_mb * 1024 * 1024:
            raise HTTPException(
                413,
                f"Fichier trop volumineux. Maximum pour « {field.label} » : {field.max_size_mb}MB.",
            )

        try:
            uploaded = await self._storage.save(
                content=content,
                filename=file.filename or "upload",
                form_id=form.id,
                field_name=field_name,
            )
        except FileTooLargeError as e:
            raise HTTPException(413, str(e))
        except FileTypeNotAllowedError as e:
            raise HTTPException(415, str(e))
        except FileStorageError as e:
            raise HTTPException(400, str(e))

        logger.info(
            "Upload OK : form=%s field=%s file_id=%s name=%s size=%d",
            form.id,
            field_name,
            uploaded.file_id,
            uploaded.original_name,
            uploaded.size_bytes,
        )

        return {
            "status": "ok",
            "file_id": uploaded.file_id,
            "original_name": uploaded.original_name,
            "size_bytes": uploaded.size_bytes,
            "mime_type": uploaded.mime_type,
        }

    @router.post("/public/{slug}/submit", tags=["xform-public"])
    async def http_public_submit(
        self,
        slug: str,
        body: SubmitBody,
        request: Request,
    ) -> dict:
        """
        Soumission JSON (après avoir uploadé les fichiers via /upload).

        Les valeurs des champs fichier = file_id retourné par /upload.
        """
        meta = dict(body.meta)
        meta["ip"] = request.client.host if request.client else ""
        meta["user_agent"] = request.headers.get("user-agent", "")
        return await self.ipc_submit({"slug": slug, "data": body.data, "meta": meta})

    @router.post("/public/{slug}/submit-form", tags=["xform-public"])
    async def http_public_submit_multipart(
        self,
        slug: str,
        request: Request,
    ) -> dict:
        """
        Soumission tout-en-un via multipart/form-data.

        Envoie données + fichiers dans une seule requête.
        Compatible avec un <form enctype="multipart/form-data"> HTML natif.
        Le backend gère automatiquement l'upload des fichiers.

        Exemple fetch :
            const fd = new FormData(document.querySelector('form'))
            await fetch('/public/mon-slug/submit-form', { method: 'POST', body: fd })
        """
        form = await self._store.get_form_by_slug(slug)
        self._get_form_or_404(form)
        self._require_active(form)

        try:
            form_data = await request.form()
        except Exception as e:
            raise HTTPException(400, f"Données multipart invalides : {e}")

        data: Dict[str, Any] = {}
        meta: Dict[str, Any] = {
            "ip": request.client.host if request.client else "",
            "user_agent": request.headers.get("user-agent", ""),
        }
        file_field_names = {f.name for f in form.fields if f.type.value == "file"}
        upload_errors = []

        for key, value in form_data.multi_items():
            # Métadonnées sérialisées (optionnel)
            if key == "__meta__":
                try:
                    meta.update(json.loads(str(value)))
                except Exception:
                    pass
                continue

            if isinstance(value, UploadFile):
                # ── FICHIER ──────────────────────────────────
                if not value.filename:
                    continue  # fichier sans nom → ignorer
                content = await value.read()
                if not content:
                    continue  # fichier vide → ignoré (validé comme manquant si required)

                field = form.get_field_by_name(key)
                if not field:
                    continue

                if field.max_size_mb and len(content) > field.max_size_mb * 1024 * 1024:
                    upload_errors.append(
                        {
                            "field_name": key,
                            "message": f"Fichier trop volumineux pour « {field.label} » (max {field.max_size_mb}MB).",
                        }
                    )
                    continue

                try:
                    uploaded = await self._storage.save(
                        content=content,
                        filename=value.filename,
                        form_id=form.id,
                        field_name=key,
                    )
                    data[key] = uploaded.file_id
                    logger.info(
                        "Multipart upload: field=%s file_id=%s", key, uploaded.file_id
                    )
                except FileTooLargeError as e:
                    upload_errors.append({"field_name": key, "message": str(e)})
                except FileTypeNotAllowedError as e:
                    upload_errors.append({"field_name": key, "message": str(e)})
                except FileStorageError as e:
                    upload_errors.append({"field_name": key, "message": str(e)})
            else:
                # ── CHAMP TEXTE ──────────────────────────────
                str_val = str(value)
                # JSON pour les champs multi-valeurs (checkbox)
                if str_val.startswith("[") or str_val.startswith("{"):
                    try:
                        data[key] = json.loads(str_val)
                        continue
                    except json.JSONDecodeError:
                        pass
                # Si c'est un file_id déjà uploadé passé comme champ caché
                if key in file_field_names:
                    data[key] = str_val
                else:
                    data[key] = str_val

        if upload_errors:
            raise HTTPException(
                422,
                {
                    "message": "Erreurs lors de l'upload des fichiers.",
                    "errors": upload_errors,
                },
            )

        # Vérifier les file_ids
        await self._validate_file_refs(form, data)

        # Validation champs texte
        valid, errors = self._validator.validate(form, data)
        if not valid:
            raise HTTPException(
                422, {"message": "Données invalides.", "errors": errors}
            )

        return await self._save_and_pipeline(form, data, meta)

    # ─────────────────────────────────────────────────────────
    # Router
    # ─────────────────────────────────────────────────────────

    def get_router(self) -> Any | None:
        return self.RouterIn()
