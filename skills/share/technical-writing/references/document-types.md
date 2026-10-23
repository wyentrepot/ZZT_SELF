# Document Types

Use this reference to choose the writing shape before drafting or reorganizing a document.

## Quick Selection

| Type | Reader goal | Use when | Required shape |
| --- | --- | --- | --- |
| Learning or tutorial | Learn a new tool or workflow from zero | The reader needs a guided path | Goal, prerequisites, steps, verification, next steps |
| Problem-solving | Fix a specific problem | The reader has context and needs action | Problem, cause or context, solution, verification, alternatives |
| Reference | Look up exact behavior | The reader knows the concept and needs precision | Overview, signature or schema, parameters, return values, examples, edge cases |
| Explanation | Understand why or how something works | The reader needs background and mental models | Concept, context, mechanism, tradeoffs, examples |
| Technical article | Communicate insight, decision, or experience | The reader wants a useful narrative | Problem, stakes, approach, result, lessons, next action |

## Learning Or Tutorial

Use a tutorial when the reader is unfamiliar with the tool or workflow.

Require:

- a concrete outcome at the top,
- prerequisites and environment assumptions,
- ordered steps that can be followed without hidden jumps,
- runnable code or commands,
- verification after meaningful milestones,
- links to deeper reference material after the main path.

Avoid:

- explaining every internal detail before the first useful result,
- mixing multiple independent goals in one page,
- leaving the reader without a way to confirm success.

Template: `../assets/templates/learning-doc.md`

## Problem-Solving

Use a problem-solving document when the reader can recognize a symptom and wants a fix.

Require:

- a precise problem statement,
- affected environments or versions,
- direct solution steps,
- expected result after the fix,
- fallback or escalation path when the first solution does not work.

Avoid:

- burying the fix after a long history,
- naming the problem vaguely,
- omitting environment-specific differences.

Template: `../assets/templates/problem-solving-doc.md`

## Reference

Use a reference document when accuracy and completeness matter more than narrative flow.

Require:

- stable naming and terminology,
- complete parameter or option lists,
- types, defaults, constraints, and return values,
- short examples for common use,
- edge cases and errors that change implementation choices.

Avoid:

- marketing language,
- incomplete tables,
- hidden defaults,
- examples that do not compile or cannot be applied.

Template: `../assets/templates/reference-doc.md`

## Explanation

Use an explanation when the reader needs a mental model.

Require:

- the concept and why it exists,
- background and constraints,
- mechanism or architecture,
- tradeoffs and failure modes,
- concrete examples or diagrams when useful.

Avoid:

- turning the explanation into a step-by-step tutorial,
- assuming unexplained domain knowledge,
- making claims without giving enough context.

Template: `../assets/templates/explanation-doc.md`

## Technical Article

Use a technical article when the goal is to share a decision, lesson, engineering story, or technical insight.

Require:

- a reader-relevant problem or tension,
- the value of reading now,
- enough technical context to trust the story,
- concrete implementation details or examples,
- a useful conclusion grounded in the article.

Avoid:

- vague "we improved things" narratives,
- dramatic claims without evidence,
- long background before the reader understands the payoff.

Template: `../assets/templates/technical-article.md`
