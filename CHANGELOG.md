# Changelog

Fork de [Master13011/SNCF-API-HA](https://github.com/Master13011/SNCF-API-HA), basé sur la 1.3.3.

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
