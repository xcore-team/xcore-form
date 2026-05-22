# Intégration XFlow — Plugin XForm

Permet de construire des formulaires dynamiques et de capturer des données structurées.

## ⚡ Actions IPC

| Action | Qualified Name | Entrée (Payload) | Sortie |
| :--- | :--- | :--- | :--- |
| **Create Form** | `xform.create_form` | `CreateFormPayload` | `{"form": FormDefinition}` |
| **Submit** | `xform.submit` | `SubmitFormPayload` | `SubmitResponse` |
| **Analytics** | `xform.analytics` | `{"form_id": string}` | `{"analytics": FormAnalytics}` |

---

## 📦 Détail des Objets (Schemas)

### `FormDefinition`
- `id`: (string) UUID unique.
- `title`: (string) Titre du formulaire.
- `slug`: (string) URL unique (ex: `recrutement-dev`).
- `fields`: (array[`FormField`]) Liste des champs du formulaire.
- `settings`: (object) `{ "confirmation_message": string, "workflow_id": string, "notify_owner": bool }`.
- `status`: (string) `active`, `paused`, `archived` ou `draft`.

### `FormField`
- `id`: (string) ID technique du champ.
- `type`: (string) `text`, `email`, `number`, `select`, `file`, `signature`.
- `name`: (string) Nom de la clé dans les données soumises.
- `label`: (string) Texte affiché à l'utilisateur.
- `validation`: (object) `{ "required": bool, "pattern": regex, "min_length": int }`.

### `SubmitFormPayload`
- `slug`: (string, requis) Le slug du formulaire.
- `data`: (object, requis) Les réponses `{ "nom": "Alice", "age": 25 }`.
- `meta`: (object) Données techniques `{ "duration_sec": int, "ip": string }`.

### `SubmitResponse`
- `submission_id`: (string) ID unique de la réponse enregistrée.
- `message`: (string) Message de remerciement configuré.
- `redirect_url`: (string, optionnel) URL vers laquelle rediriger après succès.

### `FormAnalytics`
- `total_views`: (int) Nombre de fois que le formulaire a été affiché.
- `total_submissions`: (int) Nombre de réponses reçues.
- `completion_rate`: (float) Ratio submissions / vues.
- `last_submission`: (datetime) Date de la dernière réponse.

## 📡 Événements (Event Bus)

- `xform.new_submission` (Émis) : Émis quand une réponse est validée.
  - Payload : `{ "form_id", "form_title", "owner_id", "submission_id", "submission_data" }`.
