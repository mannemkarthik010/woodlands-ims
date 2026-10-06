"""
Yesterday's sales by email: read, and recorded when nothing needs a person.

The rules are the hand upload's rules -- nothing here may record a day an
owner would have been stopped from recording, or quietly replace one.
"""

import base64
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.notify.models import Notification
from apps.sales import inbound
from apps.sales.models import SalesImport, SalesImportStatus
from apps.sales.services import set_portion
from apps.sales.tests.test_daily_sales import DAY, SalesTestCase, report

LA = ZoneInfo("America/Los_Angeles")
MORNING = datetime(2026, 9, 28, 6, 30, tzinfo=LA)  # Monday morning: the report is Sunday's
TOKEN = "s3cret-token-for-tests"


class InboundTests(SalesTestCase):
    def receive(self, data=DAY, **kw):
        return inbound.receive(
            data, filename="sales.csv", location=self.restaurant, received_at=MORNING, **kw
        )

    def test_a_clean_day_is_read_as_yesterday_and_recorded(self):
        set_portion(self.batter_button, ounces=32)
        outcome = self.receive()
        self.assertEqual(outcome.status, "recorded")
        self.assertEqual(outcome.sales_import.business_date, date(2026, 9, 27))
        self.assertEqual(outcome.sales_import.status, SalesImportStatus.POSTED)
        self.assertIn("recorded", Notification.objects.get(recipient=self.owner).body)

    def test_a_tub_with_no_size_waits_for_the_owner(self):
        outcome = self.receive()
        self.assertEqual(outcome.status, "waiting")
        self.assertEqual(outcome.sales_import.status, SalesImportStatus.IMPORTED)
        self.assertIn("Dosa Batter", Notification.objects.get(recipient=self.owner).body)

    def test_a_new_menu_button_waits_for_the_owner(self):
        set_portion(self.batter_button, ounces=32)
        outcome = self.receive(report(("Masala Dosa", "Dosa", 3, 30), ("Pumpkin Soup", "Soups", 1, 9)))
        self.assertEqual(outcome.status, "waiting")
        self.assertIn("Pumpkin Soup", outcome.message + Notification.objects.get().body)

    def test_a_day_uploaded_by_hand_is_never_replaced(self):
        mine = self.load()
        outcome = self.receive(report(("Masala Dosa", "Dosa", 1, 10)))
        self.assertEqual(outcome.status, "already")
        self.assertEqual(SalesImport.objects.exclude(status=SalesImportStatus.SUPERSEDED).get(), mine)

    def test_the_same_email_twice_is_handled_once(self):
        self.receive()
        self.assertEqual(self.receive().status, "duplicate")
        self.assertEqual(SalesImport.objects.count(), 1)

    def test_a_broken_file_is_refused_and_the_owners_are_told(self):
        outcome = self.receive(report(("Masala Dosa", "Dosa", 3, 30), total=99))
        self.assertEqual(outcome.status, "refused")
        self.assertIn("upload it by hand", Notification.objects.get().body)


@override_settings(SALES_INBOUND_TOKEN=TOKEN)
class InboundAddressTests(SalesTestCase):
    def url(self, token=TOKEN):
        return reverse("sales_inbound", args=[token]) + "?date=2026-09-27"

    def test_postmark_json_with_the_csv_attached(self):
        payload = {
            "Subject": "Sales Summary by Item",
            "Attachments": [
                {"Name": "logo.png", "ContentType": "image/png", "Content": ""},
                {
                    "Name": "sales-summary.csv",
                    "ContentType": "text/csv",
                    "Content": base64.b64encode(DAY).decode(),
                },
            ],
        }
        page = self.client.post(self.url(), json.dumps(payload), content_type="application/json")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["status"], "waiting")
        self.assertTrue(SalesImport.objects.filter(business_date=date(2026, 9, 27)).exists())

    def test_a_form_upload_works_too(self):
        page = self.client.post(self.url(), {"attachment-1": SimpleUploadedFile("report.csv", DAY)})
        self.assertEqual(page.json()["status"], "waiting")

    def test_the_wrong_secret_is_not_found(self):
        page = self.client.post(self.url("guess"), {"attachment-1": SimpleUploadedFile("report.csv", DAY)})
        self.assertEqual(page.status_code, 404)
        self.assertFalse(SalesImport.objects.exists())

    @override_settings(SALES_INBOUND_TOKEN="")
    def test_with_no_secret_configured_the_address_does_not_exist(self):
        page = self.client.post(
            "/sales/inbound/anything/", {"attachment-1": SimpleUploadedFile("report.csv", DAY)}
        )
        self.assertEqual(page.status_code, 404)

    def test_an_email_with_no_csv_is_refused(self):
        page = self.client.post(self.url(), json.dumps({"Attachments": []}), content_type="application/json")
        self.assertEqual(page.status_code, 400)
