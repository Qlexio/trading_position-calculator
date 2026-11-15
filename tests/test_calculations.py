"""Tests de la logique de calcul de ``trading_position_calculator.calculations``.

La référence est la modèle de référence du modèle ``reference.ods`` :

* les formules du modèle sont retranscrites dans ``_sheet_expectations``, qui
  sert d'oracle indépendant du module testé ;
* quand ``tests/reference.json`` est présent — extrait mécanique du modèle par
  ``tools/extract_reference.py`` — les valeurs calculées sont comparées
  directement aux valeurs mises en cache par le modèle ;
* cet extrait n'est pas versionné : les tests qui l'utilisent sont ignorés
  (``skip``) quand il est absent.

Aucun chiffre du modèle n'est recopié ici : les paramètres de référence
proviennent de ``fields.default_values()`` (valeurs par défaut du formulaire,
déjà présentes dans le dépôt) et sont vérifiés contre l'extrait du modèle dès
qu'il est disponible.

Les emplacements du modèle ne sont jamais écrits en dur : une ligne est
repérée par le libellé qu'elle porte, une colonne par l'intitulé de son
en-tête.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from pathlib import Path

import pytest

from trading_position_calculator.calculations import (
    BUY,
    RISK_CHECK_MARGIN,
    RISK_CHECK_OK,
    RISK_CHECK_RISK,
    RISK_CHECK_VOLUME,
    SELL,
    Parameters,
    compute_plan,
    compute_scenario,
    floor_to_step,
)
from trading_position_calculator.fields import default_values

REFERENCE_PATH = Path(__file__).with_name("reference.json")

#: Libellé de la ligne du modèle qui porte chaque paramètre.
PARAM_LABELS = {
    "capital": "Capital du compte",
    "risk_per_trade": "Risque par trade",
    "pip_size": "Taille d'un pip (en prix)",
    "contracts_per_lot": "Contrats par lots",
    "leverage": "Levier",
    "current_price": "Cours actuel",
    "entry_distance_pips": "Distance d'entrée (pips)",
    "stop_loss_pips": "Stop loss (pips)",
    "target_rr": "Ratio R/R visé",
    "spread_pips": "Spread à l'annonce (pips)",
    "slippage_pips": "Slippage prévu (pips)",
    "lot_step": "Lot minimum / pas",
    "free_margin": "Marge disponible",
}

#: Intitulé de la colonne du tableau de scénarios qui porte chaque attribut.
SCENARIO_HEADERS = {
    "entry_price": "Prix d'entrée",
    "stop_loss": "Stop loss",
    "take_profit": "Take profit",
    "breakeven": "Point mort",
    "volume": "Volume",
    "contracts": "Contrats exposés",
    "pip_value": "Valeur pip ($)",
    "exposure": "Exposition ($)",
    "required_margin": "Marge requise",
    "theoretical_risk": "Risque théorique ($)",
    "realistic_risk": "Risque réaliste ($)",
    "potential_gain": "Gain potentiel ($)",
    "real_rr": "R/R réel",
    "stop_loss_pct": "Stop loss (%)",
    "max_volume_by_risk": "Volume Risque",
    "max_volume_by_margin": "Volume marge",
}

#: Intitulé de la colonne qui porte le sens de l'ordre.
DIRECTION_HEADER = "Sens"

#: Intitulé de la colonne qui porte le contrôle de risque.
RISK_CHECK_HEADER = "Contrôle risque"

#: Libellé porté par la ligne d'en-têtes du tableau des scénarios.
ORDER_HEADER = "Ordre"

#: Libellé des deux lignes de scénarios et sens qu'elles décrivent.
SCENARIO_ROWS = {"Buy stop": BUY, "Sell stop": SELL}

#: Libellé de la ligne qui porte le risque maximal autorisé.
MAX_RISK_LABEL = "Risque max autorisé ($)"

#: Libellé de la ligne qui porte la valeur du pip pour un lot.
PIP_VALUE_LABEL = "Valeur du pip pour 1 lot ($)"

#: Attributs comparés à l'oracle (formules retranscrites).
COMPARED_FIELDS = tuple(SCENARIO_HEADERS) + ("risk_check",)

#: Citation d'une cellule dans une formule du modèle.
_CELL_REFERENCE = re.compile(r"\[\s*\.?\s*\$?(?P<column>[A-Z]{1,3})\$?(?P<row>\d+)\s*\]")


def _flatten(text: str) -> str:
    """Texte lisible et comparable : apostrophes ascii, espaces réduits."""
    return " ".join(text.replace("’", "'").split())


def _normalize(text: str) -> str:
    """Texte comparable : comme ``_flatten`` mais sans distinction de casse."""
    return _flatten(text).casefold()


def _column_index(name: str) -> int:
    """Rang d'une colonne dans le modèle (la colonne la plus à gauche vaut 0)."""
    index = 0
    for character in name:
        index = index * 26 + (ord(character) - ord("A") + 1)
    return index - 1


