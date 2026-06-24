"""
XFormStore — accès aux données XForm.
Même pattern que xflow/repositories/workflow.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Date, Float, cast, desc, func, select, text

from ..domain.orm import (
    XFormFileRecord,
    XFormPipelineLogRecord,
    XFormRecord,
    XFormSubmissionRecord,
    XFormViewRecord,
)
from ..domain.files import FileEntry
from ..domain.analytics import FormAnalytics
from ..domain.forms import FormDefinition, FormStatus
from ..domain.submissions import FormSubmission, PipelineLogEntry, SubmissionMeta, SubmissionStatus

logger = logging.getLogger("xform.store")

FORM_CACHE_TTL = 3600
ANALYTICS_CACHE_TTL = 300


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class XFormStore:
    def __init__(self, db: Any, cache: Any | None = None) -> None:
        self._db = db
        self._cache = cache

    # ─────────────────────────────────────────────────────────
    # Cache helpers
    # ─────────────────────────────────────────────────────────

    def _form_key(self, form_id: str) -> str:
        return f"xform:form:{form_id}"

    def _slug_key(self, slug: str) -> str:
        return f"xform:slug:{slug}"

    def _analytics_key(self, form_id: str) -> str:
        return f"xform:analytics:{form_id}"

    async def _cache_form(self, form: FormDefinition) -> None:
        if not self._cache:
            return
        data = form.model_dump(mode="json")
        await self._cache.set(self._form_key(form.id), data, ttl=FORM_CACHE_TTL)
        if form.slug:
            await self._cache.set(self._slug_key(form.slug), data, ttl=FORM_CACHE_TTL)

    async def _invalidate_form(self, form: FormDefinition) -> None:
        if not self._cache:
            return
        await self._cache.delete(self._form_key(form.id))
        if form.slug:
            await self._cache.delete(self._slug_key(form.slug))
        await self._cache.delete(self._analytics_key(form.id))

    # ─────────────────────────────────────────────────────────
    # Formulaires
    # ─────────────────────────────────────────────────────────

    async def save_form(self, form: FormDefinition) -> FormDefinition:
        now = _utcnow()
        async with self._db.session() as session:
            record = await session.get(XFormRecord, form.id)
            if record is None:
                form.created_at = now
                form.updated_at = now
                record = XFormRecord(
                    id=form.id,
                    title=form.title,
                    description=form.description,
                    slug=form.slug or form.id,
                    owner_id=form.owner_id,
                    tenant_id=form.tenant_id,
                    fields=[f.model_dump(mode="json") for f in form.fields],
                    steps=[s.model_dump(mode="json") for s in form.steps],
                    settings=form.settings.model_dump(mode="json"),
                    theme=form.theme.model_dump(mode="json"),
                    tags=form.tags,
                    status=form.status.value,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                form.updated_at = now
                record.title = form.title
                record.description = form.description
                record.slug = form.slug or record.slug
                record.fields = [f.model_dump(mode="json") for f in form.fields]
                record.steps = [s.model_dump(mode="json") for s in form.steps]
                record.settings = form.settings.model_dump(mode="json")
                record.theme = form.theme.model_dump(mode="json")
                record.tags = form.tags
                record.status = form.status.value
                record.updated_at = now
            await session.commit()

        await self._cache_form(form)
        logger.info("Form '%s' sauvegardé (slug=%s)", form.id, form.slug)
        return form

    async def get_form(self, form_id: str) -> Optional[FormDefinition]:
        # Cache first
        if self._cache:
            cached = await self._cache.get(self._form_key(form_id))
            if cached:
                return FormDefinition.model_validate(cached)

        async with self._db.session() as session:
            record = await session.get(XFormRecord, form_id)
            if record is None:
                return None
            form = self._record_to_form(record)

        await self._cache_form(form)
        return form

    async def get_form_by_slug(self, slug: str) -> Optional[FormDefinition]:
        # Cache first
        if self._cache:
            cached = await self._cache.get(self._slug_key(slug))
            if cached:
                return FormDefinition.model_validate(cached)

        async with self._db.session() as session:
            result = await session.execute(
                select(XFormRecord).where(XFormRecord.slug == slug)
            )
            record = result.scalar_one_or_none()
            if record is None:
                return None
            form = self._record_to_form(record)

        await self._cache_form(form)
        return form

    async def list_forms(
        self,
        owner_id: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[FormDefinition]:
        async with self._db.session() as session:
            stmt = select(XFormRecord).order_by(desc(XFormRecord.created_at))
            if owner_id:
                stmt = stmt.where(XFormRecord.owner_id == owner_id)
            if status:
                stmt = stmt.where(XFormRecord.status == status)
            # Tag filter must happen before pagination to avoid returning fewer
            # rows than requested; fetch without limit when filtering by tags.
            if not tags:
                stmt = stmt.limit(limit).offset(offset)
            result = await session.execute(stmt)
            records = result.scalars().all()

        forms = [self._record_to_form(r) for r in records]
        if tags:
            forms = [f for f in forms if any(t in f.tags for t in tags)]
            forms = forms[offset: offset + limit]
        return forms

    async def delete_form(self, form_id: str, storage=None) -> bool:
        form = await self.get_form(form_id)
        if not form:
            return False
        # Supprime les fichiers disque avant la row DB
        if storage is not None:
            await storage.delete_all_for_form(form_id)
        async with self._db.session() as session:
            record = await session.get(XFormRecord, form_id)
            if record:
                await session.delete(record)
                await session.commit()
        await self._invalidate_form(form)
        return True

    async def update_form_status(self, form_id: str, status: FormStatus) -> None:
        form = await self.get_form(form_id)
        if not form:
            return
        async with self._db.session() as session:
            record = await session.get(XFormRecord, form_id)
            if record:
                record.status = status.value
                record.updated_at = datetime.now(timezone.utc)
                await session.commit()
        await self._invalidate_form(form)

    async def slug_exists(self, slug: str, exclude_id: Optional[str] = None) -> bool:
        async with self._db.session() as session:
            stmt = select(XFormRecord.id).where(XFormRecord.slug == slug)
            if exclude_id:
                stmt = stmt.where(XFormRecord.id != exclude_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none() is not None

    # ─────────────────────────────────────────────────────────
    # Soumissions
    # ─────────────────────────────────────────────────────────

    async def save_submission(self, submission: FormSubmission) -> FormSubmission:
        now = _utcnow()
        submission.created_at = now
        async with self._db.session() as session:
            record = XFormSubmissionRecord(
                id=submission.id,
                form_id=submission.form_id,
                data=submission.data,
                meta=submission.meta.model_dump(mode="json"),
                status=submission.status.value,
                created_at=now,
            )
            session.add(record)
            await session.commit()
        return submission

    async def get_submission(self, submission_id: str) -> Optional[FormSubmission]:
        async with self._db.session() as session:
            record = await session.get(XFormSubmissionRecord, submission_id)
            if record is None:
                return None
            return self._record_to_submission(record)

    async def list_submissions_with_files(
        self,
        form_id: str,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        include_files: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Retourne les soumissions avec leurs fichiers imbriqués.
        Filtrable par user_id (meta.user_id), status, pageable.
        """
        async with self._db.session() as session:
            stmt = (
                select(XFormSubmissionRecord)
                .where(XFormSubmissionRecord.form_id == form_id)
                .order_by(desc(XFormSubmissionRecord.created_at))
            )
            if status:
                stmt = stmt.where(XFormSubmissionRecord.status == status)
            if user_id:
                stmt = stmt.where(
                    XFormSubmissionRecord.meta["user_id"].astext == user_id
                )
            stmt = stmt.limit(limit).offset(offset)
            result = await session.execute(stmt)
            sub_records = result.scalars().all()

            # Récupère tous les fichiers pour ces soumissions en une seule requête
            files_by_sub: Dict[str, List[FileEntry]] = {}
            if include_files and sub_records:
                sub_ids = [r.id for r in sub_records]
                files_result = await session.execute(
                    select(XFormFileRecord).where(
                        XFormFileRecord.submission_id.in_(sub_ids)
                    )
                )
                for frec in files_result.scalars().all():
                    files_by_sub.setdefault(frec.submission_id, []).append(
                        self._record_to_file(frec)
                    )

        rows = []
        for r in sub_records:
            sub = self._record_to_submission(r)
            entry = sub.model_dump(mode="json")
            if include_files:
                entry["files"] = [
                    f.model_dump(mode="json")
                    for f in files_by_sub.get(sub.id, [])
                ]
            rows.append(entry)
        return rows

    async def list_submissions(
        self,
        form_id: str,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[FormSubmission]:
        async with self._db.session() as session:
            stmt = (
                select(XFormSubmissionRecord)
                .where(XFormSubmissionRecord.form_id == form_id)
                .order_by(desc(XFormSubmissionRecord.created_at))
                .limit(limit)
                .offset(offset)
            )
            if status:
                stmt = stmt.where(XFormSubmissionRecord.status == status)
            result = await session.execute(stmt)
            records = result.scalars().all()
        return [self._record_to_submission(r) for r in records]

    async def count_submissions(self, form_id: str) -> int:
        async with self._db.session() as session:
            return await session.scalar(
                select(func.count(XFormSubmissionRecord.id)).where(
                    XFormSubmissionRecord.form_id == form_id
                )
            ) or 0

    async def find_submission_by_user(
        self, form_id: str, user_id: str
    ) -> Optional[FormSubmission]:
        async with self._db.session() as session:
            result = await session.execute(
                select(XFormSubmissionRecord)
                .where(XFormSubmissionRecord.form_id == form_id)
                .where(XFormSubmissionRecord.meta["user_id"].astext == user_id)
                .limit(1)
            )
            record = result.scalar_one_or_none()
            return self._record_to_submission(record) if record else None

    async def update_submission_status(
        self, submission_id: str, status: SubmissionStatus
    ) -> None:
        async with self._db.session() as session:
            record = await session.get(XFormSubmissionRecord, submission_id)
            if record:
                record.status = status.value
                await session.commit()

    # ─────────────────────────────────────────────────────────
    # Vues (analytics)
    # ─────────────────────────────────────────────────────────

    async def track_view(self, form_id: str, ip: str, user_agent: str) -> None:
        async with self._db.session() as session:
            view = XFormViewRecord(form_id=form_id, ip=ip, user_agent=user_agent)
            session.add(view)
            await session.commit()

    async def get_analytics(self, form_id: str) -> FormAnalytics:
        if self._cache:
            cached = await self._cache.get(self._analytics_key(form_id))
            if cached:
                return FormAnalytics.model_validate(cached)

        async with self._db.session() as session:
            views_count = await session.scalar(
                select(func.count(XFormViewRecord.id)).where(
                    XFormViewRecord.form_id == form_id
                )
            )
            subs_count = await session.scalar(
                select(func.count(XFormSubmissionRecord.id)).where(
                    XFormSubmissionRecord.form_id == form_id
                )
            )
            last_sub = await session.scalar(
                select(func.max(XFormSubmissionRecord.created_at)).where(
                    XFormSubmissionRecord.form_id == form_id
                )
            )
            # Submissions par jour (30 derniers jours)
            day_rows = await session.execute(
                select(
                    cast(XFormSubmissionRecord.created_at, Date).label("day"),
                    func.count().label("cnt"),
                )
                .where(XFormSubmissionRecord.form_id == form_id)
                .group_by(cast(XFormSubmissionRecord.created_at, Date))
                .order_by(cast(XFormSubmissionRecord.created_at, Date))
            )
            by_day = {str(row.day): row.cnt for row in day_rows}

            # Durée moyenne (stockée dans meta->duration_sec, JSON)
            try:
                avg_dur = await session.scalar(
                    select(
                        func.avg(
                            cast(XFormSubmissionRecord.meta["duration_sec"].astext, Float)
                        )
                    ).where(XFormSubmissionRecord.form_id == form_id)
                )
            except Exception:
                avg_dur = None

        total_views = views_count or 0
        total_subs = subs_count or 0
        rate = round(total_subs / total_views, 3) if total_views > 0 else 0.0

        analytics = FormAnalytics(
            form_id=form_id,
            total_views=total_views,
            total_submissions=total_subs,
            completion_rate=rate,
            last_submission=last_sub,
            submissions_by_day=by_day,
            avg_duration_sec=float(avg_dur) if avg_dur is not None else None,
        )
        if self._cache:
            await self._cache.set(
                self._analytics_key(form_id),
                analytics.model_dump(mode="json"),
                ttl=ANALYTICS_CACHE_TTL,
            )
        return analytics

    # ─────────────────────────────────────────────────────────
    # Fichiers uploadés
    # ─────────────────────────────────────────────────────────

    async def save_file_meta(self, entry: FileEntry) -> FileEntry:
        async with self._db.session() as session:
            record = XFormFileRecord(
                id=entry.file_id,
                form_id=entry.form_id,
                submission_id=entry.submission_id,
                field_name=entry.field_name,
                original_name=entry.original_name,
                stored_name=entry.stored_name,
                size_bytes=entry.size_bytes,
                mime_type=entry.mime_type,
                uploaded_at=entry.uploaded_at or _utcnow(),
            )
            session.add(record)
            await session.commit()
        return entry

    async def link_file_to_submission(self, file_id: str, submission_id: str) -> None:
        async with self._db.session() as session:
            record = await session.get(XFormFileRecord, file_id)
            if record:
                record.submission_id = submission_id
                await session.commit()

    async def list_files_for_submission(self, submission_id: str) -> list[FileEntry]:
        async with self._db.session() as session:
            result = await session.execute(
                select(XFormFileRecord).where(XFormFileRecord.submission_id == submission_id)
            )
            records = result.scalars().all()
        return [self._record_to_file(r) for r in records]

    async def list_files_for_form(self, form_id: str) -> list[FileEntry]:
        async with self._db.session() as session:
            result = await session.execute(
                select(XFormFileRecord).where(XFormFileRecord.form_id == form_id)
            )
            records = result.scalars().all()
        return [self._record_to_file(r) for r in records]

    async def list_orphan_files(self, form_id: str) -> list[FileEntry]:
        """Fichiers uploadés mais jamais liés à une soumission."""
        async with self._db.session() as session:
            result = await session.execute(
                select(XFormFileRecord)
                .where(XFormFileRecord.form_id == form_id)
                .where(XFormFileRecord.submission_id.is_(None))
            )
            records = result.scalars().all()
        return [self._record_to_file(r) for r in records]

    async def delete_file_meta(self, file_id: str) -> bool:
        async with self._db.session() as session:
            record = await session.get(XFormFileRecord, file_id)
            if not record:
                return False
            await session.delete(record)
            await session.commit()
        return True

    async def get_file_meta(self, file_id: str) -> FileEntry | None:
        async with self._db.session() as session:
            record = await session.get(XFormFileRecord, file_id)
            return self._record_to_file(record) if record else None

    @staticmethod
    def _record_to_file(record: XFormFileRecord) -> FileEntry:
        return FileEntry(
            file_id=record.id,
            form_id=record.form_id,
            submission_id=record.submission_id,
            field_name=record.field_name,
            original_name=record.original_name,
            stored_name=record.stored_name,
            size_bytes=record.size_bytes,
            mime_type=record.mime_type,
            uploaded_at=record.uploaded_at,
        )

    async def get_global_stats(self) -> dict:
        """Stats globales plateforme : nombre de forms par statut, soumissions, vues."""
        async with self._db.session() as session:
            forms_by_status_rows = await session.execute(
                select(XFormRecord.status, func.count().label("cnt"))
                .group_by(XFormRecord.status)
            )
            forms_by_status = {row.status: row.cnt for row in forms_by_status_rows}

            total_forms = sum(forms_by_status.values())
            total_submissions = await session.scalar(
                select(func.count(XFormSubmissionRecord.id))
            ) or 0
            total_views = await session.scalar(
                select(func.count(XFormViewRecord.id))
            ) or 0

        return {
            "total_forms": total_forms,
            "forms_by_status": forms_by_status,
            "total_submissions": total_submissions,
            "total_views": total_views,
        }

    # ─────────────────────────────────────────────────────────
    # Pipeline logs
    # ─────────────────────────────────────────────────────────

    async def log_pipeline_step(self, entry: PipelineLogEntry) -> None:
        async with self._db.session() as session:
            record = XFormPipelineLogRecord(
                submission_id=entry.submission_id,
                form_id=entry.form_id,
                step=entry.step,
                status=entry.status,
                payload=entry.payload,
                error=entry.error,
                executed_at=_utcnow(),
            )
            session.add(record)
            await session.commit()

    # ─────────────────────────────────────────────────────────
    # Helpers de conversion
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _record_to_form(record: XFormRecord) -> FormDefinition:
        from ..domain.forms import FormField, FormStep, FormSettings, FormTheme

        fields = [FormField.model_validate(f) for f in (record.fields or [])]
        steps = [FormStep.model_validate(s) for s in (record.steps or [])]

        return FormDefinition(
            id=record.id,
            title=record.title,
            description=record.description,
            slug=record.slug,
            owner_id=record.owner_id,
            tenant_id=record.tenant_id,
            fields=fields,
            steps=steps,
            settings=FormSettings.model_validate(record.settings or {}),
            theme=FormTheme.model_validate(record.theme or {}),
            tags=record.tags or [],
            status=FormStatus(record.status),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _record_to_submission(record: XFormSubmissionRecord) -> FormSubmission:
        return FormSubmission(
            id=record.id,
            form_id=record.form_id,
            data=record.data or {},
            meta=SubmissionMeta.model_validate(record.meta or {}),
            status=SubmissionStatus(record.status),
            created_at=record.created_at,
        )