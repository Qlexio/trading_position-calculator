"""Application web locale du calculateur de positions CFD.

Serveur FastAPI : une seule page, un formulaire de saisie et un tableau de
résultats. Aucune logique de calcul ici — tout passe par ``calculations.py``.
"""

from __future__ import annotations

from fractions import Fraction
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
                row("Contrôle du risque", buy.risk_check, sell.risk_check),
            ],
        },
    ]


def fmt_trade_fraction(value: float | None) -> str:
    """« 4 sur 5 », uniquement quand la fraction est exacte.

    Une fraction approchée pourrait se situer sous le seuil affiché et
    recommander un taux de réussite trop faible : on ne propose donc la
    fraction que si elle tombe exactement sur la valeur.
    """
    if value is None or value <= 0 or value > 1:
        return ""
    fraction = Fraction(value).limit_denominator(10)
    if abs(float(fraction) - value) > 1e-9:
        return ""
    return f"{fraction.numerator} sur {fraction.denominator}"


def describe_win_rate(scenario) -> str:
    """Décrit le taux de réussite minimum d'un scénario."""
    if scenario.min_win_rate is None:
        if scenario.realistic_risk == 0:
            return "non défini (volume nul)"
        if scenario.real_rr == -1:
            return "non défini (R/R réel = -1)"
        return "non défini"
    fraction = fmt_trade_fraction(scenario.min_win_rate)
    if fraction:
        return f"{fmt_percent(scenario.min_win_rate)} ({fraction} trades)"
    return f"au moins {fmt_percent(scenario.min_win_rate)} des trades"


def build_win_rate(plan: Plan) -> dict[str, str]:
    """Bloc « taux de réussite minimum », présenté hors du tableau de résultats."""
    buy, sell = plan.buy, plan.sell
    buy_text, sell_text = describe_win_rate(buy), describe_win_rate(sell)

    if buy_text == sell_text:
        detail = f"Il faut gagner {buy_text} pour ne pas perdre d'argent."
    else:
        detail = f"Il faut gagner {buy_text} pour l'achat, et {sell_text} pour la vente."

    return {
        "buy": fmt_percent(buy.min_win_rate),
        "sell": fmt_percent(sell.min_win_rate),
        "detail": detail,
        "explanation": (
            "Un taux élevé est exigeant : 80 %, c'est 4 trades gagnants sur 5 pour ne "
            "pas perdre d'argent. Un taux bas est confortable : 25 %, c'est 1 trade "
            "gagnant sur 4."
        ),
    }


def build_notes(plan: Plan) -> list[str]:
    """Explications affichées sous les résultats."""
    notes: list[str] = []
    if any(s.theoretical_risk < 0 or s.realistic_risk < 0 for s in plan.scenarios):
        notes.append("Les montants de risque sont négatifs : ils représentent une perte.")
    if any(s.real_rr is not None and s.real_rr < 0 for s in plan.scenarios):
        notes.append(
            "R/R réel négatif : le taux de réussite minimum dépasse 100 % — il est "
            "indéfini si le R/R réel vaut -1. L'espérance du trade est négative : aucun "
            "taux de réussite ne le rend rentable."
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
        "win_rate": build_win_rate(plan) if plan else None,
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