def _row_label(cells: dict[str, object]) -> str:
    """Libellé d'une ligne : le texte de sa colonne la plus à gauche."""
    texts = [(column, text) for column, text in cells.items() if isinstance(text, str)]
    if not texts:
        raise AssertionError("ligne sans libellé")
    return min(texts, key=lambda item: _column_index(item[0]))[1]


def _find_row(rows: dict[str, dict[str, object]], label: str) -> tuple[str, dict[str, object]]:
    """Ligne dont le libellé correspond, et sa clé dans l'extrait."""
    wanted = _normalize(label)
    matches = [
        (number, cells) for number, cells in rows.items() if _normalize(_row_label(cells)) == wanted
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"libellé « {label} » : {len(matches)} ligne(s) trouvée(s) dans le modèle"
        )
    return matches[0]


def _header_cells(rows: dict[str, dict[str, object]]) -> dict[str, object]:
    """Ligne d'en-têtes du tableau des scénarios."""
    return _find_row(rows, ORDER_HEADER)[1]


def _column_of(header_cells: dict[str, object], header: str) -> str:
    """Colonne de la ligne d'en-têtes qui porte l'intitulé demandé."""
    wanted = _normalize(header)
    matches = [
        column
        for column, cell in header_cells.items()
        if isinstance(cell, str) and _normalize(cell) == wanted
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"intitulé « {header} » : {len(matches)} colonne(s) trouvée(s) dans les en-têtes"
        )
    return matches[0]


def _cell(cells: dict[str, object], column: str) -> dict[str, object]:
    """Contenu de la cellule demandée."""
    cell = cells[column]
    assert isinstance(cell, dict), "la cellule ne porte ni valeur ni formule"
    return cell


def _value_column(cells: dict[str, object]) -> str:
    """Colonne qui porte la valeur numérique de la ligne."""
    columns = [
        column for column, cell in cells.items() if isinstance(cell, dict) and "value" in cell
    ]
    if len(columns) != 1:
        raise AssertionError("la ligne ne porte pas une valeur numérique unique")
    return columns[0]


def _row_value(rows: dict[str, dict[str, object]], label: str) -> float:
    """Valeur numérique portée par la ligne qui porte ce libellé."""
    cells = _find_row(rows, label)[1]
    return _cell(cells, _value_column(cells))["value"]


def _readable_formula(formula: str, rows: dict[str, dict[str, object]]) -> str:
    """Formule du modèle où chaque cellule citée est remplacée par son intitulé.

    Une cellule de la ligne de paramètres est désignée par le libellé de sa
    ligne, une cellule du tableau des scénarios par l'intitulé de sa colonne.
    """
    header_cells = _header_cells(rows)
    scenario_rows = {number for number, _ in (_find_row(rows, label) for label in SCENARIO_ROWS)}
    labels = {number: _flatten(_row_label(cells)) for number, cells in rows.items()}

    def substitute(match: re.Match[str]) -> str:
        column, number = match.group("column"), match.group("row")
        title = header_cells.get(column, "") if number in scenario_rows else labels.get(number, "")
        if not isinstance(title, str):
            title = ""
        return f"[{_flatten(title)}]"

    return _CELL_REFERENCE.sub(substitute, formula)


