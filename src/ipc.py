from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from xcore.sdk import AutoDispatchMixin, action, error, ok, schema

from .domain.forms import FormField, FormSettings, FormStatus, FormStep, FormTheme
from .domain.submissions import FormSubmission, SubmissionMeta

logger = logging.getLogger("xform.ipc")


class IPCCommands(AutoDispatchMixin):

    @action("create_form")
    @schema(
        version="1.0",
        input={
            "owner_id": (str, ...),
            "title": (str, ...),
            "description": (Optional[str], None),
            "fields": (list, []),
            "steps": (list, []),
            "settings": (dict, {}),
            "theme": (dict, {}),
            "tags": (list, []),
        },
        output={"form": dict},
        type_response="model",
        unset=False,
    )
    async def ipc_create_form(self, payload) -> dict:
        try:
            form = await self._build_form(payload.model_dump(), payload.owner_id)
            form.status = FormStatus.ACTIVE
            saved = await self._store.save_form(form)
            return ok(result={"form": saved.model_dump(mode="json")})
        except Exception:
            logger.exception("Erreur création formulaire IPC")
            return error("Erreur lors de la création du formulaire.", "create_error")

    @action("update_form")
    @schema(
        version="1.0",
        input={
            "form_id": (str, ...),
            "title": (Optional[str], None),
            "description": (Optional[str], None),
            "fields": (Optional[list], None),
            "steps": (Optional[list], None),
            "settings": (Optional[dict], None),
            "theme": (Optional[dict], None),
            "status": (Optional[str], None),
            "tags": (Optional[list], None),
        },
        output={"form": dict},
        type_response="model",
        unset=False,
    )
    async def ipc_update_form(self, payload) -> dict:
        form = await self._store.get_form(payload.form_id)
        if not form:
            return error("Formulaire introuvable.", "not_found")
        try:
            if payload.title is not None:
                form.title = payload.title
            if payload.description is not None:
                form.description = payload.description
            if payload.fields is not None:
                form.fields = [FormField.model_validate(f) for f in payload.fields]
            if payload.steps is not None:
                form.steps = [FormStep.model_validate(s) for s in payload.steps]
            if payload.settings is not None:
                form.settings = FormSettings.model_validate(payload.settings)
            if payload.theme is not None:
                form.theme = FormTheme.model_validate(payload.theme)
            if payload.status is not None:
                form.status = FormStatus(payload.status)
            if payload.tags is not None:
                form.tags = payload.tags

            saved = await self._store.save_form(form)
            return ok(result={"form": saved.model_dump(mode="json")})
        except Exception:
            logger.exception("Erreur mise à jour formulaire IPC")
            return error("Erreur lors de la mise à jour.", "update_error", result={"form_id": payload.form_id})

    @action("delete_form")
    @schema(
        version="1.0",
        input={"form_id": (str, ...)},
        output={"msg": str, "form_id": str},
        type_response="model",
        unset=False,
    )
    async def ipc_delete_form(self, payload) -> dict:
        deleted = await self._store.delete_form(payload.form_id, storage=getattr(self, "_storage", None))
        if not deleted:
            return error("Formulaire introuvable.", "not_found", result={"form_id": payload.form_id})
        return ok(result={"msg": "Formulaire supprimé", "form_id": payload.form_id})

    @action("get_form")
    @schema(
        version="1.0",
        input={
            "form_id": (Optional[str], None),
            "slug": (Optional[str], None),
        },
        output={"form": dict},
        type_response="model",
        unset=False,
    )
    async def ipc_get_form(self, payload) -> dict:
        form = None
        if payload.form_id:
            form = await self._store.get_form(payload.form_id)
        elif payload.slug:
            form = await self._store.get_form_by_slug(payload.slug)
        if not form:
            return error("Formulaire introuvable.", "not_found")
        return ok(result={"form": form.model_dump(mode="json")})

    @action("list_forms")
    @schema(
        version="1.0",
        input={
            "owner_id": (Optional[str], None),
            "status": (Optional[str], None),
            "tags": (Optional[list], None),
            "limit": (int, 50),
            "offset": (int, 0),
        },
        output={"forms": list},
        type_response="model",
        unset=False,
    )
    async def ipc_list_forms(self, payload) -> dict:
        forms = await self._store.list_forms(
            owner_id=payload.owner_id,
            status=payload.status,
            tags=payload.tags,
            limit=payload.limit,
            offset=payload.offset,
        )
        return ok(result={"forms": [f.model_dump(mode="json") for f in forms]})

    @action("submit")
    @schema(
        version="1.0",
        input={
            "slug": (str, ...),
            "data": (dict, ...),
            "meta": (dict, {}),
        },
        output={"submission_id": str, "message": Optional[str], "redirect_url": Optional[str]},
        type_response="model",
        unset=False,
    )
    async def ipc_submit(self, payload) -> dict:
        """Soumet une réponse et déclenche le pipeline."""
        form = await self._store.get_form_by_slug(payload.slug)
        if not form:
            return error("Formulaire introuvable.", "not_found", result={"slug": payload.slug})
        if form.status != FormStatus.ACTIVE:
            return error("Ce formulaire n'accepte plus de réponses.", "form_closed", result={"current_status": form.status})

        now = datetime.now(timezone.utc)
        if form.settings.close_after and now > form.settings.close_after:
            return error("Ce formulaire a expiré.", "form_closed", result={"close_after": str(form.settings.close_after)})

        if form.settings.max_submissions:
            count = await self._store.count_submissions(form.id)
            if count >= form.settings.max_submissions:
                return error("Ce formulaire a atteint sa limite de réponses.", "form_full", result={"max": form.settings.max_submissions})

        if form.settings.one_submission_per_user:
            user_id = (payload.meta or {}).get("user_id")
            if user_id:
                existing = await self._store.find_submission_by_user(form.id, user_id)
                if existing:
                    return error("Vous avez déjà répondu à ce formulaire.", "already_submitted")

        valid, errors = self._validator.validate(form, payload.data)
        if not valid:
            return error("Données invalides.", "validation_error", errors=errors, result={"validation_errors": errors})

        submission = FormSubmission(
            form_id=form.id,
            data=payload.data,
            meta=SubmissionMeta.model_validate(payload.meta or {}),
        )
        saved = await self._store.save_submission(submission)
        self._bg_task(self._pipeline.run(form, saved))

        return ok(result={
            "submission_id": saved.id,
            "message": form.settings.confirmation_message,
            "redirect_url": form.settings.redirect_url,
        })

    @action("get_submission")
    @schema(
        version="1.0",
        input={"submission_id": (str, ...)},
        output={"submission": dict},
        type_response="model",
        unset=False,
    )
    async def ipc_get_submission(self, payload) -> dict:
        sub = await self._store.get_submission(payload.submission_id)
        if not sub:
            return error("Soumission introuvable.", "not_found")
        return ok(result={"submission": sub.model_dump(mode="json")})

    @action("list_submissions")
    @schema(
        version="1.0",
        input={
            "form_id": (str, ...),
            "status": (Optional[str], None),
            "limit": (int, 50),
            "offset": (int, 0),
        },
        output={"submissions": list},
        type_response="model",
        unset=False,
    )
    async def ipc_list_submissions(self, payload) -> dict:
        subs = await self._store.list_submissions(
            form_id=payload.form_id,
            status=payload.status,
            limit=payload.limit,
            offset=payload.offset,
        )
        return ok(result={"submissions": [s.model_dump(mode="json") for s in subs]})

    @action("export")
    @schema(
        version="1.0",
        input={
            "form_id": (str, ...),
            "format": (str, "json"),
        },
        output={"data": Any, "format": str},
        type_response="model",
        unset=False,
    )
    async def ipc_export(self, payload) -> dict:
        form = await self._store.get_form(payload.form_id)
        if not form:
            return error("Formulaire introuvable.", "not_found")
        subs = await self._store.list_submissions(payload.form_id, limit=10000)
        fmt = payload.format
        if fmt == "json":
            return ok(result={"data": self._exporter.export_json(form, subs), "format": "json"})
        elif fmt == "csv":
            return ok(result={"data": self._exporter.export_csv(form, subs), "format": "csv"})
        return error(
            f"Format '{fmt}' non supporté en IPC. Utilisez la route HTTP /export.",
            "unsupported_format",
        )

    @action("analytics")
    @schema(
        version="1.0",
        input={"form_id": (str, ...)},
        output={"total_views": int, "total_submissions": int, "completion_rate": float},
        type_response="model",
        unset=False,
    )
    async def ipc_analytics(self, payload) -> dict:
        analytics = await self._store.get_analytics(payload.form_id)
        return ok(result=analytics.model_dump())

    @action("track_view")
    @schema(
        version="1.0",
        input={
            "form_id": (str, ...),
            "ip": (str, ""),
            "user_agent": (str, ""),
        },
        output={"msg": str},
        type_response="model",
        unset=False,
    )
    async def ipc_track_view(self, payload) -> dict:
        await self._store.track_view(
            form_id=payload.form_id,
            ip=payload.ip,
            user_agent=payload.user_agent,
        )
        return ok(result={"msg": "Vue trackée"})

    @action("xflow.integration")
    @schema(
        version="1.0",
        input={},
        output={"actions": list, "events": list},
        type_response="model",
        unset=False,
    )
    async def ipc_xflow_integration(self, payload) -> dict:
        """Retourne le contrat d'intégration xform ↔ xflow."""
        import json
        from pathlib import Path
        path = Path(__file__).parent.parent / "data" / "xflow_integration.json"
        if not path.exists():
            return ok(result={
                "plugin": "xform",
                "version": "1.0",
                "actions": [
                    {"name": "submit", "description": "Soumettre une réponse à un formulaire"},
                    {"name": "get_form", "description": "Récupérer la définition d'un formulaire"},
                    {"name": "get_submission", "description": "Récupérer une soumission"},
                    {"name": "list_submissions", "description": "Lister les soumissions d'un formulaire"},
                ],
                "events": [],
            })
        try:
            with open(path, "r", encoding="utf-8") as f:
                return ok(data=json.load(f))
        except Exception as exc:
            logger.error("Erreur lecture xflow_integration.json: %s", exc)
            return error(f"Erreur lecture intégration: {exc}", "integration_error")
