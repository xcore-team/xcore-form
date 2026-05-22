# XForm — Smart Form Builder for XCore

**XForm** est un moteur de formulaires intelligent permettant de construire des interfaces de saisie de données complexes et de déclencher des automatisations post-soumission.

## 🚀 Fonctionnalités

- **Builder Dynamique** : Créez des formulaires avec une large palette de champs (texte, email, signature, upload de fichiers, etc.).
- **Logique Conditionnelle** : Affichez ou masquez des champs selon les réponses précédentes.
- **Gestion des Fichiers** : Système de stockage intégré pour les pièces jointes avec suivi de progression.
- **Pipeline d'Automatisation** : Déclenchement automatique de workflows XFlow, tickets XDesk ou notifications XPulse à la soumission.
- **Analytics** : Suivi des vues, des taux de complétion et statistiques de soumission.
- **SDK JavaScript** : Client complet pour intégrer facilement les formulaires dans n'importe quelle interface web.

## 🛠 Intégration XFlow

XForm expose des actions IPC pour manipuler les formulaires programmatiquement :
- `xform.create_form` : Créer un formulaire.
- `xform.submit` : Soumettre des données.
- `xform.analytics` : Récupérer les performances.

## 📂 Structure
- `src/main.py` : Point d'entrée FastAPI et gestion des routes publiques/privées.
- `src/services/storage.py` : Gestion sécurisée du stockage de fichiers.
- `data/demo/` : Exemple d'implémentation frontend utilisant le SDK.