def _scenario_formula(rows: dict[str, dict[str, object]], row_label: str, header: str) -> str:
    """Formule lisible du modèle pour une colonne du tableau des scénarios."""
    column = _column_of(_header_cells(rows), header)
    cells = _find_row(rows, row_label)[1]
    return _readable_formula(_cell(cells, column)["formula"], rows)


def _row_formula(rows: dict[str, dict[str, object]], label: str) -> str:
    """Formule lisible du modèle écrite dans la ligne d'un paramètre."""
    cells = _find_row(rows, label)[1]
    return _readable_formula(_cell(cells, _value_column(cells))["formula"], rows)


@pytest.fixture(scope="module")
def reference_rows() -> dict[str, dict[str, object]]:
    """Contenu de la modèle de référence extrait du modèle."""
    if not REFERENCE_PATH.exists():
        pytest.skip(
            "tests/reference.json absent : le régénérer avec "
            "`uv run python tools/extract_reference.py --sheet Sheet2 "
            "--output tests/reference.json`"
        )
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    return data["sheets"]["Sheet2"]["rows"]


def reference_parameters() -> Parameters:
    """Paramètres par défaut du formulaire (= paramètres du modèle)."""
    return Parameters(**{name: float(value) for name, value in default_values().items()})


def parameters_from_sheet(rows: dict[str, dict[str, object]]) -> Parameters:
    """Paramètres lus dans les cellules de paramètres du modèle."""
    values = {name: _row_value(rows, label) for name, label in PARAM_LABELS.items()}
    return Parameters(**values)


def _sheet_expectations(params: Parameters, direction: int) -> dict[str, object]:
    """Recalcule le modèle de référence pour un sens donné (+1 achat, -1 vente).

    Chaque valeur porte en commentaire l'intitulé de la colonne du modèle
    dont elle est issue.
    """
    max_risk = params.capital * params.risk_per_trade
    pip_per_lot = params.contracts_per_lot * params.pip_size
    entry = (  # Prix d'entrée
        params.current_price + direction * params.entry_distance_pips * params.pip_size
    )
    stop_loss = entry - direction * params.stop_loss_pips * params.pip_size  # Stop loss
    take_profit = (  # Take profit
        entry + direction * params.stop_loss_pips * params.target_rr * params.pip_size
    )
    breakeven = entry + direction * params.spread_pips * params.pip_size  # Point mort
    max_volume_by_risk = max_risk / (  # Volume Risque
        (params.stop_loss_pips + params.spread_pips + params.slippage_pips) * pip_per_lot
    )
    max_volume_by_margin = (  # Volume marge
        params.free_margin * params.leverage / (params.contracts_per_lot * entry)
    )
    # Volume : INT(MIN(plafonds) / pas) * pas, INT arrondit vers -inf comme floor.
    volume = math.floor(min(max_volume_by_risk, max_volume_by_margin) / params.lot_step) * (
        params.lot_step
    )
    contracts = volume * params.contracts_per_lot  # Contrats exposés
    pip_value = volume * pip_per_lot  # Valeur pip ($)
    exposure = contracts * entry  # Exposition ($)
    required_margin = exposure / params.leverage  # Marge requise
    theoretical_risk = -params.stop_loss_pips * pip_value  # Risque théorique ($)
    realistic_risk = (  # Risque réaliste ($)
        -(params.stop_loss_pips + params.spread_pips + params.slippage_pips) * pip_value
    )
    potential_gain = (  # Gain potentiel ($)
        params.stop_loss_pips * params.target_rr - params.spread_pips
    ) * pip_value
    real_rr = None if realistic_risk == 0 else potential_gain / -realistic_risk  # R/R réel
    stop_loss_pct = -params.stop_loss_pips * params.pip_size / entry  # Stop loss (%)
    if volume < params.lot_step:  # Contrôle risque
        risk_check = RISK_CHECK_VOLUME
    elif -realistic_risk > max_risk:
        risk_check = RISK_CHECK_RISK
    elif required_margin > params.free_margin:
        risk_check = RISK_CHECK_MARGIN
    else:
        risk_check = RISK_CHECK_OK
    return {
        "entry_price": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "breakeven": breakeven,
        "volume": volume,
        "contracts": contracts,
        "pip_value": pip_value,
        "exposure": exposure,
        "required_margin": required_margin,
        "theoretical_risk": theoretical_risk,
        "realistic_risk": realistic_risk,
        "potential_gain": potential_gain,
        "real_rr": real_rr,
        "stop_loss_pct": stop_loss_pct,
        "max_volume_by_risk": max_volume_by_risk,
        "max_volume_by_margin": max_volume_by_margin,
        "risk_check": risk_check,
    }


