---
name: deep-research
description: Conduct multi-step, citation-backed web research using Tavily Search.
version: 0.1.0
---

# Deep Research

Use this skill when the user asks for deep research, market research, competitor
research, technical landscape research, fact-gathering across multiple web
sources, or a citation-backed report.

## Required Tool

You have a runtime tool named `tavily_search`.

If `tavily_search` is unavailable or returns a backend configuration error, stop
and tell the user that the backend operator must configure `TAVILY_API_KEY`.

## Workflow

1. Restate the research question and identify scope constraints. If the scope is
   ambiguous, ask a concise follow-up before searching.
2. Determine the current year from the system context and include freshness terms
   in queries when the topic depends on current information.
3. Create 6-12 search queries that cover the main question, opposing evidence,
   recent updates, and primary-source candidates.
4. Call `tavily_search` for each query. Use `search_depth="advanced"` when
   accuracy matters more than latency, and keep `max_results` focused.
5. Track source title, URL, publication date when available, relevance, and the
   claim each source supports.
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
