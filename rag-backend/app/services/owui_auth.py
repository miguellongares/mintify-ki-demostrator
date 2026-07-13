"""
OwuiAuth — resolve the calling user from their OWUI session token
-----------------------------------------------------------------
The browser calls the rag-backend directly (like the Bewertung panel) and
forwards its OWUI token as a Bearer header. We verify it against OWUI's own
auth endpoint — identity stays in OWUI, the rag-backend never stores users.

  GET {OWUI_BASE_URL}/api/v1/auths/   with Authorization: Bearer <token>
  → 200 + user object {id, name, email, role, ...} if the token is valid
"""

import httpx


class OwuiAuth:
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")

    def resolve_user(self, token: str) -> dict | None:
        try:
            response = httpx.get(
                f"{self._base}/api/v1/auths/",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            print(f"[PersonalDocs] OWUI auth request failed: {exc}")
            return None
        if response.status_code != 200:
            return None
        user = response.json()
        return user if user.get("id") else None
