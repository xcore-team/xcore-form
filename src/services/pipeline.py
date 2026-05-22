"""
Pipeline XForm — automatisation post-soumission.
Déclenche XFlow, XPulse, XDesk selon les settings du formulaire.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from ..schemas.form import FormDefinition, FormSubmission, PipelineLogEntry, SubmissionStatus
from ..repositories.store import XFormStore

logger = logging.getLogger("xform.pipeline")


class XFormPipeline:
    """
    Pipeline exécuté après chaque soumission.

    Étapes :
      1. email_confirmation  → envoie un email au soumetteur (si configuré)
      2. notify_owner        → notifie le propriétaire via XPulse
      3. xflow_trigger       → déclenche un workflow XFlow
      4. xdesk_ticket        → crée un ticket XDesk
    """

    def __init__(
        self,
        store: XFormStore,
        call_plugin: Callable,  # self.call_plugin du plugin parent
        events: Any,            # self.ctx.events
    ) -> None:
        self._store = store
        self._call = call_plugin
        self._events = events

    async def run(
        self, form: FormDefinition, submission: FormSubmission
    ) -> None:
        """Exécute le pipeline complet pour une soumission."""
        logger.info(
            "Pipeline XForm — form=%s submission=%s", form.id, submission.id
        )
        settings = form.settings

        await self._step(
            submission, form.id, "email_confirmation",
            self._send_confirmation, form, submission,
            enabled=settings.confirmation_email,
        )
        await self._step(
            submission, form.id, "notify_owner",
            self._notify_owner, form, submission,
            enabled=settings.notify_owner,
        )
        await self._step(
            submission, form.id, "xflow_trigger",
            self._trigger_workflow, form, submission,
            enabled=bool(settings.workflow_id),
        )
        await self._step(
            submission, form.id, "xdesk_ticket",
            self._create_ticket, form, submission,
            enabled=settings.create_ticket,
        )

        # Marquer la soumission comme traitée
        await self._store.update_submission_status(
            submission.id, SubmissionStatus.PROCESSED
        )
        logger.info("Pipeline terminé — submission=%s", submission.id)

    # ─────────────────────────────────────────────────────────
    # Étapes
    # ─────────────────────────────────────────────────────────

    async def _send_confirmation(
        self, form: FormDefinition, submission: FormSubmission
    ) -> None:
        """Envoie un email de confirmation au soumetteur."""
        email = self._find_email(form, submission)
        if not email:
            logger.debug("[xform] Pas d'email trouvé dans la soumission, skip confirmation.")
            return

        await self._events.emit("xform.send_email", {
            "to": email,
            "subject": f"Confirmation — {form.title}",
            "html": f"""
                <h2>Merci pour votre soumission !</h2>
                <p>{form.settings.confirmation_message}</p>
                <p><small>Formulaire : {form.title}</small></p>
            """,
            "context": {
                "form_id": form.id,
                "form_title": form.title,
                "submission_id": submission.id,
            },
        })

    async def _notify_owner(
        self, form: FormDefinition, submission: FormSubmission
    ) -> None:
        """Notifie le propriétaire du formulaire via XPulse."""
        try:
            await self._call("xpulse", "xpulse.stream", {
                "channel": f"xform.{form.owner_id}",
                "event": {
                    "user_id": form.owner_id,
                    "type": "new_submission",
                    "title": f"Nouvelle réponse — {form.title}",
                    "form_id": form.id,
                    "submission_id": submission.id,
                },
            })
        except Exception as e:
            logger.warning("[xform] XPulse non disponible : %s", e)
            # Fallback : émettre sur le bus d'événements
            await self._events.emit("xform.new_submission", {
                "form_id": form.id,
                "form_title": form.title,
                "owner_id": form.owner_id,
                "submission_id": submission.id,
                "submission_data": submission.data,
            })

    async def _trigger_workflow(
        self, form: FormDefinition, submission: FormSubmission
    ) -> None:
        """Déclenche un workflow XFlow avec les données de la soumission."""
        try:
            result = await self._call("xflow", "run", {
                "workflow_name": form.settings.workflow_id,
                "payload": {
                    "form_id": form.id,
                    "form_title": form.title,
                    "form_slug": form.slug,
                    "submission_id": submission.id,
                    "submission_data": submission.data,
                    "submitted_at": submission.created_at.isoformat() if submission.created_at else None,
                    "owner_id": form.owner_id,
                },
            })
            logger.info(
                "[xform] XFlow déclenché — workflow=%s run=%s",
                form.settings.workflow_id,
                result.get("run_id"),
            )
        except Exception as e:
            logger.error("[xform] Erreur déclenchement XFlow : %s", e)
            raise

    async def _create_ticket(
        self, form: FormDefinition, submission: FormSubmission
    ) -> None:
        """Crée un ticket XDesk à partir de la soumission."""
        try:
            await self._call("xdesk", "create_ticket", {
                "title": f"[{form.title}] Nouvelle soumission",
                "body": self._format_submission_text(form, submission),
                "assignee": form.settings.ticket_assignee,
                "metadata": {
                    "form_id": form.id,
                    "submission_id": submission.id,
                },
            })
        except Exception as e:
            logger.warning("[xform] XDesk non disponible : %s", e)
            raise

    # ─────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────

    async def _step(
        self,
        submission: FormSubmission,
        form_id: str,
        step_name: str,
        fn: Any,
        *args,
        enabled: bool = True,
    ) -> None:
        """Exécute une étape et enregistre le résultat dans les logs."""
        if not enabled:
            await self._store.log_pipeline_step(PipelineLogEntry(
                submission_id=submission.id,
                form_id=form_id,
                step=step_name,
                status="skipped",
            ))
            return

        try:
            await fn(*args)
            await self._store.log_pipeline_step(PipelineLogEntry(
                submission_id=submission.id,
                form_id=form_id,
                step=step_name,
                status="success",
            ))
        except Exception as e:
            logger.error("[xform] Étape '%s' échouée : %s", step_name, e)
            await self._store.log_pipeline_step(PipelineLogEntry(
                submission_id=submission.id,
                form_id=form_id,
                step=step_name,
                status="failed",
                error=str(e),
            ))
            # On ne bloque pas le pipeline sur une étape optionnelle

    @staticmethod
    def _find_email(form: FormDefinition, submission: FormSubmission) -> Optional[str]:
        """Trouve le premier champ email dans les données soumises."""
        for field in form.fields:
            if field.type.value == "email":
                value = submission.data.get(field.name) or submission.data.get(field.id)
                if value and "@" in str(value):
                    return str(value)
        return None

    @staticmethod
    def _format_submission_text(
        form: FormDefinition, submission: FormSubmission
    ) -> str:
        """Formate les données en texte lisible pour le ticket."""
        lines = [f"Formulaire : {form.title}", f"ID : {submission.id}", "---"]
        for field in form.fields:
            value = submission.data.get(field.name) or submission.data.get(field.id, "")
            lines.append(f"{field.label} : {value}")
        return "\n".join(lines)