def assert_scenario_matches(scenario, expected: dict[str, object]) -> None:
    """Compare un scénario calculé aux valeurs attendues."""
    for field in COMPARED_FIELDS:
        wanted = expected[field]
        actual = getattr(scenario, field)
        if isinstance(wanted, str):
            assert actual == wanted
        elif wanted is None:
            assert actual is None
        else:
            assert actual == pytest.approx(wanted, rel=1e-9, abs=1e-12), field


# --------------------------------------------------------------------------
# Comparaison avec les formules du modèle
# --------------------------------------------------------------------------


@pytest.mark.parametrize("direction", [BUY, SELL])
def test_scenario_follows_the_sheet_formulas(direction: int) -> None:
    """Le scénario recalcule le modèle de référence pour les paramètres par défaut."""
    params = reference_parameters()
    scenario = compute_scenario(params, direction)
    assert_scenario_matches(scenario, _sheet_expectations(params, direction))


@pytest.mark.parametrize("direction", [BUY, SELL])
@pytest.mark.parametrize(
    "variation",
    [
        {},
        {"spread_pips": 0.5, "slippage_pips": 0.0, "target_rr": 3.0, "lot_step": 0.05},
        {"spread_pips": 2.0, "slippage_pips": 0.5, "target_rr": 1.5, "lot_step": 0.1},
        {"stop_loss_pips": 2.5, "spread_pips": 0.8, "slippage_pips": 0.2, "target_rr": 4.0},
        {
            "capital": 5000.0,
            "risk_per_trade": 0.02,
            "pip_size": 0.01,
            "contracts_per_lot": 1000.0,
            "leverage": 10.0,
            "current_price": 50.0,
            "entry_distance_pips": 5.0,
            "stop_loss_pips": 10.0,
            "target_rr": 2.0,
            "spread_pips": 3.0,
            "slippage_pips": 1.0,
            "lot_step": 0.1,
            "free_margin": 1000.0,
        },
    ],
)
def test_scenario_follows_the_sheet_formulas_on_variations(
    direction: int, variation: dict[str, float]
) -> None:
    """Les formules du modèle restent valables sur d'autres jeux de paramètres."""
    params = replace(reference_parameters(), **variation)
    scenario = compute_scenario(params, direction)
    assert_scenario_matches(scenario, _sheet_expectations(params, direction))


def test_max_risk_and_pip_value_follow_the_sheet() -> None:
    """Le risque max vaut capital x risque par trade ; la valeur du pip vaut contrats par lot x taille du pip."""
    params = reference_parameters()
    assert params.max_risk == pytest.approx(params.capital * params.risk_per_trade)
    assert params.pip_value_per_lot == pytest.approx(params.contracts_per_lot * params.pip_size)


