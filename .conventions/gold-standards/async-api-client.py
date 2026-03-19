"""Gold standard: async HTTP client with httpx.

Rules:
- Use httpx.AsyncClient as a context manager.
- Set an explicit timeout constant (never rely on defaults).
- Call resp.raise_for_status() after every request.
- Catch httpx.HTTPError for graceful degradation.
- Return a typed result (not raw response).
Extracted from publisher.py.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT: int = 30


async def publish_github_issue(digest, config) -> str | None:
    """Create a GitHub issue. Returns the issue URL or None on failure."""
    url = f"https://api.github.com/repos/{config.github_repository}/issues"
    headers = {
        "Authorization": f"Bearer {config.github_token}",
        "Accept": "application/vnd.github+json",
    }
    payload = {"title": f"Digest: {digest.date}", "body": digest.markdown}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url, json=payload, headers=headers, timeout=_HTTP_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()["html_url"]
    except httpx.HTTPError as exc:
        logger.error("Failed to create GitHub issue: %s", exc)
        return None
