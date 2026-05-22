import asyncio

from pydantic import BaseModel
from xcore.sdk import AutoDispatchMixin, action, ok, error
from .schemas.form import FormStatus
from typing import Dict, Any


class IPCCommands(AutoDispatchMixin):

    @action("create_form")
    async def ipc_create_form(self, payload: Dict[str, Any]) -> dict:
        try:
            form = await self._build_form_from_payload(payload, payload["owner_id"])
            form.status = FormStatus.ACTIVE
            saved = await self._store.save_form(form)
            return ok(
                result={"form": saved.model_dump(mode="json")}
            )
        except Exception as e:
            logger.exception("Erreur création formulaire")
            return error("Erreur lors de la création du formulaire.", "create_error", result={"error": "Erreur lors de la création du formulaire"})

    @action("update_form")
    async def ipc_update_form(self, payload: Dict[str, Any]) -> dict:
        form = await self._store.get_form(payload["form_id"])
        if not form:
            return error("Formulaire introuvable.", "not_found")
        try:
            if payload.get("title"):
                form.title = payload["title"]
            if payload.get("description") is not None:
                form.description = payload["description"]
            if payload.get("fields") is not None:
                form.fields = [FormField.model_validate(f) for f in payload["fields"]]
            if payload.get("steps") is not None:
                form.steps = [FormStep.model_validate(s) for s in payload["steps"]]
            if payload.get("settings") is not None:
                form.settings = FormSettings.model_validate(payload["settings"])
            if payload.get("theme") is not None:
                form.theme = FormTheme.model_validate(payload["theme"])
            if payload.get("status"):
                form.status = FormStatus(payload["status"])
            if payload.get("tags") is not None:
                form.tags = payload["tags"]

            saved = await self._store.save_form(form)
            return ok(result={"form": saved.model_dump(mode="json")})
        except Exception as e:
            return error("erreur", "update_error", result={"form_id": payload.get("form_id")})

    @action("delete_form")
    async def ipc_delete_form(self, payload: Dict[str, Any]) -> dict:
        form_id = payload.get("form_id")
        if not form_id:
            return error("form_id requis.", "missing_param")
        deleted = await self._store.delete_form(form_id)
        if not deleted:
            return error("Formulaire introuvable.", "not_found", result={"form_id": form_id})
        return ok(
            result={
                "msg": "Formulaire supprimé",
                "form_id": form_id
            }
        )

    @action("get_form")
    async def ipc_get_form(self, payload: Dict[str, Any]) -> dict:
        form = None
        if payload.get("form_id"):
            form = await self._store.get_form(payload["form_id"])
        elif payload.get("slug"):
            form = await self._store.get_form_by_slug(payload["slug"])
        if not form:
            return error("Formulaire introuvable.", "not_found", result={"search_params": {"form_id": payload.get("form_id"), "slug": payload.get("slug")}})
        return ok(result={"form": form.model_dump(mode="json")})

    @action("list_forms")
    async def ipc_list_forms(self, payload: Dict[str, Any]) -> dict:
        forms = await self._store.list_forms(
            owner_id=payload.get("owner_id"),
            status=payload.get("status"),
            tags=payload.get("tags"),
            limit=int(payload.get("limit", 50)),
            offset=int(payload.get("offset", 0)),
        )
        return ok(
            result={
                "forms": [f.model_dump(mode="json") for f in forms],
            }
        )

    @action("submit")
    async def ipc_submit(self, payload: Dict[str, Any]) -> dict:
        """Soumet une réponse et déclenche le pipeline."""
        form = await self._store.get_form_by_slug(payload["slug"])
        if not form:
            return error("Formulaire introuvable.", "not_found", result={"slug": payload["slug"]})
        if form.status != FormStatus.ACTIVE:
            return error("Ce formulaire n'accepte plus de réponses.", "form_closed", result={"current_status": form.status})

        # Validation
        valid, errors = self._validator.validate(form, payload["data"])
        if not valid:
            return error("Données invalides.", "validation_error", errors=errors, result={"validation_errors": errors})

        # Sauvegarde
        from .schemas.form import FormSubmission, SubmissionMeta
        submission = FormSubmission(
            form_id=form.id,
            data=payload["data"],
            meta=SubmissionMeta.model_validate(payload.get("meta") or {}),
        )
        saved = await self._store.save_submission(submission)

        # Pipeline async (ne bloque pas la réponse)
        asyncio.create_task(self._pipeline.run(form, saved))

        return ok(
            result={
            "submission_id": saved.id,
            "message": form.settings.confirmation_message,
            "redirect_url": form.settings.redirect_url,
            }
        )

    @action("get_submission")
    async def ipc_get_submission(self, payload: Dict[str, Any]) -> dict:
        sub_id = payload.get("submission_id")
        if not sub_id:
            return error("submission_id requis.", "missing_param")
        sub = await self._store.get_submission(sub_id)
        if not sub:
            return error("Soumission introuvable.", "not_found")
        return ok(
            result={
                "submission": sub.model_dump(mode="json"),
            }
        )

    @action("list_submissions")
    async def ipc_list_submissions(self, payload: Dict[str, Any]) -> dict:
        subs = await self._store.list_submissions(
            form_id=payload["form_id"],
            status=payload.get("status"),
            limit=int(payload.get("limit", 50)),
            offset=int(payload.get("offset", 0)),
        )
        return ok(
            result={
                "submissions": [s.model_dump(mode="json") for s in subs],
            }
        )

    @action("export")
    async def ipc_export(self, payload: Dict[str, Any]) -> dict:
        form = await self._store.get_form(payload["form_id"])
        if not form:
            return error("Formulaire introuvable.", "not_found")
        subs = await self._store.list_submissions(payload["form_id"], limit=10000)
        fmt = payload.get("format", "json")
        if fmt == "json":
            return ok(
                result={
                    "data": self._exporter.export_json(form, subs),
                    "format": "json"
                }
            )
        elif fmt == "csv":
            return ok(
                result={
                    "data": self._exporter.export_csv(form, subs),
                    "format": "csv"
                }
            )
        return error(
            f"Format '{fmt}' non supporté en IPC. Utilisez la route HTTP /export.", 
            "unsupported_format",
            result={"msg": f"Format '{fmt}' non supporté en IPC. Utilisez la route HTTP /export."}
            )

    @action("analytics")
    async def ipc_analytics(self, payload: Dict[str, Any]) -> dict:
        form_id = payload.get("form_id")
        if not form_id:
            return error("form_id requis.", "missing_param",result={"msg": "form_id requis."})
        
        analytics:BaseModel = await self._store.get_analytics(form_id)
        return ok(result=analytics.model_dump())

    @action("track_view")
    async def ipc_track_view(self, payload: Dict[str, Any]) -> dict:
        await self._store.track_view(
            form_id=payload.get("form_id", ""),
            ip=payload.get("ip", ""),
            user_agent=payload.get("user_agent", ""),
        )
        return ok(
            result={
                "msg": "Vue trackée"
            }
        )

    @action("xflow.integration")
    async def ipc_xflow_integration(self, *args, **kwargs) -> dict:
        """Lit le contrat d'intégration depuis le fichier JSON."""
        import json
        from pathlib import Path
        path = Path(__file__).parent.parent / "data" / "xflow_integration.json"
        try:
            with open(path, "r", encoding="utf-8") as f:
                return ok(
                    data=json.load(f)
                )
        except Exception as e:
            return error(f"Erreur lecture xflow_integration.json: {e}", "integration_error")
