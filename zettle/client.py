"""Zettle API Client managing authentication, token refreshing, persistent caching, and HTTP requests."""

import os
import json
import time
import base64
from typing import Any, Dict, Optional, Union
from pathlib import Path
import requests
from dotenv import load_dotenv

from .exceptions import ZettleError, ZettleAuthError, ZettleNotFoundError, ZettleRequestError


# Default URLs
OAUTH_TOKEN_URL = "https://oauth.zettle.com/token"
PRODUCTS_BASE_URL = "https://products.izettle.com"
DEFAULT_CACHE_FILENAME = ".zettle_token_cache.json"


class ZettleClient:
    """Handles OAuth 2.0 token acquisition, persistent caching to disk, and HTTP communication with Zettle APIs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        env_file: Optional[Union[str, Path]] = None,
        base_url: str = PRODUCTS_BASE_URL,
        token_cache_file: Optional[Union[str, Path]] = None,
    ):
        """
        Initialize the Zettle client.
        
        Args:
            api_key: Zettle API key (JWT assertion). If not provided, loaded from .env.
            client_id: Zettle Client ID. If not provided, loaded from .env or extracted from JWT.
            env_file: Path to custom .env file. Defaults to .env in current or root directory.
            base_url: Base URL for Zettle Products API.
            token_cache_file: Path to persistent token cache file. Defaults to .zettle_token_cache.json.
        """
        # Load environment variables
        if env_file:
            load_dotenv(dotenv_path=env_file)
            self._project_dir = Path(env_file).resolve().parent
        else:
            load_dotenv()
            self._project_dir = Path.cwd()

        self.api_key = api_key or os.getenv("ZETTLE_API_KEY")
        self.client_id = client_id or os.getenv("ZETTLE_CLIENT_ID")
        self.base_url = base_url.rstrip("/")

        if not self.api_key:
            raise ZettleAuthError(
                "Zettle API key not found. Please set ZETTLE_API_KEY in your .env file "
                "or pass api_key to the function."
            )

        # Attempt to auto-extract client_id from JWT payload if not provided
        if not self.client_id:
            self.client_id = self._extract_client_id_from_jwt(self.api_key)

        # Persistent token cache path
        if token_cache_file:
            self.token_cache_path = Path(token_cache_file).resolve()
        else:
            self.token_cache_path = (self._project_dir / DEFAULT_CACHE_FILENAME).resolve()

        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

        # Attempt eager load of valid persistent token from disk
        self._load_token_cache()

    @staticmethod
    def _extract_client_id_from_jwt(jwt_token: str) -> Optional[str]:
        """Attempt to extract client_id or iss claim from a JWT assertion payload."""
        try:
            parts = jwt_token.strip().split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1]
                # Fix padding
                payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
                payload_json = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
                payload = json.loads(payload_json)
                return payload.get("client_id") or payload.get("iss")
        except Exception:
            pass
        return None

    def _load_token_cache(self) -> bool:
        """
        Load persisted access token from disk cache if valid and not expired.
        
        Returns:
            True if a valid unexpired token was loaded, False otherwise.
        """
        if not self.token_cache_path.exists():
            return False

        try:
            with open(self.token_cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            access_token = data.get("access_token")
            expires_at = float(data.get("expires_at", 0))
            cached_client_id = data.get("client_id")

            # Check matching client_id if available
            if self.client_id and cached_client_id and self.client_id != cached_client_id:
                return False

            # Require at least 60 seconds buffer before expiration
            now = time.time()
            if access_token and (expires_at - now) > 60:
                self._access_token = access_token
                self._token_expires_at = expires_at
                return True
        except Exception:
            pass

        return False

    def _save_token_cache(self, access_token: str, expires_in: int) -> None:
        """Persist access token and expiration timestamp to disk."""
        now = time.time()
        # 60 seconds safety buffer
        expires_at = now + max(60, expires_in - 60)
        cache_data = {
            "access_token": access_token,
            "expires_at": expires_at,
            "client_id": self.client_id,
            "created_at": now,
            "expires_in": expires_in,
        }
        try:
            self.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            # Write with restricted permissions (0600)
            temp_path = self.token_cache_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2)
            os.chmod(temp_path, 0o600)
            temp_path.replace(self.token_cache_path)
            self._access_token = access_token
            self._token_expires_at = expires_at
        except Exception:
            # Fallback in-memory
            self._access_token = access_token
            self._token_expires_at = expires_at

    def clear_token_cache(self) -> None:
        """Invalidate in-memory and on-disk token cache."""
        self._access_token = None
        self._token_expires_at = 0.0
        try:
            if self.token_cache_path.exists():
                self.token_cache_path.unlink()
        except Exception:
            pass

    def get_token_info(self) -> Dict[str, Any]:
        """
        Get metadata about the current persistent token and expiration.
        
        Returns:
            JSON-serializable dictionary with token status.
        """
        now = time.time()
        # Make sure cache is checked
        if not self._access_token:
            self._load_token_cache()

        has_valid_token = bool(self._access_token and now < self._token_expires_at)
        remaining = max(0, int(self._token_expires_at - now)) if has_valid_token else 0

        return {
            "is_authenticated": has_valid_token,
            "has_api_key": bool(self.api_key),
            "client_id": self.client_id,
            "persisted_on_disk": self.token_cache_path.exists(),
            "cache_file": str(self.token_cache_path),
            "expires_in_seconds": remaining,
            "expires_in_minutes": round(remaining / 60, 1),
            "token_masked": (
                f"{self._access_token[:8]}...{self._access_token[-8:]}"
                if self._access_token and len(self._access_token) > 16
                else None
            ),
        }

    def get_access_token(self, force_refresh: bool = False) -> str:
        """
        Retrieve a valid OAuth access token.
        Uses in-memory cache, then persistent disk cache, and only hits Zettle OAuth if needed.
        
        Returns:
            Bearer access token string.
        """
        now = time.time()
        if not force_refresh:
            # 1. Check in-memory cache
            if self._access_token and now < self._token_expires_at:
                return self._access_token
            # 2. Check disk cache
            if self._load_token_cache():
                return self._access_token

        # Check if client_id is missing
        if not self.client_id:
            # If still no client_id, try using the api_key directly as bearer token
            self._access_token = self.api_key
            self._token_expires_at = now + 3600
            self._save_token_cache(self._access_token, 3600)
            return self._access_token

        payload = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "client_id": self.client_id,
            "assertion": self.api_key,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            response = requests.post(OAUTH_TOKEN_URL, data=payload, headers=headers, timeout=15)
        except requests.RequestException as e:
            raise ZettleRequestError(f"Network error while connecting to Zettle token endpoint: {e}")

        if response.status_code != 200:
            error_data = {}
            try:
                error_data = response.json()
            except Exception:
                pass
            error_desc = error_data.get("error_description") or response.text or f"HTTP {response.status_code}"

            # Fallback to direct token if already an access token
            if response.status_code in (400, 401) and self.api_key.startswith("ey"):
                self._access_token = self.api_key
                self._token_expires_at = now + 3600
                self._save_token_cache(self._access_token, 3600)
                return self._access_token

            raise ZettleAuthError(
                f"Failed to authenticate with Zettle (HTTP {response.status_code}): {error_desc}. "
                "Ensure your ZETTLE_API_KEY and ZETTLE_CLIENT_ID in .env are correct."
            )

        token_data = response.json()
        new_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 7200)

        # Save to memory and disk cache
        self._save_token_cache(new_token, expires_in)
        return self._access_token

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """
        Execute an authenticated HTTP request to Zettle API.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: Relative API path (e.g., '/organizations/self/products/v2')
            params: URL query parameters
            json_data: JSON payload
            headers: Extra HTTP headers
            
        Returns:
            JSON-serializable Python data (dict or list), or True for empty 204 responses.
        """
        token = self.get_access_token()

        req_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if json_data is not None:
            req_headers["Content-Type"] = "application/json"
        if headers:
            req_headers.update(headers)

        url = f"{self.base_url}/{path.lstrip('/')}"

        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                params=params,
                json=json_data,
                headers=req_headers,
                timeout=30,
            )
        except requests.RequestException as e:
            raise ZettleRequestError(f"HTTP request to '{url}' failed: {e}")

        # Check for 401 Unauthorized (token revoked or expired early)
        if response.status_code == 401:
            # Refresh token once, update persistent cache, and retry
            token = self.get_access_token(force_refresh=True)
            req_headers["Authorization"] = f"Bearer {token}"
            try:
                response = requests.request(
                    method=method.upper(),
                    url=url,
                    params=params,
                    json=json_data,
                    headers=req_headers,
                    timeout=30,
                )
            except requests.RequestException as e:
                raise ZettleRequestError(f"HTTP request retry to '{url}' failed: {e}")

        if response.status_code == 404:
            raise ZettleNotFoundError(f"Resource at '{url}' was not found (404).")

        if response.status_code == 412:
            raise ZettleRequestError(
                f"Precondition failed (412) for '{url}'. The resource was modified concurrently.",
                status_code=412,
                response_body=response.text,
            )

        if not response.ok:
            error_body = response.text
            raise ZettleRequestError(
                f"Zettle API error ({response.status_code}) on {method} {url}: {error_body}",
                status_code=response.status_code,
                response_body=error_body,
            )

        if response.status_code == 204 or not response.content:
            return {"success": True, "status_code": response.status_code}

        try:
            return response.json()
        except Exception:
            return {"raw_response": response.text, "status_code": response.status_code}