def test_compute_plan_returns_the_two_sheet_rows() -> None:
    """Le plan correspond aux deux scénarios (achat et vente) du modèle."""
    params = reference_parameters()
    plan = compute_plan(params)
    assert plan.params is params
    assert plan.buy.direction == BUY
    assert plan.sell.direction == SELL
    assert plan.scenarios == (plan.buy, plan.sell)
    assert plan.buy == compute_scenario(params, BUY)
    assert plan.sell == compute_scenario(params, SELL)


def test_sell_row_mirrors_the_buy_row() -> None:
    """La vente est le miroir de l’achat autour du cours actuel."""
    params = reference_parameters()
    plan = compute_plan(params)
    offset = params.entry_distance_pips * params.pip_size
    assert plan.buy.entry_price == pytest.approx(params.current_price + offset)
    assert plan.sell.entry_price == pytest.approx(params.current_price - offset)
    assert plan.buy.stop_loss == pytest.approx(
        plan.buy.entry_price - params.stop_loss_pips * params.pip_size
    )
    assert plan.sell.stop_loss == pytest.approx(
        plan.sell.entry_price + params.stop_loss_pips * params.pip_size
    )
    assert plan.buy.take_profit == pytest.approx(
        plan.buy.entry_price + params.stop_loss_pips * params.target_rr * params.pip_size
    )
    assert plan.sell.take_profit == pytest.approx(
        plan.sell.entry_price - params.stop_loss_pips * params.target_rr * params.pip_size
    )
    assert plan.buy.breakeven == pytest.approx(
        plan.buy.entry_price + params.spread_pips * params.pip_size
    )
    assert plan.sell.breakeven == pytest.approx(
        plan.sell.entry_price - params.spread_pips * params.pip_size
    )
    # Les risques ne dépendent pas du sens.
    assert plan.buy.theoretical_risk == pytest.approx(plan.sell.theoretical_risk)
    assert plan.buy.realistic_risk == pytest.approx(plan.sell.realistic_risk)
    assert plan.buy.potential_gain == pytest.approx(plan.sell.potential_gain)
    assert plan.buy.volume == pytest.approx(plan.sell.volume)


# --------------------------------------------------------------------------
# Comparaison avec les valeurs mises en cache par le modèle
# --------------------------------------------------------------------------


def test_form_defaults_are_the_sheet_parameters(reference_rows) -> None:
    """Les valeurs par défaut du formulaire sont celles du modèle."""
    defaults = default_values()
    for name, label in PARAM_LABELS.items():
        assert float(defaults[name]) == pytest.approx(_row_value(reference_rows, label)), name


@pytest.mark.parametrize(("row_label", "direction"), list(SCENARIO_ROWS.items()))
def test_scenario_matches_sheet_cached_values(
    reference_rows, row_label: str, direction: int
) -> None:
    """Chaque valeur calculée est celle mise en cache par le modèle."""
    params = parameters_from_sheet(reference_rows)
    scenario = compute_scenario(params, direction)
    header_cells = _header_cells(reference_rows)
    cells = _find_row(reference_rows, row_label)[1]
    direction_column = _column_of(header_cells, DIRECTION_HEADER)
    assert _cell(cells, direction_column)["value"] == direction
    for field, header in SCENARIO_HEADERS.items():
        column = _column_of(header_cells, header)
        assert getattr(scenario, field) == pytest.approx(
            _cell(cells, column)["value"], rel=1e-9, abs=1e-12
        ), f"{row_label} / {header} ({field})"
    risk_column = _column_of(header_cells, RISK_CHECK_HEADER)
    assert scenario.risk_check == _cell(cells, risk_column)["text"]


def test_sheet_derived_cells_match_the_module(reference_rows) -> None:
    """Le risque max et la valeur du pip sont recalculés identiquement."""
    params = parameters_from_sheet(reference_rows)
    assert params.max_risk == pytest.approx(_row_value(reference_rows, MAX_RISK_LABEL))
    assert params.pip_value_per_lot == pytest.approx(_row_value(reference_rows, PIP_VALUE_LABEL))


