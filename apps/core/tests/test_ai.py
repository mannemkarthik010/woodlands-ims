"""
One way to reach the model, two places it can run: the restaurant's Google
Cloud project, or Anthropic directly. Nothing reaches the network here.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core import ai

REPLY = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Three scoops.")])


class ProviderTests(SimpleTestCase):
    @override_settings(KNOWLEDGE_CONSENT=False, AI_PROVIDER="vertex", GCP_PROJECT_ID="woodlands")
    def test_nothing_is_available_without_the_owners_consent(self):
        self.assertFalse(ai.available())

    @override_settings(KNOWLEDGE_CONSENT=True, AI_PROVIDER="vertex", GCP_PROJECT_ID="")
    def test_google_cloud_needs_a_project(self):
        self.assertFalse(ai.available())

    @override_settings(
        KNOWLEDGE_CONSENT=True,
        AI_PROVIDER="vertex",
        GCP_PROJECT_ID="woodlands-ims",
        GCP_REGION="global",
        GCP_SERVICE_ACCOUNT_JSON="",
        KNOWLEDGE_MODEL="claude-opus-5-5",
    )
    def test_on_google_cloud_the_call_goes_to_the_restaurants_project(self):
        self.assertTrue(ai.available())
        with patch("anthropic.AnthropicVertex") as vertex:
            vertex.return_value.messages.create.return_value = REPLY
            text = ai.text_of(ai.create(max_tokens=100, messages=[{"role": "user", "content": "hi"}]))
        self.assertEqual(text, "Three scoops.")
        self.assertEqual(vertex.call_args.kwargs["project_id"], "woodlands-ims")
        self.assertEqual(vertex.call_args.kwargs["region"], "global")
        sent = vertex.return_value.messages.create.call_args.kwargs
        self.assertEqual(sent["model"], "claude-opus-5-5")
        self.assertNotIn("fallbacks", sent)  # not offered on Vertex

    @override_settings(
        KNOWLEDGE_CONSENT=True,
        AI_PROVIDER="vertex",
        GCP_PROJECT_ID="woodlands-ims",
        GCP_SERVICE_ACCOUNT_JSON=json.dumps({"type": "service_account", "client_email": "ims@x"}),
    )
    def test_a_service_account_key_from_the_hosts_settings_is_used(self):
        with (
            patch("google.oauth2.service_account.Credentials.from_service_account_info") as load,
            patch("anthropic.AnthropicVertex") as vertex,
        ):
            ai.client()
        self.assertEqual(load.call_args.args[0]["client_email"], "ims@x")
        self.assertIs(vertex.call_args.kwargs["credentials"], load.return_value)

    @override_settings(KNOWLEDGE_CONSENT=True, AI_PROVIDER="anthropic", KNOWLEDGE_API_KEY="k")
    def test_directly_with_anthropic_a_declined_request_is_retried_server_side(self):
        with patch("anthropic.Anthropic") as anthropic:
            anthropic.return_value.beta.messages.create.return_value = REPLY
            ai.create(max_tokens=100, messages=[])
        self.assertEqual(anthropic.return_value.beta.messages.create.call_args.kwargs["fallbacks"], "default")

    def test_a_refusal_is_an_error_so_the_caller_works_without_ai(self):
        with self.assertRaises(RuntimeError):
            ai.text_of(SimpleNamespace(stop_reason="refusal", content=[]))
