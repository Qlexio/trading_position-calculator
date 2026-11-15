"""Tests de la couche web : formulaire, résultats, erreurs et formatage.

Le sujet testé est l'application FastAPI (``app.py``) et son gabarit
``templates/index.html``, à travers ``fastapi.testclient.TestClient``.

Les chiffres affichés sont comparés à ceux produits par la couche de calcul
(``calculations.py``) : la lecture des valeurs affichées se fait en
convertissant la présentation française (virgule décimale, espace insécable
pour les milliers) en nombre, puis en comparant avec une tolérance égale à la
demi-últime décimale affichée.

Le HTML est analysé avec la seule bibliothèque standard (``html.parser``) :
aucune dépendance de test supplémentaire n'est nécessaire.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from trading_position_calculator.app import app
from trading_position_calculator.calculations import compute_plan
from trading_position_calculator.fields import FIELDS, default_values, parse_parameters

#: Espace insécable utilisé par le format français pour les milliers.
NBSP = "\u00a0"


# --------------------------------------------------------------------------
# Petit analyseur HTML (bibliothèque standard uniquement)
# --------------------------------------------------------------------------

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _Node:
    """Nœud minimal d'un arbre HTML."""

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []
        self.parent: _Node | None = None

    @property
    def text(self) -> str:
        parts = [child if isinstance(child, str) else child.text for child in self.children]
        return " ".join(part.strip() for part in parts if part.strip())

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def walk(self):
        yield self
        for child in self.children:
            if not isinstance(child, str):
                yield from child.walk()

    def find_all(self, tag: str) -> list[_Node]:
        return [node for node in self.walk() if node.tag == tag]

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent


class _TreeBuilder(HTMLParser):
    """Construit un arbre à partir du HTML rendu par le gabarit."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("(document)", {})
        self._stack: list[_Node] = [self.root]

    def _new_node(self, tag: str, attrs) -> _Node:
        node = _Node(tag, {name: value or "" for name, value in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag, attrs) -> None:
        node = self._new_node(tag, attrs)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs) -> None:
        self._new_node(tag, attrs)

    def handle_endtag(self, tag) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data) -> None:
        self._stack[-1].children.append(data)


def _parse(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


# --------------------------------------------------------------------------
# Accès aux éléments utiles de la page
# --------------------------------------------------------------------------


def _inputs(root: _Node) -> dict[str, _Node]:
    return {node.attrs["name"]: node for node in root.find_all("input") if node.attrs.get("name")}


def _field_errors(root: _Node) -> dict[str, _Node]:
    return {
        node.attrs["id"]: node
        for node in root.find_all("p")
        if "field-error" in node.classes and node.attrs.get("id")
    }


def _summary_errors(root: _Node) -> list[str]:
    messages: list[str] = []
    for block in root.find_all("div"):
        if "errors" not in block.classes:
            continue
        messages.extend(item.text for item in block.find_all("li"))
    return messages


def _result_table(root: _Node) -> _Node | None:
    tables = root.find_all("table")
    return tables[0] if tables else None


def _result_rows(root: _Node) -> dict[str, tuple[str, str]]:
    """Lignes du tableau de résultats : libellé -> (achat, vente)."""
    table = _result_table(root)
    if table is None:
        return {}
    rows: dict[str, tuple[str, str]] = {}
    for body in table.find_all("tbody"):
        for row in body.find_all("tr"):
            cells = [
                child
                for child in row.children
                if isinstance(child, _Node) and child.tag in {"th", "td"}
            ]
            if not cells:
                continue
            buy = cells[1].text if len(cells) > 1 else ""
            sell = cells[2].text if len(cells) > 2 else ""
            rows[cells[0].text] = (buy, sell)
    return rows


def _value_cells(root: _Node) -> list[str]:
    """Contenu des cellules de valeurs du tableau de résultats."""
    table = _result_table(root)
    if table is None:
        return []
    return [cell.text for cell in table.find_all("td")]


def _derived_values(root: _Node) -> dict[str, str]:
    values: dict[str, str] = {}
    for block in root.find_all("div"):
        if "derived" not in block.classes:
            continue
        for item in block.find_all("div"):
            spans = item.find_all("span")
            strongs = item.find_all("strong")
            if spans and strongs:
                values[spans[0].text] = strongs[0].text
    return values


def _winrate_section(root: _Node) -> _Node | None:
    for node in root.find_all("section"):
        if "winrate" in node.classes:
            return node
    return None


def _winrate_paragraphs(section: _Node) -> list[str]:
    return [node.text for node in section.find_all("p")]


# --------------------------------------------------------------------------
# Conversions et attentes
# --------------------------------------------------------------------------


def _to_float(text: str, kind: str) -> float:
    """Relit un nombre au format français et le rend à l'unité de calcul."""
    cleaned = text.replace(NBSP, "").replace("\u202f", "").replace(" ", "")
    if kind == "percent":
        return float(cleaned.replace("%", "").replace(",", ".")) / 100
    if kind == "money":
        return float(cleaned.replace("$", "").replace(",", "."))
    return float(cleaned.replace(",", "."))