def test_sheet_formulas_are_the_transcribed_ones(reference_rows) -> None:
    """Les formules retranscrites dans l'oracle correspondent bien à le modèle."""
    risk_cap = _scenario_formula(reference_rows, "Buy stop", SCENARIO_HEADERS["max_volume_by_risk"])
    assert MAX_RISK_LABEL in risk_cap
    assert PIP_VALUE_LABEL in risk_cap

    margin_cap = _scenario_formula(
        reference_rows, "Buy stop", SCENARIO_HEADERS["max_volume_by_margin"]
    )
    assert PARAM_LABELS["free_margin"] in margin_cap
    assert PARAM_LABELS["leverage"] in margin_cap

    volume = _scenario_formula(reference_rows, "Buy stop", SCENARIO_HEADERS["volume"])
    assert "INT(MIN" in volume
    assert PARAM_LABELS["lot_step"] in volume

    entry = _scenario_formula(reference_rows, "Buy stop", SCENARIO_HEADERS["entry_price"])
    assert PARAM_LABELS["current_price"] in entry
    assert PARAM_LABELS["entry_distance_pips"] in entry
    assert PARAM_LABELS["pip_size"] in entry

    assert f"[{PARAM_LABELS['capital']}]*[{PARAM_LABELS['risk_per_trade']}]" in _row_formula(
        reference_rows, MAX_RISK_LABEL
    )
    assert f"[{PARAM_LABELS['contracts_per_lot']}]*[{PARAM_LABELS['pip_size']}]" in _row_formula(
        reference_rows, PIP_VALUE_LABEL
    )


def test_risk_check_labels_come_from_the_sheet(reference_rows) -> None:
    """Les libellés du contrôle de risque sont ceux écrits dans la formule du contrôle."""
    formula = _scenario_formula(reference_rows, "Buy stop", RISK_CHECK_HEADER)
    for label in (RISK_CHECK_VOLUME, RISK_CHECK_RISK, RISK_CHECK_MARGIN, RISK_CHECK_OK):
        assert label in formula


def test_sheet_cached_values_match_the_transcribed_formulas(reference_rows) -> None:
    """L'oracle (formules retranscrites) redonne les valeurs du modèle."""
    params = parameters_from_sheet(reference_rows)
    header_cells = _header_cells(reference_rows)
    for row_label, direction in SCENARIO_ROWS.items():
        expected = _sheet_expectations(params, direction)
        cells = _find_row(reference_rows, row_label)[1]
        for field, header in SCENARIO_HEADERS.items():
            column = _column_of(header_cells, header)
            assert expected[field] == pytest.approx(
                _cell(cells, column)["value"], rel=1e-9, abs=1e-12
            ), f"{row_label} / {header} ({field})"
        risk_column = _column_of(header_cells, RISK_CHECK_HEADER)
        assert expected["risk_check"] == _cell(cells, risk_column)["text"]


# --------------------------------------------------------------------------
# Arrondi du volume sur le pas de lot
# --------------------------------------------------------------------------


def test_floor_to_step_keeps_exact_multiples() -> None:
    assert floor_to_step(0.03, 0.01) == pytest.approx(0.03)
    assert floor_to_step(0.07, 0.01) == pytest.approx(0.07)
    assert floor_to_step(1.0, 0.1) == pytest.approx(1.0)
    assert floor_to_step(0.0, 0.01) == 0.0


def test_floor_to_step_rounds_down() -> None:
    assert floor_to_step(0.029, 0.01) == pytest.approx(0.02)
    assert floor_to_step(0.0099, 0.01) == pytest.approx(0.0)
    assert floor_to_step(7.4, 0.05) == pytest.approx(7.4)


def test_floor_to_step_survives_float_representation() -> None:
    """0.3 / 0.1 vaut 2.9999999999999996 : le pas ne doit pas être perdu."""
    assert 0.3 / 0.1 < 3.0
    assert floor_to_step(0.3, 0.1) == pytest.approx(0.3)
    assert floor_to_step(0.29, 0.01) == pytest.approx(0.29)


