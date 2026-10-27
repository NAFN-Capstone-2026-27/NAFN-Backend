import base64
import importlib
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.stub import ANY, Stubber


LAMBDA_DIR = Path(__file__).resolve().parents[1] / "NAFNBackend/BackendInfrastructure/lambda"
with patch.dict(os.environ, {"PARTNERS_TABLE_NAME": "test-partners"}), \
     patch.object(sys, "path", [str(LAMBDA_DIR), *sys.path]), \
     patch("boto3.resource"):
    create_partner = importlib.import_module("NAFNBackend.BackendInfrastructure.lambda.create_partner")


class ConditionalFailure(Exception):
    pass


class CreatePartnerTests(unittest.TestCase):
    def setUp(self):
        self.table = MagicMock()
        self.table.name = "test-partners"
        self.table.meta.client.exceptions.ConditionalCheckFailedException = ConditionalFailure
        self.paginator = self.table.meta.client.get_paginator.return_value
        self.paginator.paginate.return_value = iter([{"Items": []}])
        self.table_patch = patch.object(create_partner, "table", self.table)
        self.table_patch.start()
        self.addCleanup(self.table_patch.stop)
        self.payload = {
            "name": " Example Charity ",
            "address": " 1 Example St ",
            "email": " contact@example.org ",
            "phone": "(303) 555-0100",
            "focusAreas": ["Domestic Violence", "Climate Emergency", "Domestic Violence"],
        }

    def submit(self, payload=None):
        response = create_partner.handler({"body": json.dumps(self.payload if payload is None else payload)}, None)
        return response["statusCode"], json.loads(response["body"])

    def test_partner_without_ein_gets_generated_identity_and_cleaned_fields(self):
        self.payload.update({"partnerId": "client-id", "createdAt": "client-time", "extra": "discard"})
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertEqual(UUID(body["partnerId"]).version, 4)
        self.assertIsNotNone(datetime.fromisoformat(body["createdAt"]).utcoffset())
        self.assertEqual(body["name"], "Example Charity")
        self.assertEqual(body["address"], "1 Example St")
        self.assertEqual(body["email"], "contact@example.org")
        self.assertEqual(body["phone"], "3035550100")
        self.assertEqual(body["focusAreas"], ["Domestic Violence", "Climate Emergency"])
        self.assertEqual(set(body), {"partnerId", "createdAt", "name", "address", "email", "phone", "focusAreas"})
        self.table.meta.client.get_paginator.assert_not_called()
        self.table.put_item.assert_called_once_with(Item=body, ConditionExpression=Attr("partnerId").not_exists())

    def test_blank_and_null_eins_are_omitted_without_a_scan(self):
        for value in ("", " ", None):
            with self.subTest(value=value):
                self.payload["ein"] = value
                status, body = self.submit()
                self.assertEqual(status, 201)
                self.assertNotIn("ein", body)
        self.table.meta.client.get_paginator.assert_not_called()

    def test_formatted_ein_is_normalized_before_scanning_and_inserting(self):
        self.payload["ein"] = "01-2345678"
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertEqual(body["ein"], "012345678")
        self.paginator.paginate.assert_called_once_with(
            TableName="test-partners", FilterExpression=Attr("ein").eq("012345678"),
            ProjectionExpression="partnerId", ConsistentRead=True,
        )
        self.assertEqual(self.table.put_item.call_args.kwargs["Item"]["ein"], "012345678")

    def test_duplicate_on_later_page_returns_409_without_insertion(self):
        self.payload["ein"] = "12-3456789"
        self.paginator.paginate.return_value = iter([
            {"Items": [], "LastEvaluatedKey": {"partnerId": "earlier"}},
            {"Items": [{"partnerId": "existing"}]},
        ])
        status, body = self.submit()
        self.assertEqual(status, 409)
        self.assertIn("EIN", body["error"])
        self.table.put_item.assert_not_called()

    def test_all_filtered_empty_pages_are_consumed_before_insert(self):
        self.payload["ein"] = "123456789"
        visited = []

        def pages():
            for page in range(3):
                visited.append(page)
                yield {"Items": []}

        self.paginator.paginate.return_value = pages()
        self.assertEqual(self.submit()[0], 201)
        self.assertEqual(visited, [0, 1, 2])

    def test_scan_failure_does_not_allow_insertion(self):
        self.payload["ein"] = "123456789"

        def failing_pages():
            yield {"Items": []}
            raise RuntimeError("scan unavailable")

        self.paginator.paginate.return_value = failing_pages()
        with self.assertRaisesRegex(RuntimeError, "scan unavailable"):
            self.submit()
        self.table.put_item.assert_not_called()

    def test_missing_fields_collect_errors_before_database_calls(self):
        status, body = self.submit({})
        self.assertEqual(status, 400)
        self.assertEqual(len(body["errors"]), 5)
        self.table.put_item.assert_not_called()
        self.table.meta.client.get_paginator.assert_not_called()

    def test_invalid_ein_and_focus_areas_are_rejected(self):
        for field, value in (("ein", 123456789), ("ein", "bad"),
                             ("focusAreas", []), ("focusAreas", ["Other"])):
            with self.subTest(field=field, value=value):
                payload = {**self.payload, field: value}
                self.assertEqual(self.submit(payload)[0], 400)
        self.table.put_item.assert_not_called()
        self.table.meta.client.get_paginator.assert_not_called()

    def test_location_fields_are_ignored_on_partner_submissions(self):
        status, body = self.submit({**self.payload, "animalsAccepted": ["Dogs"], "timeLimits": "30 days"})
        self.assertEqual(status, 201)
        self.assertNotIn("animalsAccepted", body)
        self.assertNotIn("timeLimits", body)
        stored = self.table.put_item.call_args.kwargs["Item"]
        self.assertNotIn("animalsAccepted", stored)
        self.assertNotIn("timeLimits", stored)

    def test_id_collision_is_not_misreported_as_an_ein_duplicate(self):
        self.table.put_item.side_effect = ConditionalFailure()
        status, body = self.submit()
        self.assertEqual(status, 409)
        self.assertIn("ID collision", body["error"])
        self.assertNotIn("EIN", body["error"])

    def test_unexpected_write_failure_is_not_swallowed(self):
        self.table.put_item.side_effect = RuntimeError("write unavailable")
        with self.assertRaisesRegex(RuntimeError, "write unavailable"):
            self.submit()

    def test_invalid_json_is_rejected_before_database_calls(self):
        for body in (None, "", "{", "[]", "null"):
            with self.subTest(body=body):
                response = create_partner.handler({"body": body}, None)
                self.assertEqual(response["statusCode"], 400)
        self.table.put_item.assert_not_called()

    def test_base64_encoded_request_is_supported(self):
        body = base64.b64encode(json.dumps(self.payload).encode()).decode()
        response = create_partner.handler({"body": body, "isBase64Encoded": True}, None)
        self.assertEqual(response["statusCode"], 201)

    def test_invalid_base64_or_utf8_returns_400(self):
        for body in ("!!", None, base64.b64encode(b"\xff").decode()):
            with self.subTest(body=body):
                response = create_partner.handler({"body": body, "isBase64Encoded": True}, None)
                self.assertEqual(response["statusCode"], 400)
        self.table.put_item.assert_not_called()

    def test_real_sdk_paginator_marshals_filter_and_advances_past_empty_page(self):
        resource = boto3.Session(
            aws_access_key_id="testing", aws_secret_access_key="testing",
            region_name="us-east-1",
        ).resource("dynamodb")
        client = resource.meta.client
        expected = {
            "TableName": "test-partners", "FilterExpression": ANY,
            "ProjectionExpression": "partnerId", "ConsistentRead": True,
        }
        with Stubber(client) as stubber, patch.object(create_partner, "table", resource.Table("test-partners")):
            stubber.add_response("scan", {
                "Items": [], "LastEvaluatedKey": {"partnerId": {"S": "first-page"}},
            }, expected)
            stubber.add_response("scan", {
                "Items": [{"partnerId": {"S": "existing"}}],
            }, {**expected, "ExclusiveStartKey": ANY})
            self.assertTrue(create_partner.ein_exists("012345678"))
            stubber.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
