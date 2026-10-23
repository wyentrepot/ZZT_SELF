# Prompt Workflows

Use these patterns when the user asks for reusable prompts, AI-assisted writing workflows, or team review bots.

## Drafting Prompt

```text
You are a technical writer for developer-facing documentation.

Goal:
[What the reader should be able to do or understand]

Audience:
[Experience level, role, assumptions]

Document type:
[tutorial | problem-solving | reference | explanation | technical article]

Source material:
[Facts, code, API behavior, constraints, links, notes]

Instructions:
- Preserve all technical facts, identifiers, commands, and version numbers.
- Use the structure for the selected document type.
- Lead with reader value before implementation details.
- Mark missing information as TODO instead of inventing it.
- Include verification steps where the reader performs an action.
```

## Structure Prompt

```text
Turn these notes into a technical document outline.

Audience:
[Reader]

Reader goal:
[Task or understanding]

Notes:
[Raw notes]

Instructions:
- Choose the best document type.
- Use one primary topic per page.
- Put value and overview before details.
- Suggest cross-links for material that should be split out.
- Return only the outline, with a short rationale.
```

## Review Prompt

```text
Review this draft using the technical-writing rubric.

Focus:
[structure | clarity | examples | completeness | sentence style | all]

Draft:
[Paste draft or file path]

Instructions:
- Lead with severity-ranked findings.
- Cite section names or line numbers when available.
- Preserve facts and code.
- Suggest concrete fixes.
- Do not rewrite the whole draft unless requested.
```

## Sentence Polish Prompt

```text
Polish this developer-facing text.

Constraints:
- Preserve code, commands, API names, version numbers, warnings, and product behavior.
- Do not add new claims.
- Make subjects and actions clear.
- Remove meta-discourse and vague wording.
- Keep the original register.

Text:
[Draft]
```

## Prompt Review Checklist

- Does the prompt state the reader and document type?
- Does it separate source facts from instructions?
- Does it tell the model not to invent missing facts?
- Does it include preservation rules for technical surfaces?
- Does it define the expected output shape?
