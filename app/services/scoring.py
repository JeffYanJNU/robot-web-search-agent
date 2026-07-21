from urllib.parse import urlparse


GOVERNMENT_SUFFIXES = (".gov.cn", ".gov.hk", ".gov.mo", ".gov", ".gov.uk", ".europa.eu")
AUTHORITY_DOMAINS = {
    "reuters.com",
    "apnews.com",
    "bloomberg.com",
    "sec.gov",
    "people.com.cn",
    "xinhuanet.com",
    "cctv.com",
    "chinanews.com.cn",
}
INDUSTRY_MARKERS = (
    "robot",
    "robotics",
    "techcrunch",
    "theverge",
    "36kr",
    "ofweek",
    "ieee",
)


def normalize_domain(url: str | None) -> str | None:
    if not url:
        return None
    value = url.strip()
    if not value:
        return None
    if "://" not in value:
        value = "https://" + value
    domain = urlparse(value).netloc.lower().split(":", 1)[0].removeprefix("www.")
    return domain or None


def source_kind(url: str, company_website: str | None = None) -> str:
    domain = normalize_domain(url) or ""
    company_domain = normalize_domain(company_website)
    if company_domain and (domain == company_domain or domain.endswith("." + company_domain)):
        return "official"
    if domain.endswith(GOVERNMENT_SUFFIXES) or any(
        domain == item or domain.endswith("." + item) for item in AUTHORITY_DOMAINS
    ):
        return "authority"
    if any(marker in domain for marker in INDUSTRY_MARKERS):
        return "industry"
    return "other"


def calculate_priority_score(
    *,
    source_url: str,
    official_website: str | None,
    robot_relevance: int,
    has_robot_product: bool,
    has_commercial_progress: bool,
    is_priority_category: bool,
    source_count: int = 1,
    source_types: set[str] | None = None,
    independent_source_count: int | None = None,
) -> int:
    score = 0
    if normalize_domain(official_website):
        score += 15
    if robot_relevance >= 85:
        score += 20
    elif robot_relevance >= 70:
        score += 10
    if has_robot_product:
        score += 20
    if has_commercial_progress:
        score += 15
    if ((source_types or set()) & {"official", "authority"}) or source_kind(
        source_url, official_website
    ) in {"official", "authority"}:
        score += 10
    effective_source_count = (
        independent_source_count if independent_source_count is not None else source_count
    )
    if effective_source_count >= 2:
        score += 10
    if is_priority_category:
        score += 10
    return min(score, 100)


def verification_status(
    score: int,
    auto_verify_score: int = 80,
    min_priority_score: int = 60,
    *,
    auto_verify_eligible: bool = True,
) -> str:
    if score >= auto_verify_score and auto_verify_eligible:
        return "verified"
    if score >= min_priority_score:
        return "needs_review"
    return "rejected"
