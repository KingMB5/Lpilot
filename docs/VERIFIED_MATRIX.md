# VERIFIED MATRIX — live API evidence log

Every «verified» claim about the **LIVE** Liara API must trace to a row produced by
`scripts/live_check.py` below. This separates two evidence levels:

1. **Spec-level verification** — request/response shapes proven against the official OpenAPI
   snapshot (`config/spec/paas.yaml`, checked 2026-10-20) → recorded in `config/defaults.yaml`
   and `docs/METRICS_VERIFICATION.md`.
2. **Live verification** — this file. A row exists here only after a real API call was made by
   `scripts/live_check.py` (dry-run rows only PROVE what *would* be sent; they are marked
   SKIPPED, never PASS).

Verdicts: **PASS** = real 200/201 observed · **FAIL** = real error observed ·
**UNVERIFIED** = could not confirm (endpoint differed / app missing / format rejected) ·
**SKIPPED** = tier-2 step in dry-run mode (nothing sent).

Tier-3 steps (delete app / delete disk) are intentionally absent — the agent never calls them
(risk matrix, `docs/ARCHITECTURE.md` §4).

_(No live run yet — this file fills on the first execution of
`LIARA_API_TOKEN=… python scripts/live_check.py`, dry-run first.)_
