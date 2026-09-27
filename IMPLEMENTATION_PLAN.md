# V1 implementation plan

## Scope

Produce four source-backed report drafts, quantitative evaluation and a short decisions log. Read [PROJECT_GUIDANCE.md](PROJECT_GUIDANCE.md), each client's notes/specification and the actual sources.

- Python, uv, Pydantic, pytest, Ruff, ty; OpenAI `gpt-6-luna`, Standard pricing by default.
- Use GPT-6 Astra for the first prompt rewrite; keep report generation/evaluation runs on Luna so prompt comparisons do not mix model changes.
- Preserve the formatter, CLI flags and config-driven title, section order, templates, prompts and plain-language inclusion rules. Validate config before paid calls.
- Keep prompt-only configs working. Declare deterministic slots explicitly; exclude them from prompt tuning.
- No person deduplication, vectors, hosted services, UI or extra report formats. **Flex is a later nice-to-have, outside V1.**

## Pipeline

Use pure domain functions and narrow provider ports for extraction, images and writing. Generation and evaluation share generation, repairs, validation and cost tracking.

Keep trusted config/template rules separate from client evidence. Extract guidance from internal notes deliberately; do not pass whole specifications into every slot. Explicit finalisation requirements govern report release, even when meeting notes suggest confirming figures later.

Wire an OpenRouter adapter in V1 to demonstrate the same ports: explicit provider, endpoint, API key and model configuration, selected at the CLI composition point. Reuse compatible client code; keep provider details outside domain functions. No silent fallback. Document switching with placeholder credentials/model IDs; no OpenRouter test calls or claim of verified compatibility.

1. **Read:** JSON, DOCX paragraphs/tables, text and images become evidence blocks: source, hash, locator, text. Preserve structure; inventory exclusions, failures and unsupported embedded content. Handle renamed/nested sources within client-directory and size limits. Separate generic material from client evidence. Read images without supplying database answers; deduplicate their facts. Failed OCR remains unresolved, blocking dependent claims where material.
2. **Extract:** Build typed accounts, scope, observations, objectives, actions, funding, conflicts and review items. Material facts need a source, locator and excerpt. Verify support, not just valid references/schema. Amounts carry Decimal value, currency, effective date and precision. Interpret free-form instructions into validated account references; avoid regex lists for language understanding.
3. **Reconcile:** Database establishes accounts/holders, requests define scope, meetings record decisions. No blanket source priority. Compare matching account/currency/valuation basis by effective date; quoted historical figures keep their dates. Undated figures cannot win on recency; unresolved equal-date conflicts stay flagged. Preserve “around” and “a little over”; never invent precision.
4. **Investigate:** Read/search this client's evidence and accounts only. Maximum eight turns, sixteen tool calls and a run timeout. Stop repeated unhelpful reads and requests for human-owned figures. No shell/network access or instructions taken from source documents. Reconcile new evidence.
5. **Select:** Config-declared selectors supply relevant facts, dependencies, conflicts, review items and surrounding template wording. Prompt-only slots receive complete validated facts with a diagnostic. Reject unknown selectors before calls; never silently truncate required evidence. Inclusion returns include/omit/unresolved with evidence; unresolved needs a diagnostic.
6. **Write:** Declare slots as phrase, paragraph, table or static text. Config-declared renderers produce actions, holdings and fixed wording; models explain recommendations through active prompts. Reject unexpected headings, tables or whole reports within slots. Check contradictions, duplicated openings and final assembly. Allow two narrative repairs using fixed facts; factual failures return to reconciliation within shared limits. Changed facts invalidate dependent output.

Reconciliation rules:

- Resolve relative dates against the source/report effective date, never the execution date. Flag ambiguous anchors; rerunning later must not change the intended period.
- Deduplicate joint accounts by ID, combine holders, retain conflicting observations; never assume equal ownership shares.
- Separate existing holdings, funding sources and planned accounts. A mention does not establish custody or advice scope.
- Actions retain source/destination, amount or allocation rule, full/partial extent, rationale and timing. Keep contributions and disposals separate.
- Link commitments to receipts; deduct once, account for already-paid obligations and exclude contingent funds. Transfers add no wealth; valuations are not receipts; partial sales do not release full balances. Never mix currencies or double-count sale proceeds.
- Flag fees/CGT for human confirmation. Keep unknowns and valuation qualifiers visible; no ambiguous value enters an exact calculation. Conditional actions may await allowances/amounts. Unclear identities, conflicting instructions or unsupported funding block affected recommendations and a passing report.

## Errors and fail-early checks

Use explicit result types: small `Ok[T]` / `Err[E]` dataclasses, a `Result[T, E]` union and typed `Protocol` ports. Fallible operations declare their error types, e.g. `extract(...) -> Result[CaseFacts, ExtractionError]`. Keep error types provider-independent; no Result framework dependency.

