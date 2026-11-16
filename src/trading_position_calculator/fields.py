"""Description des paramètres saisis et validation des entrées du formulaire.

Ce module est la source unique des champs : il sert à la fois à afficher le
formulaire (``app.py``) et à convertir la saisie en objet ``Parameters``.

Unités : chaque champ est exprimé dans l'unité d'origine du modèle. Les
pourcentages sont donc saisis en fraction décimale (0.01 = 1 %).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .calculations import Parameters


@dataclass(frozen=True)
class Field:
    """Un champ du formulaire."""

    name: str
    label: str
    unit: str
    default: str
    step: str
    minimum: float
    minimum_inclusive: bool
    maximum: float | None = None
    maximum_label: str | None = None


FIELDS: tuple[Field, ...] = (
    Field("capital", "Capital du compte", "$", "181.31", "0.01", 0, False),
    Field(
        "risk_per_trade",
        "Risque par trade",
        "fraction (0.01 = 1 %)",
        "0.01",
        "0.0001",
        0,
        True,
        1,
        "1 (100 %)",
    ),
    Field("pip_size", "Taille d'un pip (en prix)", "en prix", "0.0001", "0.0001", 0, False),
    Field("contracts_per_lot", "Contrats par lots", "contrats", "100000", "1", 0, False),
    Field("leverage", "Levier", "x", "30", "1", 0, False),
    Field("current_price", "Cours actuel", "prix", "1.1055", "0.0001", 0, False),
    Field("entry_distance_pips", "Distance d'entrée (pips)", "pips", "2", "0.1", 0, True),
    Field("stop_loss_pips", "Stop loss (pips)", "pips", "1", "0.1", 0, True),
    Field("target_rr", "Ratio R/R visé", "x", "2", "0.1", 0, True),
    Field("spread_pips", "Spread à l'annonce (pips)", "pips", "1.2", "0.1", 0, True),
    Field("slippage_pips", "Slippage prévu (pips)", "pips", "1", "0.1", 0, True),
    Field("lot_step", "Lot minimum / pas", "lots", "0.01", "0.01", 0, False),
    Field("free_margin", "Marge disponible", "$", "130", "0.01", 0, True),
)

FIELDS_BY_NAME = {f.name: f for f in FIELDS}


class ValidationError(Exception):
    """Saisie invalide : ``errors`` contient les messages, en français.

    ``field_errors`` associe chaque champ aux messages qui le concernent,
    pour que la page puisse les afficher à côté de la saisie.
    """

    def __init__(self, errors: list[str], field_errors: dict[str, list[str]] | None = None) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.field_errors = field_errors or {}


#: Séparateurs de milliers acceptés à la saisie, comme ceux affichés par l'app.
THOUSANDS_SEPARATORS = ("\u00a0", "\u202f", "\u2009", " ")


def _check(field: Field, raw: str) -> float:
    text = (raw or "").strip()
    for separator in THOUSANDS_SEPARATORS:
        text = text.replace(separator, "")
    if not text:
        raise ValidationError([f"{field.label} : champ obligatoire."])
    if "_" in text:
        raise ValidationError([f"{field.label} : doit être un nombre."])
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        raise ValidationError([f"{field.label} : doit être un nombre."]) from None

    if not math.isfinite(value):
        raise ValidationError([f"{field.label} : doit être un nombre fini."])

    if field.minimum_inclusive:
        too_small = value < field.minimum
    else:
        too_small = value <= field.minimum
    if too_small:
        if field.minimum == 0:
            raise ValidationError([f"{field.label} : doit être strictement positif."])
        raise ValidationError([f"{field.label} : doit être supérieur ou égal à {field.minimum}."])
    if field.maximum is not None and value > field.maximum:
        bound = field.maximum_label or str(field.maximum)
        raise ValidationError([f"{field.label} : ne doit pas dépasser {bound}."])
    return value


def parse_parameters(data: Mapping[str, object]) -> Parameters:
    """Convertit une saisie brute en ``Parameters``.

    Lève ``ValidationError`` avec la liste des messages si un ou plusieurs
    champs sont invalides.
    """
    values: dict[str, float] = {}
    errors: list[str] = []
    field_errors: dict[str, list[str]] = {}
    for field in FIELDS:
        try:
            values[field.name] = _check(field, str(data.get(field.name, "")))
        except ValidationError as exc:
            errors.extend(exc.errors)
            field_errors.setdefault(field.name, []).extend(exc.errors)
    if not errors:
        combination_errors, combination_fields = _check_combinations(values)
        errors.extend(combination_errors)
        for name, message in combination_fields:
            field_errors.setdefault(name, []).append(message)
    if errors:
        raise ValidationError(errors, field_errors)
    return Parameters(**values)


def _check_combinations(values: dict[str, float]) -> tuple[list[str], list[tuple[str, str]]]:
    """Règles de cohérence portant sur plusieurs champs à la fois.

    Ces deux combinaisons rendent un calcul indéfini (division par zéro) ;
    elles sont refusées plutôt que d'inventer un comportement non défini.
    """
    errors: list[str] = []
    owners: list[tuple[str, str]] = []

    risk_per_pip = values["stop_loss_pips"] + values["spread_pips"] + values["slippage_pips"]
    if risk_per_pip == 0:
        message = (
            "Stop loss, spread et slippage ne peuvent pas être tous nuls : le volume "
            "maximal lié au risque est une division par zéro."
        )
        errors.append(message)
        owners.append(("stop_loss_pips", message))

    if values["free_margin"] > values["capital"]:
        message = (
            f"La marge disponible ({values['free_margin']:g}) ne peut pas dépasser le "
            f"capital du compte ({values['capital']:g}) : le capital est le montant total "
            "de trésorerie disponible."
        )
        errors.append(message)
        owners.append(("free_margin", message))

    entry_price = values["current_price"] - values["entry_distance_pips"] * values["pip_size"]
    if entry_price <= 0:
        message = (
            "Le prix d'entrée de vente doit rester strictement positif : réduisez la "
            "distance d'entrée ou la taille du pip, ou augmentez le cours actuel."
        )
        errors.append(message)
        owners.append(("entry_distance_pips", message))

    return errors, owners


def default_values() -> dict[str, str]:
    """Valeurs par défaut du formulaire."""
    return {f.name: f.default for f in FIELDS}
