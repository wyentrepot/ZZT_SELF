# Sentence Style

Use this reference when polishing prose. Preserve meaning first; style changes are only useful when they make the technical content easier to understand.

## Clear Subject

Make the actor clear.

Prefer:

- "The client sends a request."
- "The server returns a token."
- "You can retry the job after the status changes to `failed`."

Avoid vague subjects:

- "It is processed."
- "This is handled automatically."
- "The process is performed."

For Korean drafts, avoid forcing tools or systems into unnatural subject positions when a user or developer action is clearer.

## Active Voice

Use active voice when it clarifies responsibility.

Prefer:

- "The SDK validates the signature."
- "Call `confirmPayment` after the customer approves the payment."

Use passive voice only when the actor is unknown, irrelevant, or intentionally hidden.

## Compactness

Keep the useful information and remove meta-discourse.

Remove or rewrite:

- "In this document, we will explain..."
- "It is important to note that..."
- "As mentioned above..."
- repeated setup phrases that do not add constraints.

Do not remove warnings, assumptions, prerequisites, or edge cases just to make the page shorter.

## Concreteness

Make actions, conditions, and results observable.

Prefer:

- exact commands,
- concrete inputs and outputs,
- environment or version constraints,
- expected UI or API state,
- measurable thresholds when they matter.

Replace vague wording:

- "quickly" -> actual expected time if known,
- "properly" -> the condition that proves it works,
- "some settings" -> the specific settings.

## Natural Expression

Choose plain language over heavy nouns and literal translation patterns.

For English:

- Prefer direct verbs over noun stacks.
- Avoid inflated phrases such as "utilize" when "use" is enough.
- Keep sentence rhythm varied, but do not become editorial if the document is operational.

For Korean:

- Remove unnecessary Sino-Korean nouns when a natural verb phrase is clearer.
- Rewrite literal translation patterns into idiomatic Korean.
- Preserve formal register when the source requires it; naturalness is not casualness.

## Terminology Consistency

Use one term for one concept.

Check:

- Follow official product, API, and framework names.
- Expand an acronym on first use unless the audience certainly knows it.
- Do not alternate between synonyms for the same technical concept.
- Keep foreign word transliteration consistent in Korean drafts.

## Code And Identifier Safety

Never polish inside code blocks unless the user asks.

Preserve:

- command names and flags,
- API identifiers and parameters,
- config keys,
- exact error messages,
- version numbers,
- URLs,
- quoted legal, security, or compliance text.
