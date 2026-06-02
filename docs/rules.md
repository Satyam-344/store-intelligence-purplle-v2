# Engineering Rules

## Code Style
- Python 3.11+ — use type hints everywhere
- Line length: 100 chars max (ruff enforced)
- Formatter: ruff format
- Linter: ruff check
- No `print()` in app/ — use structlog logger only
- No bare `except:` — always catch specific exceptions

## Naming Conventions
- Files: snake_case.py
- Classes: PascalCase
- Functions/variables: snake_case
- Constants: UPPER_SNAKE_CASE
- DB table names: snake_case plural (events, pos_transactions)
- Pydantic models: PascalCase with suffix (EventCreate, EventResponse)

## Git Commits
Format: `type(scope): description`
Types: feat | fix | test | docs | refactor | chore
Examples:
- `feat(pipeline): add ByteTrack multi-object tracking`
- `fix(api): return 503 on DB unavailable`
- `test(metrics): add zero-visitor edge case`

## Test Requirements
- Statement coverage > 70% (enforced by CI gate)
- Every test file MUST have prompt block header:
```python
# PROMPT: <exact prompt used to generate these tests>
# CHANGES MADE: <what was manually adjusted after generation>
```
- Test naming: `test_<what>_<condition>` e.g. `test_ingest_duplicate_events_are_idempotent`
- Edge cases required: empty store, all-staff clip, zero purchases, re-entry in funnel
- No mocking the database — use in-memory SQLite for tests

## API Rules
- All responses are JSON
- Error responses always include: `{"error": "ERROR_CODE", "detail": "human message"}`
- HTTP 400: bad request / schema validation failure
- HTTP 404: store not found
- HTTP 503: database unavailable (never 500 for DB errors)
- Never return raw Python exception messages or stack traces
- Partial success on batch endpoints: return both succeeded and failed items

## Event Schema Rules
- `event_id` must be UUID v4 — reject anything else
- `timestamp` must be ISO-8601 UTC (ends in Z) — reject other formats
- `is_staff=true` events stored in DB but excluded from ALL customer metrics
- `confidence` must be 0.0–1.0 — store as-is, do not suppress low-confidence events
- `zone_id` must be null for ENTRY and EXIT events

## Docker Rules
- `docker compose up` must start everything from cold state
- No manual DB setup steps — init_db() runs on API startup
- Health check on all services
- No secrets in Dockerfile — use environment variables

## Documentation Rules
- DESIGN.md: updated as architecture evolves (not written at the end)
- CHOICES.md: every major decision documented with options considered
- README.md: 5-command setup from git clone to working system
- AI-assisted decisions: documented in DESIGN.md "AI-Assisted Decisions" section
