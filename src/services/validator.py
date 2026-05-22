"""
Validation des soumissions XForm selon les règles déclarées sur les champs.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from ..schemas.form import FieldType, FormDefinition, FormField, LogicOperator


class ValidationError(Exception):
    def __init__(self, errors: List[Dict[str, str]]) -> None:
        self.errors = errors
        super().__init__(str(errors))


class XFormValidator:
    """Valide les données soumises contre la définition du formulaire."""

    def validate(
        self, form: FormDefinition, data: Dict[str, Any]
    ) -> Tuple[bool, List[Dict[str, str]]]:
        errors: List[Dict[str, str]] = []

        for field in form.fields:
            # Ignorer les champs layout (section, divider)
            if field.type in (FieldType.SECTION, FieldType.DIVIDER, FieldType.HIDDEN):
                continue

            # Vérifier si le champ est visible (logique conditionnelle)
            if not self._is_visible(field, data, form):
                continue

            value = data.get(field.name) or data.get(field.id)
            field_errors = self._validate_field(field, value)
            errors.extend(field_errors)

        return len(errors) == 0, errors

    def _is_visible(
        self, field: FormField, data: Dict[str, Any], form: FormDefinition
    ) -> bool:
        """Évalue la logique conditionnelle pour déterminer la visibilité."""
        if not field.logic or not field.logic.rules:
            return True

        results = []
        for rule in field.logic.rules:
            # Trouver le champ référencé
            ref_field = form.get_field(rule.field_id)
            if not ref_field:
                continue
            ref_value = data.get(ref_field.name) or data.get(ref_field.id)
            results.append(self._evaluate_rule(rule.operator, ref_value, rule.value))

        if not results:
            return True

        # AND vs OR
        match = all(results) if field.logic.match_all else any(results)

        # Appliquer l'action
        if field.logic.rules[0].action == "show":
            return match
        else:  # hide
            return not match

    @staticmethod
    def _evaluate_rule(operator: LogicOperator, actual: Any, expected: Any) -> bool:
        if operator == LogicOperator.IS_EMPTY:
            return actual is None or str(actual).strip() == ""
        if operator == LogicOperator.NOT_EMPTY:
            return actual is not None and str(actual).strip() != ""

        actual_s = str(actual) if actual is not None else ""
        expected_s = str(expected) if expected is not None else ""

        if operator == LogicOperator.EQ:
            return actual_s == expected_s
        if operator == LogicOperator.NEQ:
            return actual_s != expected_s
        if operator == LogicOperator.CONTAINS:
            return expected_s.lower() in actual_s.lower()
        if operator == LogicOperator.GT:
            try:
                return float(actual_s) > float(expected_s)
            except (ValueError, TypeError):
                return False
        if operator == LogicOperator.GTE:
            try:
                return float(actual_s) >= float(expected_s)
            except (ValueError, TypeError):
                return False
        if operator == LogicOperator.LT:
            try:
                return float(actual_s) < float(expected_s)
            except (ValueError, TypeError):
                return False
        if operator == LogicOperator.LTE:
            try:
                return float(actual_s) <= float(expected_s)
            except (ValueError, TypeError):
                return False
        return False

    def _validate_field(
        self, field: FormField, value: Any
    ) -> List[Dict[str, str]]:
        errors = []
        rules = field.validation
        label = field.label

        # Vide ?
        is_empty = value is None or str(value).strip() == ""

        # Requis
        if rules.required and is_empty:
            errors.append({
                "field_id": field.id,
                "field_name": field.name,
                "message": rules.custom_msg or f"Le champ « {label} » est requis.",
            })
            return errors  # Pas besoin de valider plus si vide et requis

        if is_empty:
            return errors  # Optionnel et vide → OK

        value_str = str(value)

        # Email
        if field.type == FieldType.EMAIL:
            if not re.match(r"^[^@]+@[^@]+\.[^@]+$", value_str):
                errors.append({
                    "field_id": field.id,
                    "field_name": field.name,
                    "message": f"Le champ « {label} » doit être un email valide.",
                })

        # Longueur min
        if rules.min_length is not None and len(value_str) < rules.min_length:
            errors.append({
                "field_id": field.id,
                "field_name": field.name,
                "message": f"« {label} » doit faire au moins {rules.min_length} caractères.",
            })

        # Longueur max
        if rules.max_length is not None and len(value_str) > rules.max_length:
            errors.append({
                "field_id": field.id,
                "field_name": field.name,
                "message": f"« {label} » ne peut pas dépasser {rules.max_length} caractères.",
            })

        # Valeur numérique
        if field.type == FieldType.NUMBER:
            try:
                num = float(value_str)
                if rules.min_value is not None and num < rules.min_value:
                    errors.append({
                        "field_id": field.id,
                        "field_name": field.name,
                        "message": f"« {label} » doit être ≥ {rules.min_value}.",
                    })
                if rules.max_value is not None and num > rules.max_value:
                    errors.append({
                        "field_id": field.id,
                        "field_name": field.name,
                        "message": f"« {label} » doit être ≤ {rules.max_value}.",
                    })
            except (ValueError, TypeError):
                errors.append({
                    "field_id": field.id,
                    "field_name": field.name,
                    "message": f"« {label} » doit être un nombre.",
                })

        # Pattern regex custom
        if rules.pattern:
            if not re.match(rules.pattern, value_str):
                errors.append({
                    "field_id": field.id,
                    "field_name": field.name,
                    "message": rules.custom_msg or f"« {label} » a un format invalide.",
                })

        return errors