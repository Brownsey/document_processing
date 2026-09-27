# doc-gen-assignment

A small config-driven pipeline that turns a client's source material into an advice report. Start
with [project guidance](PROJECT_GUIDANCE.md); it describes the exercise.

See the [implementation plan](IMPLEMENTATION_PLAN.md), [decisions log](DECISIONS.md), and [prompt rewrite brief](prompt.md).

## Setup

```bash
# From the repository root:
cp .env.example .env          # put your OpenAI key in .env
uv sync                       # or: pip install -e .
```

## Run

```bash
uv run python -m agent_pipeline.generate --client client_01_clean
# report is written to outputs/client_01_clean.md
```

Available clients live under `data/`:

- `client_01_clean`
- `client_02_medium`
- `client_03_hard`
- `client_04_stretch` (large and messy on purpose; a deliberate stretch)

Successful runs write a Markdown draft and JSON sidecar under `outputs/`. Drafts always need
adviser review. Failed or blocked runs return a non-zero exit and diagnostics, removing stale
success files. `--data-dir`, `--config` and `--output-dir` remain supported.

## First-round workflow

Client files become source-labelled evidence. A model extracts typed facts; pure functions
reconcile accounts, dates and money. A bounded investigator can read/search that client's
evidence. Configured selectors supply relevant facts to each slot. Code renders holdings,
actions and fixed warnings; Luna writes the narrative. Validation runs before the unchanged
formatter's assembled report is released.

Provider calls sit behind a small port. OpenAI Chat Completions is the default; OpenRouter is wired but has not
been tested live. Switching requires explicit selection and your own provider/model settings:

```bash
# Set OPENROUTER_API_KEY in .env first; substitute a valid OpenRouter model ID.
uv run --locked python -m agent_pipeline.generate --client client_01_clean --provider openrouter --model "PROVIDER/MODEL" --base-url https://openrouter.ai/api/v1
```

The OpenAI generation CLI defaults to `--cap-usd 10`, with persistent reservations in
`.local/paid-budget.sqlite3` (override using `--ledger`). The cap requires a locally priced model;
OpenRouter and experiment callers do not receive this CLI default. Calls, retries and execution
time are bounded; usage and estimated or unknown costs are recorded. Optional `--cache-dir .cache` reuses accepted extraction/image results;
prompt comparisons use fresh calls. Flex is outside V1.

## Prompts and evaluation

`template_config.json` includes the integrated guidance, native fixes and two further Astra reviews.
Edit the top-level `tone_of_voice` in `config/template_config.json` to change generated prose
style across all model calls. Verbatim evidence and task/output contracts take precedence;
fixed report wording remains controlled by templates and renderers.
All four development clients passed the final captured-prompt subagent run; live API verification
remains pending. These results do not establish unseen hold-out performance.
The shortened prompts await fresh live checks while the API account is unavailable.
`template_config.candidate.json` contains the earlier separate Astra rewrite, **PENDING USER REVIEW**.
`template_config.original.json` preserves the scaffold configuration.

```bash
# Compare both prompt versions on all four clients, using Luna for both.
uv run --locked --extra experiment python -m agent_pipeline.evaluation --mode compare --baseline-config config/template_config.json --config config/template_config.candidate.json --mlflow

# Generate a candidate draft explicitly.
uv run --locked python -m agent_pipeline.generate --client client_01_clean --config config/template_config.candidate.json
```

Comparisons and local MLflow stores stay under ignored `.local/`. Source-reviewed expectations
live in `eval/development/`; separate synthetic families are reserved for later checks. They
are never passed to generation. Registering prompts does not approve them or start tuning:

```bash
uv run --locked --extra experiment python -m agent_pipeline.experiments --mode register --config config/template_config.candidate.json
```

Automatic improvement requires your review of the actual drafts, results and exact registered
prompt versions.

## Verification

```bash
uv run --locked --extra dev --extra experiment python scripts/verify.py
```

This runs lint, formatting, types and offline tests, including local MLflow integration. It
does not make live API calls. Normal generation does not require MLflow.
