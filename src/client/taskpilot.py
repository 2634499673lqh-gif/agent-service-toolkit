"""Small synchronous client for the protected TaskPilot Product API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx


class TaskPilotClientError(Exception):
    """Safe, user-displayable Product client failure."""

    def __init__(self, message: str, *, kind: str = "error", status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


@dataclass(frozen=True)
class LoginResult:
    token: str | None = None
    organization_ids: tuple[UUID, ...] = ()

    @property
    def requires_organization(self) -> bool:
        return bool(self.organization_ids)


class TaskPilotClient:
    """Per-Streamlit-session transport; it never stores credentials globally."""

    timeout = 10.0

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def __repr__(self) -> str:
        return f"TaskPilotClient(base_url={self.base_url!r}, authenticated={bool(self.token)!r})"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers,
                timeout=self.timeout,
                **kwargs,
            )
        except httpx.TimeoutException:
            raise TaskPilotClientError("Request timed out.", kind="timeout") from None
        except httpx.RequestError:
            raise TaskPilotClientError(
                "Could not reach the TaskPilot service.", kind="network"
            ) from None
        if response.status_code == 401:
            self.token = None
            raise TaskPilotClientError(
                "Your session has expired. Please sign in again.",
                kind="unauthorized",
                status_code=401,
            )
        if response.status_code >= 400 and not (
            method == "POST" and path == "/api/v1/auth/login" and response.status_code == 409
        ):
            if response.status_code == 404:
                message = "The requested resource was not found."
            elif response.status_code == 409:
                message = "This request could not be completed because the resource changed."
            elif response.status_code == 422:
                message = "The submitted values are invalid."
            elif response.status_code >= 500:
                message = "The TaskPilot service is unavailable."
            else:
                message = "The TaskPilot request was rejected."
            raise TaskPilotClientError(message, status_code=response.status_code)
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any] | list[Any]:
        try:
            value = response.json()
        except ValueError:
            raise TaskPilotClientError("The service returned an invalid response.") from None
        if not isinstance(value, (dict, list)):
            raise TaskPilotClientError("The service returned an invalid response.")
        return value

    def login(self, email: str, password: str, organization_id: UUID | None = None) -> LoginResult:
        payload: dict[str, Any] = {"email": email, "password": password}
        if organization_id is not None:
            payload["organization_id"] = str(organization_id)
        response = self._request("POST", "/api/v1/auth/login", json=payload)
        data = self._json(response)
        if response.status_code == 409 and isinstance(data, dict):
            ids = data.get("organization_ids", [])
            try:
                return LoginResult(organization_ids=tuple(UUID(str(item)) for item in ids))
            except (TypeError, ValueError):
                raise TaskPilotClientError("The service returned an invalid response.") from None
        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
            raise TaskPilotClientError("The service returned an invalid response.")
        self.token = data["access_token"]
        return LoginResult(token=self.token)

    def session(self) -> dict[str, Any]:
        data = self._json(self._request("GET", "/api/v1/auth/session"))
        if not isinstance(data, dict):
            raise TaskPilotClientError("The service returned an invalid response.")
        return data

    def logout(self) -> None:
        try:
            self._request("POST", "/api/v1/auth/logout")
        finally:
            self.token = None

    def list_tasks(self) -> list[dict[str, Any]]:
        data = self._json(self._request("GET", "/api/v1/tasks"))
        if not isinstance(data, list):
            raise TaskPilotClientError("The service returned an invalid response.")
        return [item for item in data if isinstance(item, dict)]

    def get_task(self, task_id: str | UUID) -> dict[str, Any]:
        data = self._json(self._request("GET", f"/api/v1/tasks/{task_id}"))
        if not isinstance(data, dict):
            raise TaskPilotClientError("The service returned an invalid response.")
        return data

    def create_task(self, title: str, description: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title}
        if description:
            payload["description"] = description
        data = self._json(self._request("POST", "/api/v1/tasks", json=payload))
        if not isinstance(data, dict):
            raise TaskPilotClientError("The service returned an invalid response.")
        return data


__all__ = ["LoginResult", "TaskPilotClient", "TaskPilotClientError"]