#: Ligne du tableau de résultats -> (attribut du scénario, type de valeur).
RESULT_ROWS: tuple[tuple[str, str, str], ...] = (
    ("Prix d'entrée", "entry_price", "price"),
    ("Stop loss", "stop_loss", "price"),
    ("Take profit", "take_profit", "price"),
    ("Point mort", "breakeven", "price"),
    ("Stop loss (% de l'entrée)", "stop_loss_pct", "percent"),
    ("Volume (lots)", "volume", "quantity"),
    ("Contrats exposés", "contracts", "quantity"),
    ("Valeur du pip ($)", "pip_value", "money"),
    ("Exposition ($)", "exposure", "money"),
    ("Marge requise ($)", "required_margin", "money"),
    ("Volume max selon le risque (lots)", "max_volume_by_risk", "quantity"),
    ("Volume max selon la marge (lots)", "max_volume_by_margin", "quantity"),
    ("Risque théorique ($)", "theoretical_risk", "money"),
    ("Risque réaliste ($)", "realistic_risk", "money"),
    ("Gain potentiel ($)", "potential_gain", "money"),
    ("R/R réel", "real_rr", "ratio"),
)

#: Demi-últime décimale affichée, par type de valeur.
TOLERANCES = {
    "price": 5e-6,
    "money": 5e-3,
    "percent": 5e-5,
    "quantity": 5e-6,
    "ratio": 5e-3,
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------
# Formulaire
# --------------------------------------------------------------------------


def test_form_shows_the_thirteen_spreadsheet_defaults(client: TestClient) -> None:
    """La page d'accueil propose les 13 paramètres par défaut du modèle."""
    response = client.get("/")
    assert response.status_code == 200

    root = _parse(response.text)
    inputs = _inputs(root)
    defaults = default_values()

    assert len(inputs) == 13
    assert set(inputs) == {field.name for field in FIELDS}
    for field in FIELDS:
        rendered = inputs[field.name].attrs["value"]
        assert rendered == defaults[field.name].replace(".", ","), field.name
        assert float(rendered.replace(",", ".")) == pytest.approx(float(defaults[field.name]))

    # La page d'accueil ne présente aucun résultat.
    assert _result_table(root) is None
    assert _winrate_section(root) is None
    assert "Résultats" not in response.text


def test_submitted_values_are_kept_and_shown_in_french_format(client: TestClient) -> None:
    """La saisie est conservée, avec la virgule décimale française."""
    values = dict(default_values())
    values["capital"] = "181.31"
    values["spread_pips"] = "1.2"

    response = client.post("/", data=values)
    assert response.status_code == 200

    inputs = _inputs(_parse(response.text))
    assert inputs["capital"].attrs["value"] == "181,31"
    assert inputs["spread_pips"].attrs["value"] == "1,2"


# --------------------------------------------------------------------------
# Résultats
# --------------------------------------------------------------------------


def test_valid_submission_displays_the_calculated_results(client: TestClient) -> None:
    """Les chiffres affichés sont ceux de la couche de calcul."""
    values = default_values()
    response = client.post("/", data=values)
    assert response.status_code == 200

    plan = compute_plan(parse_parameters(values))
    rows = _result_rows(_parse(response.text))

    for label, attribute, kind in RESULT_ROWS:
        assert label in rows, label
        for scenario, displayed in zip(plan.scenarios, rows[label]):
            expected = getattr(scenario, attribute)
            assert displayed not in {"", "—"}, label
            assert _to_float(displayed, kind) == pytest.approx(
                expected, abs=TOLERANCES[kind], rel=1e-9
            ), f"{label} ({attribute})"

    # Les libellés de contrôle du risque sont repris tels quels.
    assert rows["Contrôle du risque"] == (plan.buy.risk_check, plan.sell.risk_check)
    assert rows["Sens"] == ("Achat", "Vente")


def test_valid_submission_shows_the_derived_figures(client: TestClient) -> None:
    """Les montants dérivés (risque max, valeur du pip) sont cohérents."""
    values = default_values()
    response = client.post("/", data=values)
    plan = compute_plan(parse_parameters(values))

    derived = _derived_values(_parse(response.text))
    assert set(derived) == {"Risque max autorisé ($)", "Valeur du pip pour 1 lot ($)"}
    for label, expected in (
        ("Risque max autorisé ($)", plan.params.max_risk),
        ("Valeur du pip pour 1 lot ($)", plan.params.pip_value_per_lot),
    ):
        assert _to_float(derived[label], "money") == pytest.approx(expected, abs=5e-3)


# --------------------------------------------------------------------------
# Bloc « taux de réussite minimum »
# --------------------------------------------------------------------------


def test_minimum_win_rate_block_is_outside_the_results_table(client: TestClient) -> None:
    """Le bloc du taux de réussite est un frère du tableau, pas une ligne."""
    response = client.post("/", data=default_values())
    root = _parse(response.text)

    section = _winrate_section(root)
    assert section is not None
    table = _result_table(root)
    assert table is not None

    assert not any(ancestor.tag == "table" for ancestor in section.ancestors())
    assert section.parent is table.parent
    assert "Taux de réussite minimum" not in table.text
    assert "Il faut gagner" not in table.text


def test_minimum_win_rate_block_agrees_with_the_calculation(client: TestClient) -> None:
    """Les taux affichés correspondent au minimum calculé pour chaque sens."""
    response = client.post("/", data=default_values())
    plan = compute_plan(parse_parameters(default_values()))

    section = _winrate_section(_parse(response.text))
    assert section is not None
    paragraphs = _winrate_paragraphs(section)
    values = paragraphs[1]
    assert values.startswith("Achat ")
    assert "Vente " in values

    buy = _to_float(values.split("Achat ", 1)[1].split("Vente", 1)[0], "percent")
    sell = _to_float(values.split("Vente ", 1)[1], "percent")
    assert buy == pytest.approx(plan.buy.min_win_rate, abs=5e-5)
    assert sell == pytest.approx(plan.sell.min_win_rate, abs=5e-5)


def test_minimum_win_rate_wording_is_coherent(client: TestClient) -> None:
    """Le bloc explique le seuil avec le taux calculé et une fraction lisible."""
    response = client.post("/", data=default_values())
    section = _winrate_section(_parse(response.text))
    assert section is not None

    detail = next(
        text for text in _winrate_paragraphs(section) if text.startswith("Il faut gagner")
    )
    assert detail == ("Il faut gagner 80,00 % (4 sur 5 trades) pour ne pas perdre d'argent.")


def test_minimum_win_rate_wording_when_the_threshold_is_undefined(client: TestClient) -> None:
    """Sans volume, le bloc annonce clairement que le seuil n'est pas défini."""
    values = dict(default_values())
    values["free_margin"] = "0"

    response = client.post("/", data=values)
    section = _winrate_section(_parse(response.text))
    assert section is not None

    paragraphs = _winrate_paragraphs(section)
    assert paragraphs[1] == "Achat — Vente —"
    detail = next(text for text in paragraphs if text.startswith("Le taux de réussite minimum"))
    assert detail == "Le taux de réussite minimum n'est pas défini pour ce trade."


# --------------------------------------------------------------------------
# Erreurs
# --------------------------------------------------------------------------


def test_invalid_submission_reports_errors_next_to_their_field(client: TestClient) -> None:
    """Chaque message d'erreur est affiché sous le champ qu'il concerne."""
    values = dict(default_values())
    values["capital"] = "abc"
    values["leverage"] = "-3"

    response = client.post("/", data=values)
    assert response.status_code == 200

    root = _parse(response.text)
    assert _summary_errors(root) == [
        "Capital du compte : doit être un nombre.",
        "Levier : doit être strictement positif.",
    ]

    # Aucun résultat n'est affiché quand la saisie est refusée.
    assert _result_table(root) is None
    assert _winrate_section(root) is None
    assert "Résultats" not in response.text

    errors = _field_errors(root)
    assert set(errors) == {"error-capital", "error-leverage"}
    assert errors["error-capital"].text == "Capital du compte : doit être un nombre."
    assert errors["error-leverage"].text == "Levier : doit être strictement positif."

    inputs = _inputs(root)
    for name in ("capital", "leverage"):
        field_input = inputs[name]
        field_error = errors[f"error-{name}"]
        # Le message est placé dans la même case, juste après la saisie.
        assert field_error.parent is field_input.parent
        assert field_error in field_input.parent.children
        assert field_input.parent.children.index(field_error) > (
            field_input.parent.children.index(field_input)
        )
        assert field_input.attrs.get("aria-invalid") == "true"
        assert field_input.attrs.get("aria-describedby") == f"error-{name}"

    # La saisie fautive est conservée, les autres champs n'ont pas d'erreur.
    assert inputs["capital"].attrs["value"] == "abc"
    assert "error-risk_per_trade" not in errors


def test_empty_submission_reports_one_error_per_field(client: TestClient) -> None:
    """Une saisie vide signale les 13 champs obligatoires."""
    values = {name: "" for name in default_values()}
    response = client.post("/", data=values)
    assert response.status_code == 200

    root = _parse(response.text)
    expected = [f"{field.label} : champ obligatoire." for field in FIELDS]
    assert _summary_errors(root) == expected

    errors = _field_errors(root)
    assert set(errors) == {f"error-{field.name}" for field in FIELDS}
    for field in FIELDS:
        assert errors[f"error-{field.name}"].text == f"{field.label} : champ obligatoire."

    assert _result_table(root) is None


def test_combination_error_is_shown_next_to_the_first_field(client: TestClient) -> None:
    """Une règle portant sur plusieurs champs signale le premier d'entre eux."""
    values = dict(default_values())
    values["stop_loss_pips"] = "0"
    values["spread_pips"] = "0"
    values["slippage_pips"] = "0"

    response = client.post("/", data=values)
    assert response.status_code == 200

    root = _parse(response.text)
    message = (
        "Stop loss, spread et slippage ne peuvent pas être tous nuls : le volume "
        "maximal lié au risque est une division par zéro."
    )
    assert _summary_errors(root) == [message]

    errors = _field_errors(root)
    assert set(errors) == {"error-stop_loss_pips"}
    assert errors["error-stop_loss_pips"].text == message
    assert _result_table(root) is None


# --------------------------------------------------------------------------
# Formatage français et encodage
# --------------------------------------------------------------------------


def test_results_use_french_number_formatting(client: TestClient) -> None:
    """Virgule décimale, espace insécable pour les milliers, unités jointes."""
    response = client.post("/", data=default_values())
    root = _parse(response.text)

    cells = _value_cells(root)
    assert cells
    for cell in cells:
        # Aucun point décimal.
        assert re.search(r"\d\.\d", cell) is None, cell
        # Aucune espace ordinaire entre deux chiffres (milliers).
        assert re.search(r"\d \d", cell) is None, cell

    rows = _result_rows(root)
    assert rows["Prix d'entrée"][0] == "1,10570"
    assert rows["Volume (lots)"][0] == "0,03"
    assert rows["Contrats exposés"][0] == f"3{NBSP}000"
    assert rows["Marge requise ($)"][0] == "110,57 $"
    assert rows["Risque théorique ($)"][0] == "-0,30 $"
    assert rows["Stop loss (% de l'entrée)"][0] == "-0,01 %"

    derived = _derived_values(root)
    assert derived["Risque max autorisé ($)"] == "1,81 $"

    section = _winrate_section(root)
    assert section is not None
    assert _winrate_paragraphs(section)[1] == "Achat 80,00 % Vente 80,00 %"


def test_response_is_utf8_and_keeps_accents(client: TestClient) -> None:
    """La réponse est en UTF-8 et les accents sont intacts."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.encoding == "utf-8"
    assert "charset=utf-8" in response.headers["content-type"].lower()
    assert '<meta charset="utf-8">' in response.text

    assert "Marché et exécution" in response.text
    assert "Marché".encode() in response.content
    assert "Ã©" not in response.text
    assert response.content.decode("utf-8") == response.text

    results = client.post("/", data=default_values())
    # Les accents restent des caractères UTF-8 littéraux (seul le guillemet
    # apostrophe est échappé en entité HTML par le gabarit).
    assert "entrée" in results.text
    assert "Taux de réussite minimum" in results.text
    assert "Marché et exécution" in results.text
    assert "Ã©" not in results.text
    assert results.content.decode("utf-8") == results.text
    assert "Prix d'entrée" in _result_rows(_parse(results.text))


def test_error_messages_keep_their_accents(client: TestClient) -> None:
    """Les messages d'erreur accentués sont bien servis en UTF-8."""
    values = dict(default_values())
    values["capital"] = "abc"

    response = client.post("/", data=values)
    assert "doit être un nombre." in response.text
    assert "être".encode() in response.content
