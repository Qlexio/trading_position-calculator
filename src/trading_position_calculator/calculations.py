"""Calcul du plan de trading CFD.

Logique pure, sans aucune dépendance web. Reproduit le comportement calculé
du modèle de référence.












Les valeurs sont conservées en précision maximale ; l'arrondi n'est appliqué
qu'à l'affichage (voir ``app.py``).

Choix de calcul à connaître :

* le point mort intègre le spread mais pas le slippage ;
* le gain potentiel intègre le spread mais pas le slippage ;
* le risque théorique n'intègre ni le spread ni le slippage ;
* le contrôle de risque est évalué dans cet ordre : volume minimum, risque
  maximum, marge disponible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

BUY = 1
SELL = -1

#: Tolérance appliquée avant l'arrondi à la baisse du volume.
#:
#: En IEEE 754, 0.03 / 0.01 vaut 2.9999999999999996 : un arrondi à la baisse
#: sans tolérance ferait perdre un lot sur un volume mathématiquement entier.
FLOOR_EPSILON = 1e-9

#: Libellés du contrôle de risque, affichés tels quels dans l'interface.
RISK_CHECK_OK = "OK"
RISK_CHECK_VOLUME = "Volume < minimum"
RISK_CHECK_RISK = "Trop risqué"
RISK_CHECK_MARGIN = "Marge insuffisante"


@dataclass(frozen=True)
class Parameters:
    """Les 13 paramètres saisis, dans l'unité d'origine du modèle."""

    capital: float
    risk_per_trade: float
    pip_size: float
    contracts_per_lot: float
    leverage: float
    current_price: float
    entry_distance_pips: float
    stop_loss_pips: float
    target_rr: float
    spread_pips: float
    slippage_pips: float
    lot_step: float
    free_margin: float

    @property
    def max_risk(self) -> float:
        """Risque max autorisé ($) — capital x risque par trade."""
        return self.capital * self.risk_per_trade

    @property
    def pip_value_per_lot(self) -> float:
        """Valeur du pip pour 1 lot ($) — contrats par lot x taille d'un pip."""
        return self.contracts_per_lot * self.pip_size


@dataclass(frozen=True)
class Scenario:
    """Résultat d'un scénario d'entrée (achat ou vente)."""

    direction: int
    entry_price: float
    stop_loss: float
    take_profit: float
    breakeven: float
    volume: float
    contracts: float
    pip_value: float
    exposure: float
    required_margin: float
    theoretical_risk: float
    realistic_risk: float
    potential_gain: float
    real_rr: float | None
    min_win_rate: float | None
    stop_loss_pct: float
    risk_check: str
    max_volume_by_risk: float
    max_volume_by_margin: float


def floor_to_step(value: float, step: float) -> float:
    """Arrondit ``value`` à la baisse sur un multiple de ``step``.

    Le volume est un multiple du pas de lot, arrondi à la baisse,
    avec la tolérance ``FLOOR_EPSILON`` qui évite de perdre un lot sur un
    rapport entier mal représenté en virgule flottante.
    """
    return math.floor(value / step + FLOOR_EPSILON) * step


def compute_scenario(params: Parameters, direction: int) -> Scenario:
    """Calcule le scénario d'entrée pour le sens demandé (+1 achat, -1 vente)."""
    if direction not in (BUY, SELL):
        raise ValueError(f"sens inconnu : {direction!r}")

    p = params
    entry = p.current_price + direction * p.entry_distance_pips * p.pip_size
    stop_loss = entry - direction * p.stop_loss_pips * p.pip_size
    take_profit = entry + direction * p.stop_loss_pips * p.target_rr * p.pip_size
    breakeven = entry + direction * p.spread_pips * p.pip_size

    max_volume_by_risk = p.max_risk / (
        (p.stop_loss_pips + p.spread_pips + p.slippage_pips) * p.pip_value_per_lot
    )
    max_volume_by_margin = (p.free_margin * p.leverage) / (p.contracts_per_lot * entry)
    volume = floor_to_step(min(max_volume_by_risk, max_volume_by_margin), p.lot_step)

    contracts = volume * p.contracts_per_lot
    pip_value = volume * p.pip_value_per_lot
    exposure = contracts * entry
    required_margin = exposure / p.leverage

    theoretical_risk = -p.stop_loss_pips * pip_value
    realistic_risk = -(p.stop_loss_pips + p.spread_pips + p.slippage_pips) * pip_value
    potential_gain = (p.stop_loss_pips * p.target_rr - p.spread_pips) * pip_value

    real_rr = None if realistic_risk == 0 else potential_gain / -realistic_risk
    min_win_rate = None
    if real_rr is not None and 1 + real_rr != 0:
        min_win_rate = 1 / (1 + real_rr)

    stop_loss_pct = -p.stop_loss_pips * p.pip_size / entry

    if volume < p.lot_step:
        risk_check = RISK_CHECK_VOLUME
    elif -realistic_risk > p.max_risk:
        risk_check = RISK_CHECK_RISK
    elif required_margin > p.free_margin:
        risk_check = RISK_CHECK_MARGIN
    else:
        risk_check = RISK_CHECK_OK

    return Scenario(
        direction=direction,
        entry_price=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        breakeven=breakeven,
        volume=volume,
        contracts=contracts,
        pip_value=pip_value,
        exposure=exposure,
        required_margin=required_margin,
        theoretical_risk=theoretical_risk,
        realistic_risk=realistic_risk,
        potential_gain=potential_gain,
        real_rr=real_rr,
        min_win_rate=min_win_rate,
        stop_loss_pct=stop_loss_pct,
        risk_check=risk_check,
        max_volume_by_risk=max_volume_by_risk,
        max_volume_by_margin=max_volume_by_margin,
    )


@dataclass(frozen=True)
class Plan:
    """Le plan complet : les deux scénarios d'entrée."""

    params: Parameters
    buy: Scenario
    sell: Scenario

    @property
    def scenarios(self) -> tuple[Scenario, Scenario]:
        return (self.buy, self.sell)


def compute_plan(params: Parameters) -> Plan:
    """Calcule le plan complet (achat et vente) pour les paramètres donnés."""
    return Plan(
        params=params,
        buy=compute_scenario(params, BUY),
        sell=compute_scenario(params, SELL),
    )
