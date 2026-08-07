# Writing Guide: Non-Native Reader-Friendly Documentation

This guide defines how to write the project's documentation so that it is
easy to read for **non-native English speakers** and easy to translate for
**machine translation engines and LLMs**. It is inspired by
[ASD-STE100 Simplified Technical English](https://www.asd-ste100.org), the
aerospace standard for controlled technical language, adapted for software
documentation.

The project's primary audience includes operators and developers worldwide
whose first language is not English. Clear, simple, unambiguous prose is a
correctness requirement for the docs, not a stylistic preference.

## 1. Sentence Length Limits

| Document type | Max words per sentence |
|---|---|
| Descriptive prose (architecture, guides, README body) | 25 |
| Instructions / procedure steps | 20 |
| Notes | 25 |

Long sentences are the single biggest readability failure in technical
documentation. Split them.

**Before (28 words):**
> The streaming engine is selected when the response exceeds the buffer
> threshold and the configured policy permits incremental delivery, which
> requires the cache validation mode not to be `full`.

**After (2 sentences, 14 + 12 words):**
> The streaming engine is selected when the response exceeds the buffer
> threshold. The policy must permit incremental delivery, and cache
> validation must not be `full`.

## 2. Rules at a Glance

| Rule | Do | Don't |
|---|---|---|
| Voice | Use active voice | "The config is read by the module" → "The module reads the config" |
| Instructions | Use the imperative | "The user should run..." → "Run `make harness-check`" |
| One topic | One topic per sentence | Stacked clauses with `and`/`which`/`that` |
| Semicolons | Split into two sentences | `;` in prose (allowed in code) |
| Contractions | Write in full | `don't`, `isn't`, `can't`, `it's` |
| Latin abbreviations | Spell out | `e.g.` → "for example", `i.e.` → "that is", avoid `etc.` |
| Multi-word nouns | Max 3 words, use prepositions | "runway light connection resistance calibration" |
| Paragraphs | Max 6 sentences, one topic each | Walls of text |
| Pronouns | Repeat the noun when ambiguous | "It can cause damage" (which "it"?) |
| Terminology | One term per concept | Mixing "directive" / "config key" / "option" |
| Spelling | American English | British spellings (`behaviour`, `colour`) |

## 3. Writing Procedures (Instructions)

1. Use the imperative form: "Remove the four screws.", "Run the check."
2. One instruction per sentence unless two actions happen at the same time.
3. When a condition must be known first, state it, then the command:
   - "When you supply hydraulic pressure, make sure that the lever does not
     touch the stop."
4. Number work steps. Each step is one sentence.

## 4. Writing Descriptive Text

1. Give information gradually — one fact per sentence.
2. Use connecting words to show how ideas relate: "and", "but", "because",
   "when", "if", "as a result".
3. Each paragraph has one topic and at most six sentences.
4. Use vertical lists for enumerations (see the docs' list style).

## 5. Words That Cause Confusion (avoid or clarify)

- **"with"** — can mean association, help, or instrument:
  - Ambiguous: "Install the panel with the green fasteners."
  - Clear: "Install the panel that has the green fasteners." / "Use the
    green fasteners to install the panel."
- **"this" / "it"** — repeat the noun when the referent is not obvious.
- **False friends** — a word may look like a word in the reader's language
  but mean something different in English. Prefer short, common words.
- **Latin abbreviations** — spell out ("for example", "that is").

## 6. Machine-Translation Friendly Writing

The docs are consumed by translators, NMT engines, and LLMs:

- Use "that" to introduce subordinate clauses: "Make sure **that** the file
  exists." (many target languages cannot omit the equivalent word)
- Keep the subject near the verb.
- Do not omit words to shorten sentences.
- Use the same term for the same concept throughout a document.

## 7. Localization (README_zh-CN.md)

The Chinese mirror must stay **1:1 structurally aligned** with the English
README:

- Same section headings (translated), same tables, same command blocks.
- Never add English-only sections or Chinese-only sections.
- Update both files in the same commit.

## 8. Checking Your Work

Run the advisory style checker (warning-only, never edits files):

```bash
python3 tools/docs/check_writing_style.py [--strict] [--limit N]
```

- Default: reports warnings, exits 0 — safe for CI and local use.
- `--strict`: exits 1 when warnings exist (opt-in gate).
- `--limit N`: cap warnings per file.
- The checker excludes code blocks, tables, headings, and inline code. It
  scans prose only.

## 9. Reference

- Full standard: [ASD-STE100](https://www.asd-ste100.org) (free to use).
- This guide's quantified limits come from ASD-STE100 Issue 9, Part 1,
  sections 5–8.

## Document Updates

| Version | Date | Changes |
| --- | --- | --- |
| 0.9.2 | 2026-08-07 | Pilot: create non-native reader-friendly writing guide (STE-inspired) for contributors. |