def test_floor_to_step_tolerance_never_drops_a_step() -> None:
    """Une valeur réellement 1e-9 sous un entier est arrondie à l'entier supérieur."""
    assert floor_to_step(3.0 - 1e-9, 1.0) == pytest.approx(3.0)


def test_floor_to_step_rounds_downwards_like_the_sheet() -> None:
    """INT arrondit vers -inf, comme floor : -0.005/0.01 donne -0.01."""
    assert floor_to_step(-0.005, 0.01) == pytest.approx(-0.01)


def test_volume_is_a_multiple_of_the_lot_step() -> None:
    params = reference_parameters()
    scenario = compute_scenario(params, BUY)
    steps = scenario.volume / params.lot_step
    assert steps == pytest.approx(round(steps))
    assert scenario.volume <= min(scenario.max_volume_by_risk, scenario.max_volume_by_margin)


def test_volume_follows_the_tighter_cap() -> None:
    """Le volume est celui du plus petit des deux plafonds (formule G)."""
    params = reference_parameters()
    scenario = compute_scenario(params, BUY)
    assert scenario.max_volume_by_margin < scenario.max_volume_by_risk
    assert scenario.volume == pytest.approx(
        math.floor(scenario.max_volume_by_margin / params.lot_step) * params.lot_step
    )

    # Un capital réduit rend le plafond « risque » plus serré que le plafond « marge ».
    tight = replace(reference_parameters(), capital=100.0)
    risky = compute_scenario(tight, BUY)
    assert risky.max_volume_by_risk < risky.max_volume_by_margin
    assert risky.volume == pytest.approx(
        math.floor(risky.max_volume_by_risk / tight.lot_step) * tight.lot_step
    )


# --------------------------------------------------------------------------
# Contrôle de risque
# --------------------------------------------------------------------------


def test_reference_scenario_passes_the_risk_check() -> None:
    params = reference_parameters()
    assert compute_scenario(params, BUY).risk_check == RISK_CHECK_OK
    assert compute_scenario(params, SELL).risk_check == RISK_CHECK_OK


def test_volume_below_minimum_is_reported() -> None:
    """Un volume inférieur au pas de lot est signalé avant tout autre contrôle."""
    params = replace(reference_parameters(), capital=1.0)
    scenario = compute_scenario(params, BUY)
    assert scenario.volume == 0.0
    assert scenario.risk_check == RISK_CHECK_VOLUME


def test_volume_check_has_priority_over_the_margin_check() -> None:
    """Sans marge, le volume tombe à zéro : c'est le contrôle « volume » qui parle."""
    params = replace(reference_parameters(), free_margin=0.0)
    scenario = compute_scenario(params, BUY)
    assert scenario.volume == 0.0
    assert scenario.risk_check == RISK_CHECK_VOLUME


def test_too_risky_is_reported() -> None:
    """Branche « Trop risqué » : elle n'est atteignable qu'à l'arrondi près du plafond.

    Le volume est borné par le plafond « risque », donc le risque réel ne peut
    dépasser le risque max que par l'arrondi du volume à la baisse.
    """
    params = replace(
        reference_parameters(),
        capital=3.7,
        risk_per_trade=0.5,
        pip_size=0.1,
        contracts_per_lot=1.0,
        leverage=10.0,
        current_price=0.5,
        entry_distance_pips=1.0,
        stop_loss_pips=0.5,
        spread_pips=2.0,
        slippage_pips=0.0,
        lot_step=0.05,
        free_margin=10.0,
    )
    scenario = compute_scenario(params, BUY)
    assert -scenario.realistic_risk > params.max_risk
    assert scenario.risk_check == RISK_CHECK_RISK


