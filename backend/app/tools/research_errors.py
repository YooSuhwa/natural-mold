"""Public tool failures after an authorized search may already have sent a query."""

from app.services.research_search_policy import SearchPolicyError
from app.tools.definitions.search_transport import ResearchSearchError

RESEARCH_SEARCH_KEYS = frozenset({"research_search", "tavily_search"})


def public_search_failure(error: Exception) -> ResearchSearchError:
    if isinstance(error, ResearchSearchError):
        return error
    if isinstance(error, SearchPolicyError):
        return ResearchSearchError(str(error))
    # A decoder or storage failure can follow a real provider call. Do not leak its
    # inputs or ask the generic tool middleware to resend the whole search.
    return ResearchSearchError("Research search could not be completed. Retry later.")
