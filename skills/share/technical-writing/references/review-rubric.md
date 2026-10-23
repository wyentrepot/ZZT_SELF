# Review Rubric

Use this reference when the user asks for a review, critique, or quality pass.

## Severity Labels

- **Blocker**: The draft could mislead readers, cause implementation failure, omit required safety/context, or contradict product behavior.
- **Major**: The draft is usable but has structural, completeness, or clarity problems that will slow readers down.
- **Minor**: The draft is mostly sound but has local clarity, consistency, or style issues.
- **Nit**: Small wording, formatting, or polish issue.

## Review Order

1. Reader and goal fit
2. Document type and structure
3. Technical completeness and factual safety
4. Examples, code, and verification
5. Sentence-level clarity
6. Terminology and consistency

## Checklist

### Reader And Goal

- Is the intended reader clear?
- Does the opening explain what the reader gains?
- Are prerequisites and assumptions explicit?

### Structure

- Does the page serve one primary goal?
- Is the section order predictable for the document type?
- Are headings keyword-rich and consistent?
- Is background placed after the reader understands the value?

### Technical Completeness

- Are important versions, environments, and constraints included?
- Are warnings and failure modes visible before risky steps?
- Are claims backed by examples, behavior, or evidence?
- Are TODOs or unknowns clearly marked instead of guessed?

### Examples And Verification

- Are code samples runnable or clearly illustrative?
- Does each workflow include an expected result?
- Are edge cases or common errors covered where they affect implementation?

### Sentence Quality

- Are actors and actions clear?
- Are sentences compact without losing required context?
- Are vague nouns replaced with concrete verbs, values, or states?
- Is terminology consistent?

## Output Format

When reviewing, lead with findings:

```markdown
## Findings

- **Major** - Section "Configure webhooks": The draft says to retry failed events but does not define which status values are safe to retry. Add the exact retryable statuses and one non-retryable example.
- **Minor** - Section "Overview": The opening explains internal architecture before the reader outcome. Move the outcome sentence first.

## Summary

The draft has the right document type, but it needs clearer prerequisites and verification steps before publication.

## Suggested Rewrite

[Include only if the user asked for a rewrite or the fix is small enough to show directly.]
```

Keep reviews specific. Avoid generic praise or style opinions that do not change the document.
