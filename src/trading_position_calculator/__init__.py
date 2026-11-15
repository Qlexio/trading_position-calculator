"""Calculateur de positions CFD.

Le paquet expose deux couches :

* ``calculations`` : la logique de calcul, sans aucune dépendance web ;
* ``app`` : l'interface web FastAPI, qui ne fait qu'afficher les résultats.
"""

from .calculations import (
    BUY,
    SELL,
    Parameters,
    Plan,
    Scenario,
    compute_plan,
    compute_scenario,
)

__all__ = [
    "BUY",
    "SELL",
    "Parameters",
    "Plan",
    "Scenario",
    "compute_plan",
    "compute_scenario",
]
