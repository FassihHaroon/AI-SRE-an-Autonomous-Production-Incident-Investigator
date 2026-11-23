"""Human approval for remediation actions.

Flow:

1. The agent calls a remediation tool without ``approval_token``. The server records an
   *approval request* (action + exact parameters) and returns ``status="approval_required"``
   with the request id. Nothing is executed.
2. A human reviews it and approves through the admin endpoint (``POST /approvals/{id}/approve``,
   admin bearer token, which the agent never holds) or ``python -m ai_sre.mcp_servers approve``.
   The response is an approval token: ``base64(payload).hmac_sha256``, where the payload binds the
   request id, action, a hash of the exact parameters, the approver and an expiry.
3. The agent calls the tool again with the same parameters and the token. The server verifies the
   signature, expiry, parameter hash and that the request is approved and unused; it marks the
   request used (single use) and only then executes.

Requests live in memory in the MCP server process (persistence arrives with the API in Phase 7).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal["pending", "approved", "rejected", "used", "expired"]


class ApprovalError(Exception):
    pass


def params_hash(action: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"action": action, "params": params}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class ApprovalRequest:
    id: str
    action: str
    params: dict[str, Any]
    requested_at: float
    expires_at: float
    status: Status = "pending"
    approver: str | None = None
    decided_at: float | None = None
    executed_result: dict[str, Any] | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "params": self.params,
            "status": self.current_status(),
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "approver": self.approver,
            "decided_at": self.decided_at,
        }

    def current_status(self) -> Status:
        if self.status in ("pending", "approved") and time.time() > self.expires_at:
            return "expired"
        return self.status


class ApprovalStore:
    def __init__(self, secret: bytes, ttl_s: int) -> None:
        self._secret = secret
        self._ttl = ttl_s
        self._requests: dict[str, ApprovalRequest] = {}

    # ------------------------------------------------------------------ requests
    def request(self, action: str, params: dict[str, Any]) -> ApprovalRequest:
        now = time.time()
        req = ApprovalRequest(
            id=f"apr-{secrets.token_hex(6)}",
            action=action,
            params=params,
            requested_at=now,
            expires_at=now + self._ttl,
        )
        self._requests[req.id] = req
        return req

    def get(self, request_id: str) -> ApprovalRequest:
        req = self._requests.get(request_id)
        if req is None:
            raise ApprovalError(f"unknown approval request {request_id}")
        return req

    def list(self) -> list[ApprovalRequest]:
        return sorted(self._requests.values(), key=lambda r: r.requested_at, reverse=True)

    # ------------------------------------------------------------------ decisions (humans)
    def approve(self, request_id: str, approver: str) -> str:
        req = self.get(request_id)
        if req.current_status() != "pending":
            raise ApprovalError(f"{request_id} is {req.current_status()}, not pending")
        if not approver or approver.lower() in {"agent", "ai-sre", "mcp"}:
            raise ApprovalError("approver must be a named human")
        req.status, req.approver, req.decided_at = "approved", approver, time.time()
        payload = {
            "rid": req.id,
            "act": req.action,
            "ph": params_hash(req.action, req.params),
            "by": approver,
            "exp": int(req.expires_at),
        }
        body = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode()
        return f"{body}.{self._sign(body)}"

    def reject(self, request_id: str, approver: str) -> ApprovalRequest:
        req = self.get(request_id)
        if req.current_status() != "pending":
            raise ApprovalError(f"{request_id} is {req.current_status()}, not pending")
        req.status, req.approver, req.decided_at = "rejected", approver, time.time()
        return req

    # ------------------------------------------------------------------ verification (tool)
    def consume(self, token: str, action: str, params: dict[str, Any]) -> ApprovalRequest:
        """Verify ``token`` for exactly this action and parameters; mark the request used."""
        try:
            body, signature = token.rsplit(".", 1)
            payload = json.loads(base64.urlsafe_b64decode(body.encode()))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ApprovalError("malformed approval token") from exc
        if not hmac.compare_digest(signature, self._sign(body)):
            raise ApprovalError("approval token signature is invalid")
        if time.time() > payload["exp"]:
            raise ApprovalError("approval token has expired")
        if payload["act"] != action:
            raise ApprovalError(f"token approves {payload['act']}, not {action}")
        if payload["ph"] != params_hash(action, params):
            raise ApprovalError("parameters differ from the approved request")
        req = self.get(payload["rid"])
        if req.current_status() == "used":
            raise ApprovalError(f"approval {req.id} was already used")
        if req.current_status() != "approved":
            raise ApprovalError(f"approval {req.id} is {req.current_status()}")
        req.status = "used"
        return req

    def _sign(self, body: str) -> str:
        return hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
