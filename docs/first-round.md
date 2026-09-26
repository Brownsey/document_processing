# First-round implementation

The planning checkpoint was committed and pushed as `064926d`. The first implementation adds
typed evidence and reconciliation, provider adapters, shared generation/evaluation, deterministic
report slots, bounded investigation and local prompt experiments. The supplied formatter is
unchanged.

## Current limits

Two small credential checks through the new OpenAI Responses adapter returned authentication
errors. The second supplied the key directly from `.env`; no environment override was present.
The old scaffold's Chat Completions path has not been retested, so the cause is not established.
Resolve that authentication/compatibility issue before further live runs. The four generated
drafts, text/image/investigation checks and baseline versus Astra comparison remain unverified.
No replacement reports or quality scores have been invented. All automated tests are offline.

The Astra candidate remains **PENDING USER REVIEW**. No automatic optimisation or promotion has
run. OpenRouter is wired only; its live compatibility is unverified. Flex remains outside V1.

Normal generation imports and the missing-key failure path were checked in a separate locked
environment without MLflow. This does not prove successful live generation. Offline tests use fake
provider replies; a local MLflow integration test uses a real temporary SQLite registry.

Final offline verification: 326 tests passed without skips, plus lint, formatting and type checks.
The same checks passed in a clean export of the staged files with a fresh environment. Independent
validation and final review resolved all Important findings. One upstream MLflow/SQLAlchemy
deprecation warning remains; live evidence is still missing.

The original `uv run python -m agent_pipeline.generate --client client_01_clean` entry point,
`.env` discovery, default config and `outputs/client_01_clean.md` destination are preserved and
covered by an offline compatibility regression. Additional CLI arguments are optional.

## Assignment evidence

| Assessment | Implementation/evidence | Still needed |
| --- | --- | --- |
| Correct facts and source priority | Typed source references, dated comparable valuations, explicit scope, duplicate/commitment/funding checks; independent source-reviewed expectations. | Inspect all four live drafts against sources. |
| Correct sections | Config order/inclusion, deterministic tax selector, fixed-warning and assembled-report checks. | Confirm actual model outputs pass. |
| Prompt quality and iteration | Original, structured baseline and Astra candidate; readable prompt diff and review gate. | Same-pipeline Luna comparison, user review, then bounded MLflow trial. |
| Speed, cost and effectiveness | Narrow inputs, limited investigation/repair/retries, accepted extraction/image caching, per-attempt usage and unknown-cost labels. | Measure cold/cached latency, cost per valid draft and failure rate. |
| Held-out behaviour | Separate synthetic families, renamed/nested sources, changed values, duplicate and missing-evidence tests. | Fresh reserved generations; actual assignment held-out set is unavailable. |
| Agent design and next steps | One workflow with client-only read/search/account tools and typed failures; OpenAI/OpenRouter ports. | Inspect a live investigation trace; expand only from measured failures. |

The scorer is an explicit check of facts and rendered text, not a proof of narrative quality. The
post-review comparison also uses an independent evidence-grounded advisory judge; its prompt and
rubric require human review. It cannot override hard failures or promote a config automatically.
GEPA reflection calls bypass the generation
adapter; their cost is recorded as unknown, never zero. Optimiser billing/traces need review before
claiming a total cost improvement.

## After prompt review

First compare the structured baseline and candidate with the README command. Review the reports,
failures, costs and prompt diff. Apply any requested prompt changes, re-evaluate, then register the
exact approved config. Registration creates `.local/experiments/prompt-registration.json`.

Only after explicit review, create `.local/experiments/approval.json` containing:

```json
{
  "review_status": "approved",
  "reviewed_by": "REVIEWER",
  "reviewed_at": "REVIEW_TIMESTAMP",
  "config_sha256": "COPY_FROM_REGISTRATION",
  "prompt_versions": {"COPY_ALL_PROMPT_KEYS": "COPY_IMMUTABLE_NUMBERED_URIS"}
}
```

The hash and complete prompt-version mapping must match the registration. Changing the config or
registered versions requires another review. This records human approval; registration alone does
not grant it.

```bash
uv run --locked --extra experiment python -m agent_pipeline.experiments --mode optimize --config config/template_config.candidate.json --registration .local/experiments/prompt-registration.json --approval .local/experiments/approval.json --prompt-key extraction_prompt --max-metric-calls 8
```

The experiment changes one allowlisted prompt family and exports complete finalist configs, with
paths in `optimisation-result.json`. It cannot promote automatically.

Review `config/advisory_judge.candidate.txt` and `config/advisory_rubric.candidate.json` alongside
the prompts. After approval, save the rubric as `.local/advisory_rubric.approved.json`, recording
the reviewer/date and `review_status: "approved"`. Its `prompt_sha256` binds the exact judge prompt;
prompt edits require updating that fingerprint and reviewing again.

```bash
uv run --locked --extra experiment python -m agent_pipeline.experiments --mode compare-finalist --config config/template_config.candidate.json --finalist-config .local/experiments/finalist-config-1.json --approval .local/experiments/approval.json --registration .local/experiments/prompt-registration.json --judge-prompt config/advisory_judge.candidate.txt --judge-rubric .local/advisory_rubric.approved.json --report-model gpt-6-luna --judge-model gpt-6-luna --output-dir .local/finalist-comparison
```

This runs two fresh generations for each variant across all four development clients and reserved
families. Generation and judging use frozen source snapshots. Changed inputs/settings, cached or
incomplete trials, hard failures and unresolved judge concerns block a promotion recommendation.
The command never changes the active config. Preserve the baseline unless correctness survives
and quality, reliability or cost improves measurably. Optimiser spend stays unknown unless a
verified amount is supplied with `--optimizer-cost-usd`; no total cost gain is claimed otherwise.

Keep local traces, caches, registry databases and historical run files out of Git. Export only
deliberately reviewed submission reports/results. The old scaffold output remains outside the
repository; regression tests use new synthetic examples.
