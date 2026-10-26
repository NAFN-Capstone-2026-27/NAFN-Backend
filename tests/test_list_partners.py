import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
from botocore.stub import ANY, Stubber


LAMBDA_DIR = Path(__file__).resolve().parents[1] / "NAFNBackend/BackendInfrastructure/lambda"
with patch.dict(os.environ, {"PARTNERS_TABLE_NAME": "test-partners"}), \
     patch.object(sys, "path", [str(LAMBDA_DIR), *sys.path]), \
     patch("boto3.resource"):
    list_partners = importlib.import_module("NAFNBackend.BackendInfrastructure.lambda.list_partners")


class ListPartnersTests(unittest.TestCase):
    def setUp(self):
        self.table = MagicMock()
        self.table.name = "test-partners"
        self.paginator = self.table.meta.client.get_paginator.return_value
        self.table_patch = patch.object(list_partners, "table", self.table)
        self.table_patch.start()
        self.addCleanup(self.table_patch.stop)

    def test_empty_table_returns_empty_array(self):
        self.paginator.paginate.return_value = iter([{"Items": []}])
        response = list_partners.handler({}, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["Content-Type"], "application/json")
        self.assertEqual(json.loads(response["body"]), {"partners": []})

    def test_all_pages_are_returned_with_optional_ein_preserved(self):
        first = {
            "partnerId": "c215a75a-31e9-4a44-8f9f-936ec54ca928",
            "name": "First Partner", "address": "1 Example St",
            "email": "first@example.org", "phone": "3035550100",
            "focusAreas": ["Domestic Violence"],
            "createdAt": "2026-10-26T12:00:00+00:00",
        }
        second = {**first, "partnerId": "843f5879-f1f3-4ba0-8e60-6edc14ef2859", "ein": "012345678"}
        self.paginator.paginate.return_value = iter([
            {"Items": [first]}, {"Items": []}, {"Items": [second]},
        ])
        response = list_partners.handler({}, None)
        self.assertEqual(json.loads(response["body"]), {"partners": [first, second]})
        self.paginator.paginate.assert_called_once_with(TableName="test-partners", ConsistentRead=True)

    def test_scan_failure_does_not_return_a_misleading_partial_listing(self):
        def pages():
            yield {"Items": [{"partnerId": "first"}]}
            raise RuntimeError("next page unavailable")

        self.paginator.paginate.return_value = pages()
        with self.assertRaisesRegex(RuntimeError, "next page unavailable"):
            list_partners.handler({}, None)

    def test_real_sdk_paginates_and_deserializes_the_complete_listing(self):
        resource = boto3.Session(
            aws_access_key_id="testing", aws_secret_access_key="testing",
            region_name="us-east-1",
        ).resource("dynamodb")
        client = resource.meta.client
        expected = {"TableName": "test-partners", "ConsistentRead": True}
        with Stubber(client) as stubber, patch.object(list_partners, "table", resource.Table("test-partners")):
            stubber.add_response("scan", {
                "Items": [{"partnerId": {"S": "first"}}],
                "LastEvaluatedKey": {"partnerId": {"S": "first"}},
            }, expected)
            stubber.add_response("scan", {
                "Items": [{"partnerId": {"S": "second"}, "ein": {"S": "012345678"}}],
            }, {**expected, "ExclusiveStartKey": ANY})
            response = list_partners.handler({}, None)
            self.assertEqual(json.loads(response["body"]), {"partners": [
                {"partnerId": "first"}, {"partnerId": "second", "ein": "012345678"},
            ]})
            stubber.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
