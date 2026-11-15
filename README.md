# trading-position-calculator

Application web locale de calcul de positions CFD.

Reprise du calculateur `reference.ods` (modèle de référence) sous forme de page
web : on saisit les paramètres du trade, on obtient le plan d'entrée, le volume
et les niveaux de stop et d'objectif.

## Lancer l'application

```bash
uv sync
uv run trading-position-calculator
```

Puis ouvrir <http://127.0.0.1:8000>. Le serveur n'écoute que sur cette adresse
(boucle locale) : aucune donnée ne quitte la machine.

## Structure

```
src/trading_position_calculator/
  calculations.py   # logique de calcul pure, sans dépendance web
  fields.py         # description des paramètres, analyse et validation de la saisie
  app.py            # application FastAPI et mise en forme des résultats
  cli.py            # lancement du serveur local
  templates/
tests/              # suite de tests
```

## Comportement de calcul

Les calculs reproduisent le comportement du modèle de référence :

* risque max autorisé = capital × risque par trade ;
* valeur du pip pour 1 lot = contrats par lot × taille d'un pip ;
* prix d'entrée = cours actuel + sens × distance d'entrée × taille d'un pip ;
* volume = arrondi à la baisse sur le pas de lot du plus petit des volumes
  maximaux autorisés par le risque et par la marge ;
* risque théorique = stop loss × valeur du pip ;
* risque réaliste = (stop loss + spread + slippage) × valeur du pip ;
* gain potentiel = (stop loss × R/R visé − spread) × valeur du pip ;
* R/R réel = gain potentiel ÷ risque réel ;
* taux de réussite minimum = 1 ÷ (1 + R/R réel), affiché dans un bloc dédié
  sous les résultats : c'est la part de trades à gagner pour ne pas perdre
  d'argent ;
* contrôle du risque, dans cet ordre : volume inférieur au pas minimum, risque
  réel supérieur au risque max, marge requise supérieure à la marge disponible.

Les valeurs sont calculées en précision maximale ; l'arrondi n'intervient qu'à
l'affichage.

Deux points hérités du modèle, à connaître :

* les deux volumes maximaux du modèle sont affichés en
  pourcentage dans le modèle, mais utilisés comme des volumes en lots par la
  formule de calcul du volume ; l'application les affiche donc en lots ;
* le taux de réussite minimum vaut 1 / (1 + R/R réel). Si le R/R réel est
  négatif, ce taux dépasse 100 % : le trade a alors une espérance négative et
  aucun taux de réussite ne le rend rentable.

### Incertitudes ouvertes

Deux points n'ont pas encore été confirmés par l'utilisateur et sont donc
implémentés **tels quels** dans le modèle :

1. le point mort intègre le spread mais pas le slippage ;
2. le gain potentiel intègre le spread mais pas le slippage.

Ces deux points n'affectent pas l'ensemble du fonctionnement : ils n'ont d'effet
que sur les valeurs de sortie correspondantes.

## Tests

La suite de tests vit dans `tests/` et compare les résultats au modèle de
référence.

```bash
uv run pytest
uv run ruff check .
```
