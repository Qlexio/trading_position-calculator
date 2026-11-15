"""Application web locale du calculateur de positions CFD.

Serveur FastAPI : une seule page, un formulaire de saisie et un tableau de
résultats. Aucune logique de calcul ici — tout passe par ``calculations.py``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from .calculations import Plan, compute_plan
from .fields import FIELDS_BY_NAME, ValidationError, default_values, parse_parameters

app = FastAPI(
    title="Calculateur de positions CFD",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

#: Sections du formulaire, regroupées par thème.
FORM_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Compte et risque", ("capital", "risk_per_trade", "free_margin")),
    ("Instrument", ("pip_size", "contracts_per_lot", "leverage")),
    (
        "Marché et exécution",
        (
            "current_price",
            "entry_distance_pips",
            "stop_loss_pips",
            "target_rr",
            "spread_pips",
            "slippage_pips",
            "lot_step",
        ),
    ),
)

#: Valeur affichée quand un résultat n'est pas défini.
EMPTY = "—"


def _swap(text: str) -> str:
    """Format français : espace insécable pour les milliers, virgule décimale."""
    return text.replace(",", "\u00a0").replace(".", ",")


def _number(value: float, decimals: int) -> str:
    """Formate un nombre en français, sans signe sur les zéros négatifs."""
    rounded = round(value, decimals)
    if rounded == 0:
        value = 0.0
    return _swap(f"{value:,.{decimals}f}")


def fmt_price(value: float | None) -> str:
    """Prix : 5 décimales."""
    if value is None:
        return EMPTY
    return _number(value, 5)


def fmt_money(value: float | None) -> str:
    """Montant en dollars : 2 décimales."""
    if value is None:
        return EMPTY
    text = _number(value, 2)
    return f"{text} $" if not text.startswith("-") else f"-{text[1:]} $"


def fmt_percent(value: float | None) -> str:
    """Pourcentage : la valeur reçue est une fraction (0.01 = 1 %)."""
    if value is None:
        return EMPTY
    scaled = value * 100
    text = _number(scaled, 2)
    return f"{text} %" if not text.startswith("-") else f"-{text[1:]} %"


def fmt_ratio(value: float | None) -> str:
    """Ratio, sans suffixe."""
    if value is None:
        return EMPTY
    text = _number(value, 2)
    return text if not text.startswith("-") else f"-{text[1:]}"


def fmt_quantity(value: float | None) -> str:
    """Quantité (volume, contrats) : sans zéros de fin, décimales conservées."""
    if value is None:
        return EMPTY
    text = f"{value:,.5f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return _swap(text)


def fmt_input(value: str) -> str:
    """Saisie affichée au format français (virgule décimale)."""
    return (value or "").strip().replace(".", ",")


def build_result_groups(plan: Plan) -> list[dict[str, object]]:
    """Tableaux de résultats, groupés par thème, achat et vente côte à côte."""
    buy, sell = plan.buy, plan.sell

    def row(label, buy_value, sell_value) -> dict[str, str]:
        return {"label": label, "buy": buy_value, "sell": sell_value}

    return [
        {
            "title": "Ordre et prix",
            "rows": [
                row("Sens", "Achat", "Vente"),
                row("Prix d'entrée", fmt_price(buy.entry_price), fmt_price(sell.entry_price)),
                row("Stop loss", fmt_price(buy.stop_loss), fmt_price(sell.stop_loss)),
                row("Take profit", fmt_price(buy.take_profit), fmt_price(sell.take_profit)),
                row("Point mort", fmt_price(buy.breakeven), fmt_price(sell.breakeven)),
                row(
                    "Stop loss (% de l'entrée)",
                    fmt_percent(buy.stop_loss_pct),
                    fmt_percent(sell.stop_loss_pct),
                ),
            ],
        },
        {
            "title": "Volume et exposition",
            "rows": [
                row("Volume (lots)", fmt_quantity(buy.volume), fmt_quantity(sell.volume)),
                row(
                    "Contrats exposés",
                    fmt_quantity(buy.contracts),
                    fmt_quantity(sell.contracts),
                ),
                row("Valeur du pip ($)", fmt_money(buy.pip_value), fmt_money(sell.pip_value)),
                row("Exposition ($)", fmt_money(buy.exposure), fmt_money(sell.exposure)),
                row(
                    "Marge requise ($)",
                    fmt_money(buy.required_margin),
                    fmt_money(sell.required_margin),
                ),
                row(
                    "Volume max selon le risque (lots)",
                    fmt_quantity(buy.max_volume_by_risk),
                    fmt_quantity(sell.max_volume_by_risk),
                ),
                row(
                    "Volume max selon la marge (lots)",
                    fmt_quantity(buy.max_volume_by_margin),
                    fmt_quantity(sell.max_volume_by_margin),
                ),
            ],
        },
        {
            "title": "Résultat et risque",
            "rows": [
                row(
                    "Risque théorique ($)",
                    fmt_money(buy.theoretical_risk),
                    fmt_money(sell.theoretical_risk),
                ),
                row(
                    "Risque réaliste ($)",
                    fmt_money(buy.realistic_risk),
                    fmt_money(sell.realistic_risk),
                ),
                row(
                    "Gain potentiel ($)",
                    fmt_money(buy.potential_gain),
                    fmt_money(sell.potential_gain),
                ),
                row("R/R réel", fmt_ratio(buy.real_rr), fmt_ratio(sell.real_rr)),
                row(
                    "Taux de réussite minimum",
                    fmt_percent(buy.min_win_rate),
                    fmt_percent(sell.min_win_rate),
                ),
                row("Contrôle du risque", buy.risk_check, sell.risk_check),
            ],
        },
    ]


def build_notes(plan: Plan) -> list[str]:
    """Explications affichées sous les résultats."""
    notes = ["Les montants de risque sont négatifs : ils représentent une perte."]
    if any(s.real_rr is not None and s.real_rr < 0 for s in plan.scenarios):
        notes.append(
            "R/R réel négatif : un taux de réussite minimum supérieur à 100 % signifie "
            "que l'espérance du trade est négative — aucun taux de réussite ne le rend "
            "rentable."
        )
    return notes


def _context(request, values, errors, plan=None):
    """Contexte de rendu commun aux deux routes."""
    return {
        "request": request,
        "groups": FORM_GROUPS,
        "fields": FIELDS_BY_NAME,
        "values": {name: fmt_input(value) for name, value in values.items()},
        "errors": errors,
        "plan": plan,
        "derived": (
            [
                ("Risque max autorisé ($)", fmt_money(plan.params.max_risk)),
                ("Valeur du pip pour 1 lot ($)", fmt_money(plan.params.pip_value_per_lot)),
            ]
            if plan
            else []
        ),
        "result_groups": build_result_groups(plan) if plan else [],
        "notes": build_notes(plan) if plan else [],
    }


@app.get("/")
async def index(request: Request):
    """Formulaire (valeurs par défaut) sans résultats."""
    return templates.TemplateResponse(
        request=request, name="index.html", context=_context(request, default_values(), [])
    )


@app.post("/")
async def calculate(request: Request):
    """Traite la saisie puis affiche les résultats."""
    form = await request.form()
    values = {name: str(form.get(name, "")) for name in FIELDS_BY_NAME}

    try:
        params = parse_parameters(values)
    except ValidationError as exc:
        return templates.TemplateResponse(
            request=request, name="index.html", context=_context(request, values, exc.errors)
        )

    plan = compute_plan(params)
    return templates.TemplateResponse(
        request=request, name="index.html", context=_context(request, values, [], plan)
    )
