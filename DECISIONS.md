# Data findings and design decisions

Going to run a hexagonal architecture approach since it suits this sort of use-case where there could be a desire to switch from openai -> openrouter or bedrock in future. Wire in openrouter for V1 to show how switching would work, no live tests on it for now.

Account level Data is clean - no person entity duplication needed it seems but if it was needed would go: deterministic -> embedding -> Jev (or Luna if sticking to OpenAI) -> LLM fallback for boundry cases

Joint accounts repeat by account ID -> normalise by ID

pngs currently show duplicated data, although expect hold-out set or others to contain useful data. Read them in V1 anyway -> should not append duplicate facts, should be deduplicated against existing evidence

We need to grab latest data -> Database snapshot is 30th April 2026 -> we can see updated data in the meeting data. Logic -> compare the actual valuation dates for the same account/currency/basis, newer meeting data can replace the older figure but keep both as evidence (possibly add typo - anomoly detection here but not a top priority). Latest supported figure, not maximum amount. Client 2 (40k -> a little over 45k -> keep "a little over 45k", dont guess 45.2k for example), client 3 going from 30 - around 38k -> keep "around 38k" as about could be less than 38k. Get this included in the prompt -> no rounding into an exact figure, should be on the client facing person to get the exact value if they need. If dates/conflicts arent resolved, flag rather than just picking one

Prompt is pretty poor - needs iteration on -> Astra first pass, run on Luna across the 4 clients and flag for my review -> reviewed version becomes the base for MLFlow automated iteration. Contradiction to remove - If you do not have exact figures, give your best approximate estimate so the client has a number to work with.

Keep the current broken report as the original baseline example. New extraction/investigation prompts dont have an old equivalent -> their first versions are the starting point. Compare later prompt changes with the pipeline/model unchanged so we know what actually helped

Current output has a broken structure (main section appears 3 times) TODO: add tests to ensure our output structure is correct.

Not all data is complete - make sure prompt takes this into account

Make sure the generic material is excluded from client data (such as the 6.4%)

Will need to test the requirements -> Such as the forced text and the key values that need human review.

Tax around ISA moving is probably a test to be added.

Read JSON, DOCX paragraphs/tables, text, and images as evidence blocks; flag unsupported embedded content.

Output document is mostly deterministic, with model calls filling narrative gaps. Give each call only relevant evidence and surrounding template wording.

Use Standard pricing in V1. Later, assess Flex for non-urgent drafts prepared before a meeting; measure cost and deadline reliability before adopting it.

Need to add some reasoning around excluded funds on the bridging stuff for client 4 -> 850k received, 200k committed to the loan, leaves 650k available from the completion payment. Confirm repayment timing. The up to 400k earnout hasnt arrived and isnt guaranteed so exclude it from available funds

Architecture:

Use the Lendable approach for errors -> typed Ok / Err results at the ports, handle the error before the next step runs. Adapters convert provider exceptions, domain logic doesnt need to know which provider failed

JSON, DOCX paragraphs/tables and image transcriptions -> evidence blocks with source references. Unfamiliar material needs review. New accounts arent automatically confirmed custody/advice scope; duplicates must not create assets.

Separate observations/actions and received/committed/contingent funds. Valuations arent cash, transfers arent new wealth. Keep contributions/disposals separate. Pure functions reconcile money/accounts; bounded investigator finds missing evidence, never guesses.

Each slot gets relevant facts + surrounding wording. Code renders actions, holdings and fixed warnings; LLM explains recommendations via config prompts. Acknowledge charges/confirmation without repeating fees. Preserve config order/inclusion and supplied formatter.

Small client inputs -> explicit selection, no vector store. Narrow provider ports for switching later. Shared CLI/MLflow generation, validation and spend tracking.

Actions can depend on confirmed allowances/amounts. Unknown figures stay flagged; ambiguous identities/instructions block affected recommendations.

First implementation:

Keep source support checks separate from the maths. A quoted number being present doesnt prove it belongs to that action -> deterministic checks catch arithmetic/scope issues, model validation still has to check meaning. Neither replaces reviewing the actual reports.

Cache only after accepting the extraction/image result. Valid JSON on its own isnt enough. Failed/refused/incomplete results should get another attempt, not become cached facts.

MLflow stays optional for generation. The optimiser's own reflection calls arent metered by our provider adapter yet -> cost stays unknown rather than pretending its free. Need to sort that before claiming a cost saving.

New Responses API path got an authentication error using the local key, also checked directly from .env. Havent retested the old Chat Completions path so need to check why before blaming the key. The four reports and prompt comparison still need running. Astra prompt rewrite is saved separately for review, no autoimprove yet. Offline tests arent evidence that the live reports are good.
