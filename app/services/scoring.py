from urllib.parse import urlparse


GOVERNMENT_SUFFIXES = (".gov.cn", ".gov.hk", ".gov.mo")
AUTHORITY_DOMAINS = {"people.com.cn", "xinhuanet.com", "cctv.com", "chinanews.com.cn"}
INDUSTRY_MARKERS = ("robot", "tech", "36kr", "sohu", "sina", "qq.com", "ofweek")


def source_kind(url: str, company_website: str | None = None) -> str:
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    company_domain = urlparse(company_website or "").netloc.lower().removeprefix("www.")
    if company_domain and (domain == company_domain or domain.endswith("." + company_domain)):
        return "official"
    if domain.endswith(GOVERNMENT_SUFFIXES) or any(domain == d or domain.endswith("." + d) for d in AUTHORITY_DOMAINS):
        return "authority"
    if any(marker in domain for marker in INDUSTRY_MARKERS):
        return "industry"
    return "other"


def calculate_confidence(
    url: str,
    has_date: bool,
    company_name: str,
    product_name: str,
    source_count: int = 1,
    company_website: str | None = None,
) -> int:
    score = {"official": 40, "authority": 30, "industry": 20, "other": 0}[source_kind(url, company_website)]
    score += 15 if has_date else 0
    score += 15 if company_name.strip() and product_name.strip() else 0
    score += 20 if source_count >= 2 else 0
    return min(score, 100)


def review_status(score: int) -> str:
    if score >= 80:
        return "accepted"
    if score >= 60:
        return "pending"
    return "weak"

