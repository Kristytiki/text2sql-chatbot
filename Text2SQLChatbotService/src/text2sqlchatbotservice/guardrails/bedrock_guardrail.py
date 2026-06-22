"""Bedrock Guardrails wrapper — input + output content filtering.

Configured in AWS console; we just wire in the guardrail ID. Fails open on
infrastructure errors (logged) but fails closed on policy hits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardrailVerdict:
    blocked: bool
    action: str           # "NONE" | "GUARDRAIL_INTERVENED" | "ERROR"
    output: str           # safe text to surface (canned msg if blocked, original if not)
    reason: str | None = None


class BedrockGuardrail:
    def __init__(self, *, guardrail_id: str, version: str, region: str) -> None:
        self._guardrail_id = guardrail_id
        self._version = version
        self._client = boto3.client("bedrock-runtime", region_name=region) if guardrail_id else None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def apply(self, text: str, *, source: str) -> GuardrailVerdict:
        """source ∈ {'INPUT', 'OUTPUT'}. Returns a verdict."""
        if not self.enabled:
            return GuardrailVerdict(blocked=False, action="NONE", output=text)

        try:
            resp = self._client.apply_guardrail(  # type: ignore[union-attr]
                guardrailIdentifier=self._guardrail_id,
                guardrailVersion=self._version,
                source=source,
                content=[{"text": {"text": text}}],
            )
        except (BotoCoreError, ClientError) as exc:
            logger.warning("Bedrock Guardrail call failed (fail-open): %s", exc)
            return GuardrailVerdict(blocked=False, action="ERROR", output=text, reason=str(exc))

        action = resp.get("action", "NONE")
        if action == "GUARDRAIL_INTERVENED":
            outputs = resp.get("outputs", [])
            canned = outputs[0]["text"] if outputs else (
                "I can only help with questions about the US Census dataset."
            )
            assessments = resp.get("assessments", [])
            return GuardrailVerdict(
                blocked=True, action=action, output=canned, reason=str(assessments)[:500]
            )
        return GuardrailVerdict(blocked=False, action=action, output=text)