def test_margin_insufficient_is_reported() -> None:
    """Branche « Marge insuffisante », atteinte à l'arrondi près du plafond « marge »."""
    params = replace(
        reference_parameters(),
        capital=100.0,
        risk_per_trade=0.1,
        pip_size=1.0,
        contracts_per_lot=100.0,
        leverage=30.0,
        current_price=1.0,
        entry_distance_pips=1.0,
        stop_loss_pips=0.3,
        spread_pips=0.1,
        slippage_pips=0.1,
        lot_step=0.05,
        free_margin=1.0,
    )
    scenario = compute_scenario(params, BUY)
    assert scenario.required_margin > params.free_margin
    assert scenario.risk_check == RISK_CHECK_MARGIN


# --------------------------------------------------------------------------
# Cas limites
# --------------------------------------------------------------------------


def test_zero_entry_distance_enters_at_the_current_price() -> None:
    params = replace(reference_parameters(), entry_distance_pips=0.0)
    plan = compute_plan(params)
    assert plan.buy.entry_price == pytest.approx(params.current_price)
    assert plan.sell.entry_price == pytest.approx(params.current_price)
    assert plan.buy.breakeven == pytest.approx(
        params.current_price + params.spread_pips * params.pip_size
    )
    assert plan.sell.breakeven == pytest.approx(
        params.current_price - params.spread_pips * params.pip_size
    )


def test_without_spread_nor_slippage_the_risks_are_equal() -> None:
    params = replace(reference_parameters(), spread_pips=0.0, slippage_pips=0.0)
    scenario = compute_scenario(params, BUY)
    assert scenario.theoretical_risk == pytest.approx(scenario.realistic_risk)
    assert scenario.potential_gain == pytest.approx(
        params.stop_loss_pips * params.target_rr * scenario.pip_value
    )


def test_zero_target_rr_puts_the_take_profit_at_the_entry_price() -> None:
    params = replace(reference_parameters(), target_rr=0.0)
    scenario = compute_scenario(params, BUY)
    assert scenario.take_profit == pytest.approx(scenario.entry_price)
    assert scenario.potential_gain == pytest.approx(-params.spread_pips * scenario.pip_value)
    assert scenario.real_rr is not None and scenario.real_rr < 0
    assert scenario.min_win_rate is not None and scenario.min_win_rate > 1


def test_min_win_rate_is_one_over_one_plus_real_rr() -> None:
    plan = compute_plan(reference_parameters())
    for scenario in plan.scenarios:
        assert scenario.real_rr is not None
        assert scenario.min_win_rate == pytest.approx(1 / (1 + scenario.real_rr))


# --------------------------------------------------------------------------
# Saisies invalides
# --------------------------------------------------------------------------


@pytest.mark.parametrize("direction", [0, 2, -2, 1.5, "buy", None])
def test_unknown_direction_is_rejected(direction: object) -> None:
    with pytest.raises(ValueError):
        compute_scenario(reference_parameters(), direction)  # type: ignore[arg-type]


def test_zero_risk_in_pips_is_a_division_by_zero() -> None:
    """Une division par zéro survient quand stop loss, spread et slippage sont nuls."""
    params = replace(reference_parameters(), stop_loss_pips=0.0, spread_pips=0.0, slippage_pips=0.0)
    with pytest.raises(ZeroDivisionError):
        compute_scenario(params, BUY)


def test_zero_entry_price_is_a_division_by_zero() -> None:
    """Une division par zéro survient quand le prix d'entrée est nul."""
    params = replace(
        reference_parameters(), current_price=0.1, entry_distance_pips=1.0, pip_size=0.1
    )
    with pytest.raises(ZeroDivisionError):
        compute_scenario(params, SELL)


def test_zero_leverage_is_a_division_by_zero() -> None:
    """Une division par zéro survient quand le levier est nul."""
    params = replace(reference_parameters(), leverage=0.0)
    with pytest.raises(ZeroDivisionError):
        compute_scenario(params, BUY)


def test_zero_pip_size_is_a_division_by_zero() -> None:
    """Une taille de pip nulle rend la valeur du pip nulle : division par zéro."""
    params = replace(reference_parameters(), pip_size=0.0)
    with pytest.raises(ZeroDivisionError):
        compute_scenario(params, BUY)
