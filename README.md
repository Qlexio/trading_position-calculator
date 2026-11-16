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
(boucle locale) : aucune donnée ne quitte la machine. Le port est modifiable
par la variable d'environnement `PORT` (`PORT=8080 uv run trading-position-calculator`).

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

Héritages, choix d'arrondi et limites :

* les deux volumes maximaux du modèle sont affichés en
  pourcentage dans le modèle, mais utilisés comme des volumes en lots par la
  formule de calcul du volume ; l'application les affiche donc en lots ;
* le taux de réussite minimum vaut 1 / (1 + R/R réel). Si le R/R réel est
  négatif, ce taux dépasse 100 % : le trade a alors une espérance négative et
  aucun taux de réussite ne le rend rentable ;
* deux des quatre verdicts du contrôle de risque (« Trop risqué » et « Marge
  insuffisante ») sont inatteignables en pratique : le volume est arrondi à la
  baisse du plus petit des deux plafonds, donc le risque réel ne peut dépasser
  le risque max, ni la marge requise la marge disponible, que par l'arrondi
  flottant. Le contrôle se réduit en pratique à « OK » ou « Volume < minimum ».
  Le comportement du modèle est conservé tel quel ;
* le calcul du volume utilise `floor(valeur / pas)` avec une tolérance
  `FLOOR_EPSILON = 1e-9`. Cette tolérance évite qu'une erreur de
  représentation flottante (par exemple `0.3 / 0.1 = 2.9999999999999996`)
  fasse perdre un lot. En contrepartie, une valeur réellement à `1e-9` sous un
  entier est arrondie à l'entier supérieur. La tolérance est volontairement
  orientée pour ne jamais sous-estimer le volume.

Trois saisies sont refusées par l'application alors que le modèle les accepte :

* stop loss, spread et slippage tous nuls : le modèle produit `#DIV/0!`
  (division par zéro sur R20 et S21) ; l'application refuse avec un message
  explicite ;
* prix d'entrée de vente (cours actuel − distance d'entrée × taille du pip)
  inférieur ou égal à zéro : le modèle calcule un volume négatif et des
  sorties incohérentes ; l'application refuse avec un message explicite ;
* marge disponible supérieure au capital du compte : le capital est le montant
  total de trésorerie, la marge disponible est la part encore libre ; le
  modèle ne le vérifie pas.

### Incertitudes ouvertes

Deux points n'ont pas encore été confirmés par l'utilisateur et sont donc
implémentés **tels quels** dans le modèle :

1. le point mort intègre le spread mais pas le slippage ;
2. le gain potentiel intègre le spread mais pas le slippage.

Ces deux points n'affectent pas l'ensemble du fonctionnement : ils n'ont d'effet
que sur les valeurs de sortie correspondantes.

## Tests

La suite de tests vit dans `tests/`. Elle est volontairement écrite par un
agent indépendant (pas par l'implémenteur) et compare les résultats au
modèle de référence. Le modèle étant `gitignore`, il n'est pas dans le
dépôt : il faut le placer à la racine (`reference.ods`) pour que la suite
puisse s'exécuter contre la référence. Sans le modèle, la suite doit
désactiver (skip) les vérifications concernées plutôt que d'échouer.

Les valeurs de référence peuvent être extraites dans un JSON compact avec :

```bash
uv run python tools/extract_reference.py --sheet Sheet2 --output tests/reference.json
```

L'extrait est lui-même `gitignore` : il contient le contenu du modèle, qui
doit rester interne et ne pas être versé dans le dépôt.

```bash
uv run pytest
uv run ruff check .
```