Adapters catch expected SDK/file exceptions and return typed errors. Services handle `Err` before accessing values or starting dependent work; preserve the error type and context when propagating it. Core logic never catches provider exceptions. Keep constructors free of I/O; fallible setup also returns a Result. Type checks and failure-path tests enforce this convention.

- `ConfigError` / `InputError`: reject invalid config, missing credentials, unknown providers/selectors, unsafe paths and unreadable required sources before paid calls where locally detectable. Check returned schemas/provenance before facts enter reconciliation.
- `ProviderError`: carry a safe error code and retryability. Authentication, permission, unsupported-model and invalid-request failures stop immediately. Only transient timeouts, rate limits and service failures get bounded retries through the shared retry owner. No provider fallback; record uncertain charges as unknown.
- `ExtractionError`: invalid schema, unsupported facts or incomplete output cannot enter reconciliation. Any repair must stay within shared limits.
- `ExecutionLimitExceeded`: stop when time, turn or call limits are reached.
- `ReportBlocked`: stop affected generation when required evidence or domain rules cannot support it. Missing human-owned figures remain review items when a safe conditional draft is possible.
- Keep narrative repair separate from transport retries. Refusals and invalid responses never become empty successes or cached facts.
- Review items must describe unresolved evidence. Missing fees can permit a conditional draft; invented figures, duplicate sections and missing static wording are validation failures, not adviser tasks. Models cannot request information already supplied by the application.
- CLI/evaluation handle failures consistently: non-zero CLI exit, failed/blocked run status and redacted diagnostics with stage/source references. Unexpected exceptions also fail the run; never swallow them or expose secrets/raw client text.

## Report requirements

| Section | Required content |
| --- | --- |
| Introduction | Scoped existing/planned accounts and owners; no balances; exact FCA line. |
| Background | High-level circumstances, objectives, risk and timing; no transaction amounts. |
| Holdings | Scoped existing accounts, once each; Account / Owner / Type / Value, with dates/qualifiers. |
| Recommendations | Every agreed action/retain decision, rationale, funding and conditions. Explain excluded funds; acknowledge relevant platform charges without repeating fees. |
| Tax | Only potentially taxable disposals; annual-exempt wording and human confirmation, never estimated tax. |
| Fees | Relevant platform and ongoing advice confirmations; sourced initial charge and supported basis. |
| Conclusion | Exact supplied risk warning and invitation; no model call. |

## Acceptance checks

Write tests before behaviour changes. Runtime checks use client evidence; independently authored evaluation answers never enter generation or repairs.

| ID | Check |
| --- | --- |
| C1 | Reject reversed transfers, inflated numeric suffixes and contradictory actions. Check source, destination, amount and status together; accept valid paraphrases/plurals and zero charges. |
| C2 | Parse thousands separators and every disposal target. Reject unauthorised sales, including ISA/SIPP sales. |
| C3 | Reject invented fees/CGT even beside review markers. Accept sourced initial rates; identify each outstanding confirmation. |
| C4 | Respect individual/joint scope. Ambiguity never expands scope. Test similar names and alternate wording. |
| C5 | Record usage and estimated cost for every paid path, including failures, repairs and judges. Label estimates/unknown charges honestly. Keep output/time limits and one retry owner; no spend cap, reservations or concurrent billing machinery. |
| C6 | Retain/fingerprint sources, config, active prompts, model/settings, code, expectations and scorer. Detect changes; rescoring creates a new evaluation. |
| C7 | Fake ports return each typed `Err`: assert dependent stages are not called and the error reaches CLI/evaluation unchanged. Invalid inputs make zero paid calls; permanent failures are not retried; transient retries obey limits; failed/blocked runs cannot emit successful reports. Check OpenAI exception-to-error mapping; no OpenRouter test calls. |
| C8 | Enforce slot shapes and exact section order/counts. FCA/risk wording appears once in the correct location. Reject spurious insertion instructions and generation defects disguised as review items. |

Also test duplicates/images, unique image evidence, changed names/amounts/dates, missing balances, full/partial sales, contingent receipts, omissions, distractor attribution, instruction/evidence separation, prompt injection, cross-client access and unchanged meaning on later-date reruns.

Source-reviewed evaluation fixtures must cover these outcomes; never hardcode them into generation:

| Client | Expected outcomes |
| --- | --- |
| 01 | ISA-only holdings; £52,000 dated 30 April; £20,000 cash-funded top-up; 0% initial charge; no tax section or gifting action; ongoing charges flagged. |
| 02 | Joint GIA counted once; latest qualified valuation; full disposal and equal ISA allocation instruction, conditional on confirmed remaining allowances; tax/fees review. |
| 03 | Sensitive inheritance wording; qualified valuations; ISA top-ups/new joint account without invented allocations; unknown cash flagged; tax/fees review. |
| 04 | £650,000 completion funds after reserving £200,000 for the loan; earnout excluded; partial GIA sale and contribution kept separate; bond retained; three-year objective, allowance, cash and fee confirmations. |

