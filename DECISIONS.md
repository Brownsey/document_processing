## Write up

I changed the model used to GPT 6 luna as this is cheaper than the current model (gpt-4o-mini) and has a significantly better benchmark. It also stays within the OpenAI family that you use. If I were to consider other alternatives or was looking at self-hosting I would look towards DS 4.1 flash as this is my standard OpenRouter model I go to.

Provided key lacks Responses write access: the live API returned HTTP 401 with missing scope `api.responses.write`. Chat Completions succeeds with the same configured credentials. As such for this assignment we will utilise chat completions but if you were taking this further we would look to migrate to responses as is aligned with OpenAIs most recent recommendations.

The blocking rules are quite harsh at the moment, feel free to use --publish-anyway, this will guarantee that it publishes the output

I ran a few checks of the code through the API on Saturday, on Sunday the same query returns an account deactivated error message: Error code: 401 - {'error': {'message': 'The OpenAI account associated with this API key has been deactivated. If you are the developer for this OpenAI app, please check your email for more information. If you are seeing this error while using another app or site, please reach out to them for more help.', 'type': 'invalid_request_error', 'code': 'account_deactivated', 'param': None}, 'status': 401}

Because of this I'll then just use my own Openrouter key set to luna 6 for the final runs as well so it keeps it consistent with what openai would have produced. I've wired in an OpenRouter option via cli too for this.

Because of this instead of running the MLFLOW prompt evaluation approach, I'll run codex loops instead. This is the process whereby instead of feeding the inputs the chat model endpoint I simply take what would have been sent to the api and instead of sending it to the MLFlow evaluate and optimize_prompts endpoint we mock this up with our subagent routing in codex. The luna subagent returns what the output would have been (slight caveat this runs luna light but chatcompletions runs with no reasoning but it gives a good rough idea). We spin up a load of test criteria (feed in half a dozen examples and get our stronger model (Sol) to negatively review the outputted text against the requirements. Every time it finds a mistake it adds this as a test and this just scales with our example loops. We iterate until it doesn't find any more examples and all Clients 1-4 are passing green) - at this point we run it through out now openrouter api endpoint and assess the output md files manually.

A production system would want to run the MLFLOW approach or another automated version control of prompts but for a starting point the codex looping is sufficient.

The architecture runs the hexagonal port/domain flow for it's fail-fast and ease of switching out endpoints if we think about openai/bedrock/openrouter.

Adding reasoning-effort as a cli arg, for the cost, running luna medium is better than luna-low. So setting that as a default (costs are still extremely low so this is worth the cost increase in my opinion - as on the testing it resolved a few cases which failed the validation layer on low)

The initial loops, returned usable outputs. However, they followed the requirements a bit too strongly and did not pick up on any issues that may arise from the intended approach of the adviser. This may be strict requirements, but in my opinion it is more useful if the output can flag these for the adviser to review. Simple example is the client 2 sell all of the 45k holdings to top up the ISA allowance of both (but they have already used some of their combined 40k allowance) - what happens to the remaining cash. Currently unsure.

So I added the adviser queries aspect - this is currently forced but could also be pinged to a cli arg as it may be something that some advisers don't like - I will implement this but in reality it would probably be an FDE business case discussion to decide whether this is required. This could arguably be made shorter to just include things that look incorrect, but also flagging things like Jeans cash ISA feels useful to me? I've added a CLI mode for this to be either full or discrepency to handle what would be displayed in this area. But for the purpose of this I'm just going to using and testing full.

## Extensions

 - I think the feedback loop to advisers could be improved and optimised to provide geniunely useful feedback, at the moment it feels like it could be a bit hit and miss
 - I think it could be quite interesting to test the jev model or locally fine tuned Laya/Julia model as an additional step on top of the deterministic layer, particularly around choosing inputs and routing, it would allow the expansion into new datasources without really needing to change the deterministic code base.
 - I could have used codex to come up with my own
 - Some of the outputted text feels fairly robotic, particularly client 4. This could be improved by iterating on the tone_of_voice and global_instructions aspect of the prompt logic
 - For a more production led implementation we could think about concurrency and actual performance at scale
 - The logic that forces it into the failure re-routing may be a bit harsh at the moment and could do with improvement over time with a larger sample size

# Initial thoughts braindump

 - Ensure the output structure is no longer broken and matches the requirements
 - Ensure accounts are normalised by ID - Joint accounts repeat by account ID
 - No requirement for personal entity deduplication but if it was needed deterministic -> embedding -> Jev (or Luna if sticking to OpenAI) -> LLM fallback for boundry cases
 - OCR, luna can take in images so in theory can pass through, better approach, ocr step to generate text and compare that text with the other files. Currently all the .png content is duplicated but it may not be in the future
 - Ensure the latest data is always grabbed (Database snapshot is 30th April 2026, notes contain more recent information)
 - For values that are not accurate (a little over 45, around 38k - we should take the minimimum confirmed value, so for the over 45 we take 45 for the around 38 we would also take 38 as even though it may be a little lower as 38k is the value to hand, anything more specific is on the guy talking to the client to get)
 - Ensure output structure is valid, a lot is deterministcally required so should not be included in the prompt.
 - Ensure generic material is excluded from client data (such as the fund performance values of 6.4%)
 - Ensure ISA tax logic handled correctly
 - Ensure all input types correctly passed and flag any unsupported content
 - For this approach use the standard endpoint, future enhancement would to use flex pricing via a submit/get approach and create the reports automatically based on the calendars of people. So they log in the morning and have the X reports generated for them for their daily meetings.
 - Ensure complexity of the 200k bridging payment stuff for client 4 is handled
 - No need for a vector store at this stage for anything
 - Ensure the LLM only edits code in the areas it should and only brings in required data to prompt
 - Use typed results for errors -> typed Ok / Err results at the ports, handle the error before the next step runs. Adapters convert provider exceptions, domain logic doesnt need to know which provider failed
 - Separate observations/actions and received/committed/contingent funds. Valuations arent cash, transfers arent new wealth. Keep contributions/disposals separate. Pure functions reconcile money/accounts; bounded investigator finds missing evidence, never guesses.

 ## Terminal commands
.\generate_all.bat --publish-anyway
uv run --locked python -m agent_pipeline.generate --client client_01_clean --provider openrouter --model openai/gpt-6-luna
uv run --locked python -m agent_pipeline.generate --client client_02_medium --provider openrouter --model openai/gpt-6-luna
uv run --locked python -m agent_pipeline.generate --client client_03_hard --provider openrouter --model openai/gpt-6-luna
uv run --locked python -m agent_pipeline.generate --client client_04_stretch --provider openrouter --model openai/gpt-6-luna