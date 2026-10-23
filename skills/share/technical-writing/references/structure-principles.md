# Structure Principles

Use this reference when planning, reorganizing, or reviewing information architecture.

## One Topic Per Page

Give each page one clear job.

Check:

- If the outline needs `H4` or deeper headings, split the page or simplify the hierarchy.
- If two reader goals appear in the same page, separate them or create an overview page that links to each topic.
- Keep overview pages broad and topic pages specific.

Good page titles describe one task or concept, not a whole product area.

## Value First, Cost Later

Lead with the reader's outcome before implementation details.

Check:

- The first section should answer "what can I do after reading this?"
- Move background, internal architecture, and optional details after the initial value.
- Replace feature-centered openings with reader-centered outcomes.

Use this especially for tutorials, release notes, migration guides, and technical articles.

## Effective Headings

Headings should help readers scan and search.

Check:

- Include the core keyword in the heading.
- Keep heading style consistent across sibling sections.
- Prefer plain, declarative, action-oriented headings.
- Keep headings short enough to scan, but not so short that the meaning disappears.

Avoid decorative or clever headings in documentation. Save personality for body prose where it does not hurt retrieval.

## Overview Before Detail

An overview should set expectations and help readers decide whether to continue.

Check:

- Summarize the document's core value.
- State the reader goal or problem.
- Mention important prerequisites, constraints, or supported environments.
- Do not start with deep background unless the background is the value.

## Predictable Order

Readers should be able to guess where information lives.

Common orders:

- Tutorial: goal -> prerequisites -> steps -> verify -> next steps
- Problem-solving: symptom -> cause/context -> fix -> verify -> fallback
- Reference: overview -> signature/schema -> options -> examples -> errors
- Explanation: concept -> context -> mechanism -> tradeoffs -> examples
- Article: problem -> stakes -> approach -> evidence -> lesson

Use matching section patterns across related pages.

## Sufficient Background

Explain new concepts when they first appear.

Check:

- Define unfamiliar terms before relying on them.
- Explain how a feature behaves, not only how to call it.
- Add context around references, links, screenshots, and diagrams.
- Prefer a short explanation near the point of use over a detached glossary entry.

## Cross-Linking

Use links to reduce page overload.

Check:

- Link from overview pages to focused topic pages.
- Link from tutorials to references after the guided path.
- Link from problem-solving docs to deeper explanations only after the fix.
- Do not make links carry required context that should be in the current page.
