# Configuration

`models.example.yaml` is a non-secret structural example. Copy it to the local `models.yaml` and replace placeholders only after the provider, endpoint, model IDs, supported parameters, and prices are confirmed.

Secrets and endpoints come from environment variables listed in [`.env.example`](../.env.example). Phase 0 intentionally leaves real endpoints, model IDs, prices, budgets, and retry policy unresolved. A successful YAML parse does not prove provider availability and does not call a model.
