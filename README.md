# doc-gen-assignment

A small config-driven pipeline that turns a client's source material into an advice report. Start
with [project guidance](PROJECT_GUIDANCE.md); it describes the exercise.

See the [implementation plan](IMPLEMENTATION_PLAN.md), [decisions log](DECISIONS.md), and [prompt rewrite brief](prompt.md).

## Setup

```bash
# From the repository root:
cp .env.example .env          # add your chosen provider's key
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

Use `--publish-anyway` to save the final draft when source-support review still rejects it
after bounded repair attempts. The default is false. The report gets a prominent manual-correction
warning and review issues; its manifest records `published_with_issues`, failed validation and
the rejection details. Generation exits zero when this requested draft is saved; evaluation
still fails it. Input, provider, execution-limit, extraction and incomplete-report failures
remain errors and do not publish a draft. Rejected extraction results are not approved for caching.

## First-round workflow

Client files become source-labelled evidence. A model extracts typed facts; pure functions
reconcile accounts, dates and money. A bounded investigator can read/search that client's
evidence. Configured selectors supply relevant facts to each slot. Code renders holdings,
actions and fixed warnings; Luna writes the narrative. Validation runs before the unchanged
formatter's assembled report is released.

Provider calls sit behind a small port. OpenAI Chat Completions is the default. The OpenRouter
adapter has been exercised live with `openai/gpt-6-luna`; select it explicitly with your own key:

```bash
# Set OPENROUTER_API_KEY in .env first.
uv run --locked python -m agent_pipeline.generate --client client_01_clean --provider openrouter --model "openai/gpt-6-luna"
```

`--provider openrouter` selects the official `https://openrouter.ai/api/v1` endpoint and
reads `OPENROUTER_API_KEY` from `.env`. An explicit `--model` is required. OpenAI remains
the default and reads `OPENAI_API_KEY` and `OPENAI_MODEL`. Neither route falls back to the other.
API keys stay in the environment, not command arguments.

Generate all four reports into `outputs/` from PowerShell:

```powershell
$clients = 'client_01_clean', 'client_02_medium', 'client_03_hard', 'client_04_stretch'
foreach ($client in $clients) {
    uv run --locked python -m agent_pipeline.generate --client $client --provider openrouter --model openai/gpt-6-luna
    if ($LASTEXITCODE -ne 0) { throw "Generation failed: $client" }
}
```

Generation and evaluation share these runtime flags:

| Flag | Default / purpose |
| --- | --- |
| `--provider` | `openai` or `openrouter` |
| `--model` | OpenAI: `OPENAI_MODEL` or `gpt-6-luna`; OpenRouter: explicit model ID |
| `--base-url` | Official URL for the selected provider; other endpoints are rejected |
| `--timeout` | 90 seconds per API attempt; maximum 600 |
| `--max-retries` | 2 transport retries; range 0–5 |
| `--max-output-tokens` | 20000 per call; range 1–128000 |
| `--reasoning-effort` | `medium`; also `none`, `low`, `high`, `xhigh`, `max`, subject to model support |
| `--run-timeout` | 300 seconds per client run |
| `--max-calls` | 40 model calls per client run; transport retries are separately bounded |
| `--publish-anyway` | Off; save a source-rejected draft for manual correction after bounded repairs |
| `--tone-of-voice` | Optional override of the config's shared prose style |
| `--adviser-confirmations` | `full` (default) or `discrepancy`; filters only the adviser-query list |
| `--config`, `--data-dir`, `--output-dir` | Select the prompt configuration, inputs and destination |

There is no local monetary budget or spend ledger. Provider account limits control spending;
usage and estimated or unknown costs are recorded for inspection. Optional generation-only
`--cache-dir .cache` reuses accepted extraction/image results. Evaluation uses fresh calls.
Flex is outside V1. Use either command's `--help` for its full argument list.

