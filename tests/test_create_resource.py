import base64
import importlib
import json
import os
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.stub import ANY, Stubber


LAMBDA_DIR = Path(__file__).resolve().parents[1] / "NAFNBackend/BackendInfrastructure/lambda"
with patch.dict(os.environ, {
    "PARTNERS_TABLE_NAME": "test-partners", "RESOURCES_TABLE_NAME": "test-resources",
}), patch.object(sys, "path", [str(LAMBDA_DIR), *sys.path]), patch("boto3.resource"):
    create_resource = importlib.import_module("NAFNBackend.BackendInfrastructure.lambda.create_resource")


class ConditionalFailure(Exception):
    pass


class CreateResourceTests(unittest.TestCase):
    def setUp(self):
        self.resources = MagicMock()
        self.resources.meta.client.exceptions.ConditionalCheckFailedException = ConditionalFailure
        self.partners = MagicMock()
        self.partner_id = "c215a75a-31e9-4a44-8f9f-936ec54ca928"
        self.partners.get_item.return_value = {"Item": {"partnerId": self.partner_id}}
        for name, value in (("resources_table", self.resources), ("partners_table", self.partners)):
            table_patch = patch.object(create_resource, name, value)
            table_patch.start()
            self.addCleanup(table_patch.stop)
        self.payload = {
            "name": " Example Vet ", "category": "Emergency Vets",
            "address": " 1 Example St ", "lat": 39.7392, "lng": -104.9903,
        }

    def submit(self, payload=None):
        response = create_resource.handler({"body": json.dumps(self.payload if payload is None else payload)}, None)
        return response["statusCode"], json.loads(response["body"])

    def test_independent_resource_gets_server_identity_and_needs_no_partner_or_ein(self):
        self.payload.update({"resourceId": "client-id", "createdAt": "client-time", "ein": "123456789", "focusAreas": ["Other"]})
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertEqual(UUID(body["resourceId"]).version, 4)
        self.assertIsNotNone(datetime.fromisoformat(body["createdAt"]).utcoffset())
        self.assertEqual(body["name"], "Example Vet")
        self.assertEqual(body["address"], "1 Example St")
        self.assertEqual(set(body), {"resourceId", "createdAt", "name", "category", "address", "lat", "lng"})
        self.partners.get_item.assert_not_called()
        stored = self.resources.put_item.call_args.kwargs["Item"]
        self.assertEqual(stored["lat"], Decimal("39.7392"))
        self.assertIsInstance(stored["lng"], Decimal)
        self.assertIsInstance(body["lat"], float)
        self.assertIsInstance(body["lng"], float)
        self.assertEqual(self.resources.put_item.call_args.kwargs["ConditionExpression"], Attr("resourceId").not_exists())

    def test_optional_fields_are_cleaned_and_stored(self):
        self.payload.update({
            "phone": "(303) 555-0100", "email": " contact@example.org ",
            "website": " https://example.org ", "description": " Emergency care ",
            "animalsAccepted": [" Cats ", "Dogs", "Cats", ""], "timeLimits": " Case by case ",
        })
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertEqual(body["phone"], "3035550100")
        self.assertEqual(body["email"], "contact@example.org")
        self.assertEqual(body["website"], "https://example.org")
        self.assertEqual(body["description"], "Emergency care")
        self.assertEqual(body["animalsAccepted"], ["Cats", "Dogs"])
        self.assertEqual(body["timeLimits"], "Case by case")

    def test_linked_resource_requires_existing_partner_and_normalizes_uuid(self):
        self.payload["partnerId"] = f" {self.partner_id.upper()} "
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertEqual(body["partnerId"], self.partner_id)
        self.partners.get_item.assert_called_once_with(
            Key={"partnerId": self.partner_id}, ConsistentRead=True, ProjectionExpression="partnerId",
        )

    def test_unknown_partner_is_rejected_without_resource_insertion(self):
        self.payload["partnerId"] = self.partner_id
        self.partners.get_item.return_value = {}
        status, body = self.submit()
        self.assertEqual(status, 400)
        self.assertIn("existing partner", body["errors"][0])
        self.resources.put_item.assert_not_called()

    def test_blank_optional_fields_are_omitted(self):
        self.payload.update({
            "partnerId": " ", "phone": None, "email": "", "website": " ",
            "description": None, "animalsAccepted": [], "timeLimits": "",
        })
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertTrue(set(body).isdisjoint({"partnerId", "phone", "email", "website", "description", "animalsAccepted", "timeLimits"}))
        self.partners.get_item.assert_not_called()

    def test_each_required_field_is_enforced(self):
        for field in ("name", "category", "address", "lat", "lng"):
            with self.subTest(field=field):
                payload = self.payload.copy()
                del payload[field]
                self.assertEqual(self.submit(payload)[0], 400)
        self.resources.put_item.assert_not_called()

    def test_invalid_category_coordinates_and_optional_types_are_rejected(self):
        for field, value in (
            ("category", "Unknown"), ("lat", 91), ("lng", -181), ("lat", "39.7"),
            ("lat", True), ("lng", float("inf")), ("lat", float("nan")),
            ("partnerId", "not-a-uuid"), ("partnerId", 123),
            ("email", "bad"), ("phone", "123"), ("website", "javascript:alert(1)"),
            ("description", []), ("timeLimits", 30), ("animalsAccepted", "Dogs"),
        ):
            with self.subTest(field=field, value=value):
                self.assertEqual(self.submit({**self.payload, field: value})[0], 400)
        self.resources.put_item.assert_not_called()
        self.partners.get_item.assert_not_called()

    def test_zero_coordinates_are_valid(self):
        status, body = self.submit({**self.payload, "lat": 0, "lng": 0})
        self.assertEqual(status, 201)
        self.assertEqual((body["lat"], body["lng"]), (0, 0))

    def test_partner_lookup_failure_does_not_allow_insertion(self):
        self.payload["partnerId"] = self.partner_id
        self.partners.get_item.side_effect = RuntimeError("lookup unavailable")
        with self.assertRaisesRegex(RuntimeError, "lookup unavailable"):
            self.submit()
        self.resources.put_item.assert_not_called()

    def test_resource_id_collision_returns_409(self):
        self.resources.put_item.side_effect = ConditionalFailure()
        status, body = self.submit()
        self.assertEqual(status, 409)
        self.assertIn("ID collision", body["error"])

    def test_unexpected_write_failure_is_not_swallowed(self):
        self.resources.put_item.side_effect = RuntimeError("write unavailable")
        with self.assertRaisesRegex(RuntimeError, "write unavailable"):
            self.submit()

    def test_invalid_json_is_rejected_without_database_calls(self):
        for body in (None, "", "{", "[]", "null"):
            with self.subTest(body=body):
                self.assertEqual(create_resource.handler({"body": body}, None)["statusCode"], 400)
        self.resources.put_item.assert_not_called()
        self.partners.get_item.assert_not_called()

    def test_base64_request_is_supported(self):
        encoded = base64.b64encode(json.dumps(self.payload).encode()).decode()
        self.assertEqual(create_resource.handler({"body": encoded, "isBase64Encoded": True}, None)["statusCode"], 201)

    def test_invalid_base64_or_utf8_returns_400(self):
        for body in ("!!", None, base64.b64encode(b"\xff").decode()):
            with self.subTest(body=body):
                self.assertEqual(create_resource.handler({"body": body, "isBase64Encoded": True}, None)["statusCode"], 400)
        self.resources.put_item.assert_not_called()

    def test_sdk_accepts_numeric_resource_and_partner_lookup(self):
        resource = boto3.Session(
            aws_access_key_id="testing", aws_secret_access_key="testing", region_name="us-east-1",
        ).resource("dynamodb")
        self.payload["partnerId"] = self.partner_id
        with Stubber(resource.meta.client) as stubber, \
             patch.object(create_resource, "resources_table", resource.Table("test-resources")), \
             patch.object(create_resource, "partners_table", resource.Table("test-partners")):
            stubber.add_response("get_item", {"Item": {"partnerId": {"S": self.partner_id}}}, {
                "TableName": "test-partners", "Key": {"partnerId": self.partner_id},
                "ConsistentRead": True, "ProjectionExpression": "partnerId",
            })
            stubber.add_response("put_item", {}, {
                "TableName": "test-resources", "Item": ANY, "ConditionExpression": ANY,
            })
            self.assertEqual(self.submit()[0], 201)
            stubber.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
