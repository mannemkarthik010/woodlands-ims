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
    credentials            a key, or a Google Cloud project to bill (signed in
                           keyless from Vercel, or with a key)

Callers treat any failure as "no AI right now" and carry on without it: the
chef's own words, or plain name matching. A model is never the only way a
screen works.
"""

from __future__ import annotations

import json
import logging
import os
from contextvars import ContextVar

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


# Vercel's identity token for the request being served (x-vercel-oidc-token).
_vercel_token: ContextVar[str] = ContextVar("vercel_oidc_token", default="")


class VercelTokenMiddleware:
    """Keeps the token Vercel sends with each request, for the keyless sign-in below."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        handle = _vercel_token.set(request.headers.get("x-vercel-oidc-token", ""))
        try:
            return self.get_response(request)
        finally:
            _vercel_token.reset(handle)


class VercelToken:
    """Hands Google the Vercel token: this request's, else the one in the environment (local dev)."""

    def get_subject_token(self, context, request):
        token = _vercel_token.get() or os.environ.get("VERCEL_OIDC_TOKEN", "")
        if not token:
            from google.auth import exceptions

            raise exceptions.RefreshError("No Vercel identity token on this request.")
        return token


def _google_credentials():
    """
    How this server signs in to Google, best first:

    keyless    GCP_WORKLOAD_PROVIDER + GCP_SERVICE_ACCOUNT_EMAIL: Vercel's own
               short-lived token is exchanged for Google access as the
               service account (workload identity federation). Nothing to
               leak, and allowed where an organisation forbids keys.
    a key      GCP_SERVICE_ACCOUNT_JSON, pasted whole into the host's settings.
    neither    the Google library's usual search (gcloud on a laptop).
    """
    if settings.GCP_WORKLOAD_PROVIDER and settings.GCP_SERVICE_ACCOUNT_EMAIL:
        from google.auth import identity_pool

        return identity_pool.Credentials(
            audience=f"//iam.googleapis.com/{settings.GCP_WORKLOAD_PROVIDER}",
            subject_token_type="urn:ietf:params:oauth:token-type:jwt",
            token_url="https://sts.googleapis.com/v1/token",
            service_account_impersonation_url=(
                "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                f"{settings.GCP_SERVICE_ACCOUNT_EMAIL}:generateAccessToken"
            ),
            subject_token_supplier=VercelToken(),
            scopes=GOOGLE_SCOPES,
        )
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
