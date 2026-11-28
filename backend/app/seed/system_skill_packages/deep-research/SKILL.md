---
name: deep-research
description: Conduct multi-step, citation-backed web research using organization-approved search providers.
version: 0.2.0
---

# Deep Research

Use this skill when the user asks for deep research, market research, competitor
research, technical landscape research, fact-gathering across multiple web
sources, or a citation-backed report.

## Required Tool

Use the runtime tool `research_search` for external searches. It selects an approved provider.

If a search is blocked, the budget is exhausted, or the requested time range is
unavailable, stop searching and report the limitation. Use the tool's retry
status when explaining a provider outage. Do not send the query to another
tool or broaden the requested time range.

## Workflow

1. Restate the research question and identify scope constraints. If the scope is
   ambiguous, ask a concise follow-up before searching.
2. Determine the current year from the system context and include freshness terms
   in queries when the topic depends on current information.
3. Create 3-5 search queries for ordinary research requests. If the user
   explicitly asks for deep research, competitor analysis, recent trend
   analysis, or a heavily sourced report, create 6-12 search queries that cover
   the main question, opposing evidence, recent updates, and primary-source
   candidates.
4. Call `research_search` with `query`, a focused `max_results`, and the
   requested `time_range` when applicable.
5. Track each result's original URL, title, content snippet, publication date
   when available, and actual provider. Cite the original source URL with the
   claim it supports and identify the provider in the report methodology.
6. Cross-check important claims against at least two independent sources when
   possible.
7. Treat company pages, official docs, standards bodies, filings, and primary
   research as stronger evidence than summaries.
8. Rate source quality from A to E:
   - A: peer-reviewed research, official government publications, standards, or
     major institution research.
   - B: official documentation, established organization reports, or strong
     methodology papers.
   - C: reputable expert analysis, conference material, case studies, or major
     news analysis.
   - D: preprints, company blogs, press releases, or trade publications.
   - E: anonymous, speculative, outdated, or weakly sourced material.
9. Do not invent citations, URLs, publication dates, statistics, or quotes.
10. Clearly mark uncertainty, contradictions, and evidence gaps.

## Output Format

Return a report with these sections:

- Executive Summary
- Research Question
- Methodology
- Findings
- Source Quality
- Open Questions
- References

Every factual claim that depends on web evidence should include a URL in the
same paragraph or bullet. Prefer inline citations with the source title or
organization, publication date when available, and URL.