Each repair saves `client_name_repair_YYYYMMDDTHHMMSSffffffZ.md` beside the report
in the selected output directory. The UTC timestamped diagnostic records the exact
trigger, repair category/attempt and run ID, plus the rejected text or extracted facts.
It is written before retrying and retained if generation fails or the client is rerun.
These are debugging artifacts, not approved reports; default-output diagnostics are
Git-ignored. The manifest's `repair_history` links each file and retains the trigger.
If a diagnostic cannot be written, the manifest records `repair_diagnostic_write_failed`
and generation continues. Past runs that did not save the trigger cannot be reconstructed.
Source-review findings about fixed templates or deterministic rendering use `issue_kind=code`:
the run stops with the exact issue in its manifest instead of attempting ineffective fact repairs.

## Prompts and evaluation

`template_config.json` includes the integrated guidance, native fixes and two further Astra reviews.
Edit the top-level `tone_of_voice` in `config/template_config.json` to change generated prose
style across all model calls, or pass `--tone-of-voice "Concise, plain British English."`
for one run. Evaluation snapshots capture the effective tone. Verbatim evidence and task/output contracts take precedence;
fixed report wording remains controlled by templates and renderers.
The shared action renderer separates transaction bullets from adviser confirmations and merges
only known overlapping controls. It preserves distinct conditions and the full audit trail.
The accounts-covered table includes in-scope proposed accounts as `Not yet opened`, without
inventing holdings or values. ISA subscriptions carry an explicit remaining-allowance check,
including plans that allocate the balance elsewhere. Ordinary ISA transfers remain distinct
from new subscriptions; special-wrapper limits still require confirmation
([GOV.UK transfer guidance](https://www.gov.uk/individual-savings-accounts/transferring-your-isa)).
Full GIA disposals allocated only to ISAs require an adviser decision before sale or reinvestment:
confirm remaining allowances and proceeds, then agree any revised sale amount or surplus treatment.
The recorded full disposal is preserved; the pipeline does not choose replacement advice.
Keep `--adviser-confirmations full` for the take-home (also the default). Select
`--adviser-confirmations discrepancy` to omit routine missing-information and missing-rationale
queries from Adviser confirmations. Conflicting facts or instructions remain visible.
Fee/tax checks, holdings uncertainty, recorded actions and conditions, and implementation pauses
are unaffected. Filtering never resolves a conflict or bypasses a generation block; the manifest
retains every review item and records the selected mode. The same setting is available as
`adviser_confirmations` in the configuration; an explicit CLI argument overrides it.
Numerical adult ISA ceilings are currently verified only for 2026/27 and named individual owners.
Other years, ambiguous timing/ownership and special ISA wrappers require manual limit confirmation.
This implementation pause permits an adviser-review draft; it does not require `--publish-anyway`.

The saved OpenRouter drafts in `outputs/` used `openai/gpt-6-luna` at `medium` reasoning,
without a publication override, and passed source review when generated. Rescoring them on
27 September 2026 passes 301 of 303 development checks: client 1 passes 34/34, client 2
64/64, client 3 70/70 and client 4 133/135. All four final runs required no repairs.
Client 4 has two directive-matching failures: the scorer treats continuation sentences
inside named SIPP contribution bullets as separate instructions without account context.
These remain evaluation failures; this is not an all-pass development result.
The prompt now distinguishes unchanged valuations from instructions to retain investments.
Opening and funding have separate checks; contribution checks cover each required recipient,
and action matching distinguishes affirmative advice from negation and account identifiers.
These are run-specific results, not a guarantee of repeatability or unseen hold-out performance.
Some review wording can still overlap when an extracted task has no reliable account link;
those controls are kept rather than merged speculatively. Official OpenAI access last returned
`account_deactivated`.
`template_config.candidate.json` contains the earlier separate Astra rewrite, **PENDING USER REVIEW**.
`template_config.original.json` preserves the scaffold configuration.

```bash
# Score all four development clients through OpenRouter (non-zero exit if any check fails).
uv run --locked python -m agent_pipeline.evaluation --provider openrouter --model openai/gpt-6-luna
# Evaluation drafts and diagnostics are stored under .local/evaluations by default.
# --output-dir outputs/evaluations keeps evaluation artifacts under outputs instead.

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
