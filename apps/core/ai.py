"""
The one way this project talks to an AI model.

Claude, reached one of two ways -- chosen by configuration, not code:

    AI_PROVIDER=vertex     through the restaurant's own Google Cloud project
                           (Vertex AI), billed to its Google Cloud account
    AI_PROVIDER=anthropic  directly, with an Anthropic API key

Same model, same answers either way. Everything that uses AI -- the recipe
assistant's wording, matching the thali -- calls `create()` here, so a
change of provider is one setting and nothing else.

Two gates sit in front of every call, and `available()` checks both:

    KNOWLEDGE_CONSENT=1    the owners agreed (they did, 6 October 2026)
    credentials            a key, or a Google Cloud project to bill

Callers treat any failure as "no AI right now" and carry on without it: the
chef's own words, or plain name matching. A model is never the only way a
screen works.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings

log = logging.getLogger(__name__)

VERTEX = "vertex"
ANTHROPIC = "anthropic"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def provider() -> str:
    return settings.AI_PROVIDER


def available() -> bool:
    """Consent, and somewhere to send the call."""
    if not settings.KNOWLEDGE_CONSENT:
        return False
    if provider() == VERTEX:
        return bool(settings.GCP_PROJECT_ID)
    return bool(settings.KNOWLEDGE_API_KEY)


def _google_credentials():
    """
    A service account's key, from GCP_SERVICE_ACCOUNT_JSON, when there is no
    file on disk to point at -- which is the case on Vercel. Without it, the
    Google library looks for credentials the usual way (gcloud on a laptop).
    """
    raw = settings.GCP_SERVICE_ACCOUNT_JSON
    if not raw:
        return None
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=GOOGLE_SCOPES)


def client():
    import anthropic

    if provider() == VERTEX:
        return anthropic.AnthropicVertex(
            project_id=settings.GCP_PROJECT_ID,
            region=settings.GCP_REGION,
            credentials=_google_credentials(),
            timeout=30.0,
        )
    return anthropic.Anthropic(api_key=settings.KNOWLEDGE_API_KEY, timeout=30.0)


def create(**request):
    """
    One request to the model. On the Anthropic API a declined request is
    retried on a suitable model inside the same call (server-side fallbacks);
    Vertex does not offer that, so there a refusal simply comes back and the
    caller falls back to working without AI, as it does for any failure.
    """
    request.setdefault("model", settings.KNOWLEDGE_MODEL)
    if provider() == VERTEX:
        return client().messages.create(**request)
    return client().beta.messages.create(
        betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request
    )


def text_of(response) -> str:
    """The answer's text, or an error if the model declined or said nothing."""
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to answer.")
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise RuntimeError("The model returned no answer.")
    return text
