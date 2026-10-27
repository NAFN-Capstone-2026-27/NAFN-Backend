import importlib
import json
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import boto3
from botocore.stub import ANY, Stubber


LAMBDA_DIR = Path(__file__).resolve().parents[1] / "NAFNBackend/BackendInfrastructure/lambda"
with patch.dict(os.environ, {"RESOURCES_TABLE_NAME": "test-resources"}), \
     patch.object(sys, "path", [str(LAMBDA_DIR), *sys.path]), \
     patch("boto3.resource"):
    list_resources = importlib.import_module("NAFNBackend.BackendInfrastructure.lambda.list_resources")


class ListResourcesTests(unittest.TestCase):
    def setUp(self):
        self.table = MagicMock()
        self.table.name = "test-resources"
        self.paginator = self.table.meta.client.get_paginator.return_value
        table_patch = patch.object(list_resources, "table", self.table)
        table_patch.start()
        self.addCleanup(table_patch.stop)

    def test_empty_table_returns_empty_array(self):
        self.paginator.paginate.return_value = iter([{"Items": []}])
        response = list_resources.handler({}, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["Content-Type"], "application/json")
        self.assertEqual(json.loads(response["body"]), {"resources": []})

    def test_all_pages_preserve_fields_links_and_numeric_coordinates(self):
        independent = {
            "resourceId": "c215a75a-31e9-4a44-8f9f-936ec54ca928",
            "name": "Example Vet", "category": "Emergency Vets",
            "address": "1 Example St", "lat": Decimal("39.7392"),
            "lng": Decimal("-104.9903"), "createdAt": "2026-10-26T12:00:00+00:00",
        }
        linked = {
            **independent, "resourceId": "843f5879-f1f3-4ba0-8e60-6edc14ef2859",
            "partnerId": "d2fa15bd-033e-41a6-a84d-cfd9f48a34dc",
            "lat": Decimal("0"), "lng": Decimal("0"),
            "phone": "3035550100", "website": "https://example.org",
            "email": "contact@example.org", "description": "Temporary care",
            "animalsAccepted": ["Dogs", "Cats"], "timeLimits": "30 days",
        }
        self.paginator.paginate.return_value = iter([
            {"Items": [independent]}, {"Items": []}, {"Items": [linked]},
        ])
        response = list_resources.handler({}, None)
        items = json.loads(response["body"])["resources"]
        self.assertEqual(items, [
            {**independent, "lat": 39.7392, "lng": -104.9903},
            {**linked, "lat": 0, "lng": 0},
        ])
        self.assertIsInstance(items[0]["lat"], float)
        self.assertIsInstance(items[0]["lng"], float)
        self.assertIsInstance(items[1]["lat"], int)
        self.assertNotIn("partnerId", items[0])
        self.paginator.paginate.assert_called_once_with(TableName="test-resources", ConsistentRead=True)

    def test_scan_failure_does_not_return_partial_success(self):
        def pages():
            yield {"Items": [{"resourceId": "first"}]}
            raise RuntimeError("next page unavailable")

        self.paginator.paginate.return_value = pages()
        with self.assertRaisesRegex(RuntimeError, "next page unavailable"):
            list_resources.handler({}, None)

    def test_sdk_pagination_deserializes_dynamodb_numbers_to_json_numbers(self):
        resource = boto3.Session(
            aws_access_key_id="testing", aws_secret_access_key="testing",
            region_name="us-east-1",
        ).resource("dynamodb")
        client = resource.meta.client
        expected = {"TableName": "test-resources", "ConsistentRead": True}
        with Stubber(client) as stubber, patch.object(list_resources, "table", resource.Table("test-resources")):
            stubber.add_response("scan", {
                "Items": [], "LastEvaluatedKey": {"resourceId": {"S": "earlier"}},
            }, expected)
            stubber.add_response("scan", {"Items": [{
                "resourceId": {"S": "example"}, "lat": {"N": "39.7392"},
                "lng": {"N": "-104.9903"},
            }]}, {**expected, "ExclusiveStartKey": ANY})
            response = list_resources.handler({}, None)
            self.assertEqual(json.loads(response["body"]), {"resources": [{
                "resourceId": "example", "lat": 39.7392, "lng": -104.9903,
            }]})
            stubber.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
