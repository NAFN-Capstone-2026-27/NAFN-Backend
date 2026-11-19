import base64
import importlib
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from uuid import UUID, uuid4

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.stub import ANY, Stubber

LAMBDA_DIR = (
    Path(__file__).resolve().parents[1] / "NAFNBackend/BackendInfrastructure/lambda"
)
with (
    patch.dict(
        os.environ,
        {
            "PARTNERS_TABLE_NAME": "test-partners",
            "PARTNER_EIN_RESERVATIONS_TABLE_NAME": "test-reservations",
        },
    ),
    patch.object(sys, "path", [str(LAMBDA_DIR), *sys.path]),
    patch("boto3.resource"),
):
    create_partner = importlib.import_module(
        "NAFNBackend.BackendInfrastructure.lambda.create_partner"
    )


class CreatePartnerTests(unittest.TestCase):
    def setUp(self):
        self.table = MagicMock()
        self.table.name = "test-partners"
        self.paginator = self.table.meta.client.get_paginator.return_value
        self.paginator.paginate.return_value = iter([{"Items": []}])
        self.table_patch = patch.object(create_partner, "table", self.table)
        self.table_patch.start()
        self.addCleanup(self.table_patch.stop)
        self.reservations = MagicMock()
        self.reservations.get_item.return_value = {}
        reservations_patch = patch.object(
            create_partner, "reservations", self.reservations
        )
        reservations_patch.start()
        self.addCleanup(reservations_patch.stop)
        self.request_id = str(uuid4())
        self.payload = {
            "name": " Example Charity ",
            "address": " 1 Example St ",
            "email": " contact@example.org ",
            "phone": "(303) 555-0100",
            "focusAreas": [
                "Domestic Violence",
                "Climate Emergency",
                "Domestic Violence",
            ],
        }

    def submit(self, payload=None):
        response = create_partner.handler(
            {
                "body": json.dumps(self.payload if payload is None else payload),
                "headers": {"Idempotency-Key": self.request_id},
            },
            None,
        )
        return response["statusCode"], json.loads(response["body"])

    def test_partner_without_ein_gets_generated_identity_and_cleaned_fields(self):
        self.payload.update(
            {"partnerId": "client-id", "createdAt": "client-time", "extra": "discard"}
        )
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertEqual(UUID(body["partnerId"]).version, 4)
        self.assertIsNotNone(datetime.fromisoformat(body["createdAt"]).utcoffset())
        self.assertEqual(body["name"], "Example Charity")
        self.assertEqual(body["address"], "1 Example St")
        self.assertEqual(body["email"], "contact@example.org")
        self.assertEqual(body["phone"], "3035550100")
        self.assertEqual(body["focusAreas"], ["Domestic Violence", "Climate Emergency"])
        self.assertEqual(
            set(body),
            {
                "partnerId",
                "createdAt",
                "name",
                "address",
                "email",
                "phone",
                "focusAreas",
            },
        )
        self.table.meta.client.get_paginator.assert_not_called()
        self.assertEqual(self.reservations.mock_calls, [])
        self.table.put_item.assert_called_once_with(Item=body)

    def test_blank_and_null_eins_are_omitted_without_a_scan(self):
        for value in ("", " ", None):
            with self.subTest(value=value):
                self.payload["ein"] = value
                status, body = self.submit()
                self.assertEqual(status, 201)
                self.assertNotIn("ein", body)
        self.table.meta.client.get_paginator.assert_not_called()
        self.assertEqual(self.reservations.mock_calls, [])

    def test_normalized_ein_is_conditionally_reserved_before_partner_write(self):
        self.payload["ein"] = "01-2345678"
        writes = MagicMock()
        writes.attach_mock(self.reservations.put_item, "claim")
        writes.attach_mock(self.table.put_item, "create")
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertEqual(body["ein"], "012345678")
        self.paginator.paginate.assert_called_once_with(
            TableName="test-partners",
            FilterExpression=Attr("ein").eq("012345678"),
            ProjectionExpression="partnerId",
            ConsistentRead=True,
        )
        claim = self.reservations.put_item.call_args.kwargs["Item"]
        self.assertEqual(claim["ein"], "012345678")
        self.assertEqual(claim["requestId"], self.request_id)
        self.assertEqual(claim["partnerId"], body["partnerId"])
        self.assertEqual(
            writes.mock_calls,
            [
                call.claim(Item=claim, ConditionExpression=Attr("ein").not_exists()),
                call.create(
                    Item=body, ConditionExpression=Attr("partnerId").not_exists()
                ),
            ],
        )

    def test_unreserved_legacy_ein_on_later_page_blocks_creation(self):
        self.payload["ein"] = "12-3456789"
        self.paginator.paginate.return_value = iter(
            [
                {"Items": [], "LastEvaluatedKey": {"partnerId": "earlier"}},
                {"Items": [{"partnerId": "existing"}]},
            ]
        )
        status, body = self.submit()
        self.assertEqual(status, 409)
        self.assertIn("EIN", body["error"])
        self.reservations.put_item.assert_not_called()
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
        self.reservations.put_item.assert_not_called()
        self.table.put_item.assert_not_called()

    def test_missing_or_invalid_idempotency_key_is_rejected_before_database_calls(self):
        self.payload["ein"] = "123456789"
        for headers in (None, {}, {"idempotency-key": "invalid"}):
            with self.subTest(headers=headers):
                response = create_partner.handler(
                    {"body": json.dumps(self.payload), "headers": headers}, None
                )
                self.assertEqual(response["statusCode"], 400)
        self.assertEqual(self.reservations.mock_calls, [])
        self.assertEqual(self.table.mock_calls, [])

    def test_conflicting_claim_is_resolved_with_a_strong_read(self):
        self.payload["ein"] = "123456789"
        self.reservations.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        self.reservations.get_item.side_effect = [
            {},
            {"Item": {"requestId": str(uuid4()), "partnerId": str(uuid4())}},
        ]
        self.assertEqual(self.submit()[0], 409)
        self.assertEqual(
            self.reservations.get_item.call_args_list,
            [call(Key={"ein": "123456789"}, ConsistentRead=True)] * 2,
        )
        self.table.put_item.assert_not_called()

    def test_unconfirmed_regional_claim_never_reaches_partner_write(self):
        self.payload["ein"] = "123456789"
        failure = ClientError(
            {"Error": {"Code": "ReplicatedWriteConflictException"}}, "PutItem"
        )
        self.reservations.put_item.side_effect = failure
        with self.assertRaises(ClientError) as raised:
            self.submit()
        self.assertIs(raised.exception, failure)
        self.reservations.get_item.assert_called_with(
            Key={"ein": "123456789"}, ConsistentRead=True
        )
        self.table.put_item.assert_not_called()

    def test_lost_claim_response_recovers_the_confirmed_owner(self):
        self.payload["ein"] = "123456789"

        def lose_response(**kwargs):
            self.reservations.get_item.return_value = {"Item": kwargs["Item"]}
            raise ReadTimeoutError(endpoint_url="https://example.com")

        self.reservations.put_item.side_effect = lose_response
        status, body = self.submit()
        self.assertEqual(status, 201)
        self.assertEqual(
            body["partnerId"],
            self.reservations.get_item.return_value["Item"]["partnerId"],
        )
        self.reservations.get_item.assert_called_with(
            Key={"ein": "123456789"}, ConsistentRead=True
        )

    def test_retry_after_partner_write_failure_uses_original_reservation(self):
        self.payload["ein"] = "12-3456789"
        self.table.put_item.side_effect = ReadTimeoutError(
            endpoint_url="https://example.com"
        )
        self.table.get_item.return_value = {}
        with self.assertRaises(ReadTimeoutError):
            self.submit()
        claim = self.reservations.put_item.call_args.kwargs["Item"]
        self.reservations.get_item.return_value = {"Item": claim}

        self.table.put_item.side_effect = None
        status, body = self.submit({**self.payload, "ein": "123456789"})
        self.assertEqual(status, 201)
        self.assertEqual(body["partnerId"], claim["partnerId"])
        self.reservations.put_item.assert_called_once()
        self.paginator.paginate.assert_called_once()

    def test_retry_returns_saved_partner_and_changed_data_conflicts(self):
        self.payload["ein"] = "123456789"
        status, original = self.submit()
        self.assertEqual(status, 201)
        self.reservations.get_item.return_value = {
            "Item": self.reservations.put_item.call_args.kwargs["Item"]
        }
        self.table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        self.table.get_item.return_value = {"Item": original}

        self.assertEqual(self.submit(), (201, original))
        self.table.get_item.assert_called_once_with(
            Key={"partnerId": original["partnerId"]}, ConsistentRead=True
        )

        self.table.put_item.reset_mock()
        self.assertEqual(self.submit({**self.payload, "name": "Different"})[0], 409)
        self.table.put_item.assert_not_called()

    def test_retry_can_replay_a_partner_created_during_the_legacy_scan(self):
        self.payload["ein"] = "01-2345678"
        _, original = self.submit()
        claim = self.reservations.put_item.call_args.kwargs["Item"]
        self.reservations.put_item.reset_mock()
        self.reservations.get_item.side_effect = [{}, {"Item": claim}]
        self.paginator.paginate.return_value = [{"Items": [original]}]
        self.table.put_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        self.table.get_item.return_value = {"Item": original}

        self.assertEqual(self.submit(), (201, original))
        self.reservations.put_item.assert_not_called()

    def test_missing_fields_collect_errors_before_database_calls(self):
        status, body = self.submit({})
        self.assertEqual(status, 400)
        self.assertEqual(len(body["errors"]), 5)
        self.table.put_item.assert_not_called()
        self.table.meta.client.get_paginator.assert_not_called()

    def test_invalid_ein_and_focus_areas_are_rejected(self):
        for field, value in (
            ("ein", 123456789),
            ("ein", "bad"),
            ("focusAreas", []),
            ("focusAreas", ["Other"]),
        ):
            with self.subTest(field=field, value=value):
                payload = {**self.payload, field: value}
                self.assertEqual(self.submit(payload)[0], 400)
        self.table.put_item.assert_not_called()
        self.table.meta.client.get_paginator.assert_not_called()

    def test_location_fields_are_ignored_on_partner_submissions(self):
        status, body = self.submit(
            {**self.payload, "animalsAccepted": ["Dogs"], "timeLimits": "30 days"}
        )
        self.assertEqual(status, 201)
        self.assertNotIn("animalsAccepted", body)
        self.assertNotIn("timeLimits", body)
        stored = self.table.put_item.call_args.kwargs["Item"]
        self.assertNotIn("animalsAccepted", stored)
        self.assertNotIn("timeLimits", stored)

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

    def test_base64_encoded_request_is_rejected(self):
        body = base64.b64encode(json.dumps(self.payload).encode()).decode()
        response = create_partner.handler({"body": body, "isBase64Encoded": True}, None)
        self.assertEqual(response["statusCode"], 400)
        self.table.put_item.assert_not_called()

    def test_real_sdk_paginator_marshals_filter_and_advances_past_empty_page(self):
        resource = boto3.Session(
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
            region_name="us-east-1",
        ).resource("dynamodb")
        client = resource.meta.client
        expected = {
            "TableName": "test-partners",
            "FilterExpression": ANY,
            "ProjectionExpression": "partnerId",
            "ConsistentRead": True,
        }
        with (
            Stubber(client) as stubber,
            patch.object(create_partner, "table", resource.Table("test-partners")),
        ):
            stubber.add_response(
                "scan",
                {
                    "Items": [],
                    "LastEvaluatedKey": {"partnerId": {"S": "first-page"}},
                },
                expected,
            )
            stubber.add_response(
                "scan",
                {
                    "Items": [{"partnerId": {"S": "existing"}}],
                },
                {**expected, "ExclusiveStartKey": ANY},
            )
            self.assertTrue(create_partner.ein_exists("012345678"))
            stubber.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
