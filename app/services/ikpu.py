import asyncio

import httpx

from app.services.uzum_template import _word_similarity, _words

API = "https://tasnif.soliq.uz/api/cls-api/mxik/"
GENERIC_SUFFIX = "000000"
AUTO_SCORE = 0.75


async def suggest_ikpu(queries: list[str], target_text: str) -> list[dict]:
    """tasnif.soliq.uz ommaviy qidiruvidan umumiy (brendsiz) IKPU kodlarini topadi; xato bo'lsa bo'sh ro'yxat."""
    unique = list(dict.fromkeys(query.strip() for query in queries if query and query.strip()))[:8]
    if not unique:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            batches = await asyncio.gather(*(_search(client, query) for query in unique))
    except httpx.HTTPError:
        return []
    found: dict[str, str] = {}
    for items in batches:
        for item in items:
            code, name = item.get("mxikCode"), item.get("subPositionName") or item.get("name")
            if (isinstance(code, str) and code.endswith(GENERIC_SUFFIX) and code.isdigit()
                    and isinstance(name, str) and name):
                found.setdefault(code, name)
    target = _words(target_text)
    ranked = [{"code": code, "name": name, "score": _coverage(_words(name), target)} for code, name in found.items()]
    ranked.sort(key=lambda item: (-item["score"], len(item["name"])))
    return [{**item, "score": round(item["score"], 3)} for item in ranked[:8]]


def recommended(candidates: list[dict]) -> str | None:
    return candidates[0]["code"] if candidates and candidates[0]["score"] >= AUTO_SCORE else None


async def _search(client: httpx.AsyncClient, query: str) -> list[dict]:
    results = await asyncio.gather(
        _get(client, "search-subposition", {"search_text": query}),
        _get(client, "search/by-params", {"text": query}),
    )
    return [item for result in results for item in result]


async def _get(client: httpx.AsyncClient, endpoint: str, params: dict) -> list[dict]:
    try:
        response = await client.get(API + endpoint, params={"lang": "ru", "size": 10, "page": 0, **params})
        response.raise_for_status()
        data = (response.json() or {}).get("data")
    except (httpx.HTTPError, ValueError):
        return []
    content = data.get("content") if isinstance(data, dict) else data
    return [item for item in content or [] if isinstance(item, dict)]


def _coverage(name_words: list[str], target_words: list[str]) -> float:
    if not name_words or not target_words:
        return 0.0
    return sum(max((max(_word_similarity(word, other), _stem_similarity(word, other)) for other in target_words),
                   default=0.0) for word in name_words) / len(name_words)


def _stem_similarity(first: str, second: str) -> float:
    return 0.8 if min(len(first), len(second)) >= 5 and first[:5] == second[:5] else 0.0
