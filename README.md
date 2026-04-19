## Cueillette de données

rouler le script

`python3 property_finder.py` 

pour lancer la recherche et l'enrichissement des données avec les API call vers le détail des propriétés selectionnées dans le 1er passage.
Les données sont mis en cache pour éviter des calls remote lors de prochain run afin d'éviter de faire monter l'utilisation vers RealtyInUS provider.

Pour lancer la simulation en local sans aucun call externe:

`python3 property_finder.py --local` 

## Analyse de données

Rouler le serveur python avec

`./start_web_finance.sh`

Ouvrir un browser a l'adresse: [analyse financiere](http://localhost:8000/src/property_finance.html)