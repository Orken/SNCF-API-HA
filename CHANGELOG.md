# Changelog

Fork de [Master13011/SNCF-API-HA](https://github.com/Master13011/SNCF-API-HA), basé sur la 1.3.3.

## Non publié

### Nouveautés
- **Clé API refusée** : Home Assistant propose de saisir une nouvelle clé, vérifiée avant d'être enregistrée. Auparavant, les entités restaient indisponibles jusqu'à une reconfiguration manuelle ou un redémarrage.
- Attributs `departure_name` et `arrival_name` sur les capteurs « Train N » (noms des gares).

### Corrections
- **Dernier train en retard** : avec une heure de fin, un train prévu avant cette heure mais parti en retard reste affiché jusqu'à son départ réel, au lieu de céder la place aux trains du lendemain.
- **Trajets avec correspondance** : seuls les trains directs sont demandés à l'API. Les correspondances ne réduisent plus le nombre de trains affichés (capteurs « Train 8 » à « Train 10 » vides, plage terminée trop tôt).
- **Événement `sncf_trains_status_changed` répété toutes les 2 minutes** quand deux trajets partant de la même gare prennent le même train : le statut de chaque train est désormais suivi par trajet. Le `train_uid` des événements ne change pas.
- **Intervalles de mise à jour** : les options refusent une valeur inférieure à 1 minute. Avec 0, l'API était interrogée en continu et le quota journalier épuisé en quelques minutes.
- **Train très en retard annoncé supprimé** : un train dépassé par le suivant disparaît du temps réel. Avec une perturbation « retard important », il est désormais affiché en retard, avec ses horaires corrigés quand la SNCF les donne.
- **Train supprimé un autre jour** : une perturbation trouvée par numéro de train ne s'applique plus qu'à ses dates. La suppression du train de demain ne marque plus celui d'aujourd'hui comme supprimé.
- **Calendrier** : son état montre le train qui part ou le prochain, et non le plus proche dans le temps (souvent déjà parti, avec un décalage d'une à deux heures dû au fuseau horaire). Plus d'événement une fois tous les trains partis.
- **Retard du capteur « Tous les trains » et du calendrier** : même calcul que les capteurs « Train N », le plus grand du retard au départ et à l'arrivée. Un train parti en retard puis arrivé à l'heure n'y était pas signalé.
- **Reconfiguration d'un trajet** : changer la plage horaire met à jour l'identifiant du trajet. L'ancienne plage peut de nouveau être ajoutée, et la nouvelle ne peut plus l'être en double.
- **Démarrage** : un seul appel à l'API par trajet au lieu de trois (les entités ne redemandent plus de rafraîchissement à leur création).
- **Ressource Lovelace avec Home Assistant 2026.1 et avant** : la ressource des cartes est de nouveau créée et mise à jour automatiquement en mode stockage.
- **Diagnostics** : téléchargeables même quand l'installation a échoué (API injoignable au démarrage).
- **Cartes : jour du départ** : un train dans plusieurs jours affiche sa date (« 19/10 ») et un train parti la veille « hier », au lieu de « demain ».
- **Cartes : appareil renommé** : la destination du tableau des départs n'est plus vide et la gare de départ n'est plus remplacée par le nom de l'appareil.

### Technique
- Tests des cartes Lovelace (`node --test tests/cards.test.mjs`), lancés par la CI.

## 2.0.0

### Nouveautés
- **Trois cartes Lovelace** au choix, chargées automatiquement : **prochain train** (`sncf-train-tile`, compte à rebours), **tableau des départs** (`sncf-departure-board`, façon écran en gare) et **frise des départs** (`sncf-train-timeline`). Éditeur visuel, thème clair et sombre, textes en français ou en anglais, appui sur un train pour ouvrir sa fiche.

### Changements incompatibles
- **L'ancienne carte `sncf-train-card` est supprimée.** Les tableaux de bord qui l'utilisent affichent une erreur : remplacez-la par l'une des trois nouvelles cartes. Sa ressource Lovelace est reprise automatiquement.

### Corrections
- Traduction anglaise rétablie : les formulaires affichaient les noms techniques des champs (`departure_station`…) avec Home Assistant en anglais.

## 1.5.0

### Nouveautés
- **Heure de fin facultative** : un trajet peut se configurer avec une heure de début et un nombre de trains seulement (« les 2 premiers trains après 6h »). Avec une heure de fin, la plage se termine à la première limite atteinte : départ du dernier train demandé, ou heure de fin. Une fois la plage terminée, les trains du lendemain s'affichent.
- Le nombre de trains est limité à 1–10 et les heures sont vérifiées (format HH:MM) dans le formulaire. Le capteur « Tous les trains » se limite lui aussi au nombre de trains demandé.

### Corrections
- Le champ « Heure de fin » était libellé « Heure d'arrivée ».

## 1.4.3

### Corrections
- **Carte enregistrée comme ressource Lovelace** (mode stockage), comme les cartes HACS, avec la version dans l'adresse : le navigateur ne peut plus servir une page en cache sans la carte, ni une ancienne version de la carte.

## 1.4.2

### Corrections
- **« Configuration error » intermittent** : la carte est déclarée dès le chargement de l'intégration (et non plus à la fin du démarrage), et ne réinterroge plus tout le registre des entités à chaque changement d'état de la maison (elle utilise `hass.entities`).

## 1.4.1

### Corrections
- **Carte Lovelace `sncf-train-card`** : les valeurs par défaut n'étaient jamais appliquées, si bien que les horaires de départ et d'arrivée restaient masqués et que le rafraîchissement tournait sans délai. Elle affiche aussi désormais les trains supprimés.

## 1.4.0

### Nouveautés
- **Trains supprimés** : comparaison horaires théoriques / temps réel (l'API retire les trains supprimés du temps réel au lieu de les signaler), perturbations `NO_SERVICE`, et dernier train de la plage. Attributs `status`, `cancelled`, `disruption_effect`, `disruption_message`. Calendrier : « ❌ Supprimé ».
- **Gare d'arrivée non desservie** : `destination_served: false` (information, le train n'est pas compté supprimé).
- **Statut « parti »** (`departed`).
- **Événement `sncf_trains_status_changed`** par train (`train_uid` stable), sans doublon après redémarrage.
- **Retards au départ et à l'arrivée** : `departure_delay_minutes`, `arrival_delay_minutes` ; `delay_minutes` = le plus grand.
- **Cause enrichie** : message de perturbation, cause du train, messages du trajet.
- **Réparations Home Assistant** : trajet invalide, API injoignable, quota dépassé.

### Corrections
- La plage horaire filtre enfin sur l'heure de fin ; plages traversant minuit corrigées.
- Nombre de capteurs « Train N » constant (avant : figé selon les trains trouvés au démarrage).
- Assistant de configuration : seules des gares sont proposées (villes et adresses donnaient des trajets impossibles).
- « Aucun train » n'est plus traité comme une erreur ; données conservées pendant une panne.
- Appareils nommés « SNCF <départ> → <arrivée> ».
- Calendrier : utilise la section du train (et non un trajet à pied).
- Diagnostics : import cassé corrigé ; plus d'appel bloquant au démarrage.

### Technique
- Suite de tests complète au vert (76 tests), prête pour la CI.
- Installation HACS depuis les fichiers du dépôt (sans zip de release).