Renamed/reordered sections, changed prompts, renamed sources and unfamiliar inclusion rules must work without section-specific Python changes. Test an alternative config and prove prompt edits affect generation without losing required evidence. Test corrupt reports and valid paraphrases; numeric checks alone do not prove prose correctness.

## Prompt evaluation

- Save local evaluation results starting with client 01; Git config remains authoritative. Prompts specify task, evidence, output shape, template context and uncertainty.
- Preserve the current failed client 01 report as a baseline/regression fixture before replacing outputs. Its duplicate sections and spurious FCA insertion requests must fail validation.
- Review expected answers independently. Reserve synthetic scenario families before tuning; keep related variants together and reserved answers out of feedback. Supplied clients are development cases. A reserved case used for tuning becomes regression data.
- **Astra first pass (V1):** follow [prompt.md](prompt.md) using GPT-6 Astra for active extraction, inclusion, investigation, validation and narrative prompts. Remove contradictory instructions; bake in no client-specific facts. Save original and candidate versions with a readable diff and rationale. Mark the candidate **PENDING USER REVIEW**; do not silently replace approved prompts.
- **Updated baseline:** evaluate the Astra candidate on Luna across all four clients before review. Compare original/candidate prompts on the same pipeline, model/settings, sources and scorer. Present reports, scores, failures and prompt diff for user review. Re-evaluate user edits, then retain the approved version as the next comparison baseline. Keep the original scaffold output separately as historical evidence.
- Score extraction, narrative and complete reports for account/action coverage, valuations, funding, review markers and unsupported claims. Record tokens, retries, latency and cost per valid draft, including failed calls and repairs; report when none pass.
- Compare the baseline and manually edited candidate with fresh Luna generations on the development cases. Cache hits do not count. Review the generated reports alongside deterministic scores; never override hard failures. Keep reserved cases out of prompt tuning.
- Adopt a prompt change only with preserved correctness and measured quality, cost or reliability gains; otherwise keep the approved baseline. Export hypothesis, prompt diff, inputs, outputs, scores, spend and decision. Separate architecture gains from prompt gains; do not claim statistical certainty.

## Delivery order

Use the `fullstack-delivery` skill for implementation, with its light profile. Follow its scheduling, ownership, integration and validation workflow within this plan's scope.

1. Define contracts, exceptions, preflight checks, source-reviewed expectations and metered provider boundary. Wire OpenAI and OpenRouter selection; validate live behaviour on OpenAI only. Pass C5 and offline C7 checks before paid calls.
2. Complete client 01 through the supplied CLI, including validation and report sidecar. Add evaluation summaries and C6; produce the Astra prompt candidate, flagged for user review.
3. Complete clients 02–04 and generalisation tests. Evaluate the candidate baseline across all four; present the review package. Pass C1–C8 before accepting prompt changes.
4. After user review, retain the approved configuration and record whether the manual prompt change was accepted. Continue independent implementation/validation while review is pending.
5. Run clean-checkout checks, real OpenAI text/image/tool flows, independent validation and a distinct final review. Resolve Important/Critical findings and rerun affected checks. Verify formatter hash unchanged.

Check credentials/model access without exposing secrets. No monetary cap or spending approval gate. Keep bounded execution and cost reporting. If timeboxed, reserve the final quarter for validation/handoff; cut extra experiments first.

Cache only validated OCR/extraction, keyed by client/source hashes, prompt/schema, model/settings and code. Refusals, invalid or incomplete responses are failures. Invalidate dependencies on changes; measure cold/cached runs separately. Provider caching alone does not prove savings.

Independent workers may own evaluator, provider and evidence modules after contracts settle. Lead owns config, lockfile, integration and docs. Commit milestones; checkpoint progress/usage. Respect execution limits; continue offline work when useful. Keep secrets, raw traces and local stores out of Git; no unauthorised publication.

## Handoff

Deliver reports in `outputs/`, evaluation/comparison summaries, README commands and `DECISIONS.md`. Each JSON sidecar records run/input fingerprints, sources, selected facts/provenance, review items, checks, model/settings and usage/cost.

Map the assignment's six assessment criteria to reports, tests and measured results. Include one investigation trace showing the triggering gap, evidence retrieved, decision and stopping reason.

Passed drafts remain `needs_review`; human approval is separate. Blocked/failed runs return non-zero with diagnostic files, never stale reports presented as current. Test interruptions and changed-input reruns; disclose unfinished work and unavailable gates.

Provide one locked lint/format/type/test command. Verify every client's generation and the locked development environment:

```text
uv run --locked --extra dev python scripts/verify.py
uv run --locked python -m agent_pipeline.generate --client client_01_clean
```
