import contextlib
import io
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from boto3.dynamodb.conditions import Attr

import post_deployment_tests as smoke


class ConditionalFailure(Exception):
    pass


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.config = {"account": "123456789012", "region": "us-east-1", "project_name": "Demo"}
        self.environments = {"dev": self.config}
        self.session = MagicMock()
        self.session.region_name = "us-east-1"
        self.session.client.return_value.get_caller_identity.return_value = {"Account": "123456789012"}
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def test_production_is_rejected_before_aws_or_http(self):
        with self.assertRaises(ValueError):
            smoke.check_target_is_writable("prod", {"prod": self.config}, self.session)
        self.session.client.assert_not_called()

    def test_wrong_account_is_rejected(self):
        self.session.client.return_value.get_caller_identity.return_value = {"Account": "999999999999"}
        with self.assertRaisesRegex(ValueError, "Credentials"):
            smoke.check_target_is_writable("dev", self.environments, self.session)

    def test_wrong_region_is_rejected(self):
        self.session.region_name = "us-west-2"
        with self.assertRaisesRegex(ValueError, "region"):
            smoke.check_target_is_writable("dev", self.environments, self.session)

    def test_environment_cannot_alias_a_production_stack(self):
        with self.assertRaisesRegex(ValueError, "shares a stack"):
            smoke.check_target_is_writable("dev", {**self.environments, "prod": self.config.copy()}, self.session)

    def test_allowed_account_region_and_stack_are_accepted(self):
        smoke.check_target_is_writable("dev", self.environments, self.session)

    def test_cleanup_uses_exact_id_and_unique_name_condition(self):
        table = MagicMock()
        table.meta.client.exceptions.ConditionalCheckFailedException = ConditionalFailure
        identifier = str(uuid4())
        name = f"Post-Deployment Test {uuid4()} Location"
        smoke.delete_test_record(table, "resourceId", identifier, name)
        table.delete_item.assert_called_once_with(
            Key={"resourceId": identifier}, ConditionExpression=Attr("name").eq(name),
        )

    def test_cleanup_rejects_non_test_records_or_wrong_keys(self):
        table = MagicMock()
        for key, identifier, name in (
            ("ein", "123456789", "Post-Deployment Test Example"),
            ("partnerId", "not-a-uuid", "Post-Deployment Test Example"),
            ("resourceId", str(uuid4()), "Real Resource"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke.delete_test_record(table, key, identifier, name)
        table.delete_item.assert_not_called()

    def test_cleanup_condition_failure_is_reported(self):
        table = MagicMock()
        table.meta.client.exceptions.ConditionalCheckFailedException = ConditionalFailure
        table.delete_item.side_effect = ConditionalFailure()
        with self.assertRaisesRegex(RuntimeError, "name changed"):
            smoke.delete_test_record(table, "partnerId", str(uuid4()), "Post-Deployment Test Example")

    def test_missing_new_table_output_stops_before_requests(self):
        with patch.object(smoke, "stack_outputs", return_value={"ApiUrl": "https://example.org"}), \
             patch.object(smoke, "request") as request:
            with self.assertRaisesRegex(ValueError, "required table/API outputs"):
                smoke.run_checks(self.session, self.config)
            request.assert_not_called()

    def test_unexpected_api_endpoint_stops_before_requests(self):
        outputs = {"ApiUrl": "https://unrelated.example", "PartnersTableName": "partners", "ResourcesTableName": "resources"}
        with patch.object(smoke, "stack_outputs", return_value=outputs), patch.object(smoke, "request") as request:
            with self.assertRaisesRegex(ValueError, "API Gateway"):
                smoke.run_checks(self.session, self.config)
            request.assert_not_called()

    def exercise_backend(self, *, allow_duplicates=False, fail_listing=False, cleanup_failure=False):
        outputs = {
            "ApiUrl": "https://example.execute-api.us-east-1.amazonaws.com",
            "PartnersTableName": "partners", "ResourcesTableName": "resources",
        }
        records = {"partners": [], "resources": []}
        deleted = []
        tables = {name: MagicMock() for name in records}
        self.tables = tables
        self.session.resource.return_value.Table.side_effect = tables.__getitem__
        for table_name, table in tables.items():
            table.meta.client.exceptions.ConditionalCheckFailedException = ConditionalFailure

            def delete(*, Key, ConditionExpression, table_name=table_name):
                if cleanup_failure and not deleted:
                    deleted.append((table_name, Key, "failed"))
                    raise RuntimeError("cleanup unavailable")
                key, identifier = next(iter(Key.items()))
                match = next(item for item in records[table_name] if item[key] == identifier)
                self.assertEqual(ConditionExpression, Attr("name").eq(match["name"]))
                records[table_name].remove(match)
                deleted.append((table_name, Key, "deleted"))

            table.delete_item.side_effect = delete

        def request(method, url, payload=None):
            name = url.rsplit("/", 1)[-1]
            if method == "GET":
                if fail_listing:
                    raise OSError("HTTP unavailable")
                return 200, {name: records[name].copy()}
            if name == "partners":
                if "email" not in payload:
                    return 400, {"errors": ["email required"]}
                ein = payload.get("ein", "").strip().replace("-", "")
                if ein and not allow_duplicates and any(item.get("ein") == ein for item in records[name]):
                    return 409, {"error": "duplicate EIN"}
                item = {**payload, "partnerId": str(uuid4()), "createdAt": "2026-10-26T12:00:00+00:00", "phone": "3035550100"}
                if ein:
                    item["ein"] = ein
                else:
                    item.pop("ein", None)
            else:
                if payload["category"] == "Unknown" or payload["lat"] == 91:
                    return 400, {"errors": ["invalid resource"]}
                if "partnerId" in payload and not any(item["partnerId"] == payload["partnerId"] for item in records["partners"]):
                    return 400, {"errors": ["missing partner"]}
                item = {**payload, "resourceId": str(uuid4()), "createdAt": "2026-10-26T12:00:00+00:00"}
            records[name].append(item)
            return 201, item.copy()

        with patch.object(smoke, "stack_outputs", return_value=outputs), patch.object(smoke, "request", side_effect=request):
            result = smoke.run_checks(self.session, self.config)
        return result, records, deleted

    def test_full_smoke_flow_passes_and_removes_resources_before_partners(self):
        result, records, deleted = self.exercise_backend()
        self.assertEqual(result, 0)
        self.assertEqual(records, {"partners": [], "resources": []})
        self.assertEqual([name for name, _, _ in deleted], ["resources", "resources", "partners", "partners", "partners"])
        self.assertIn("12/12 checks passed", self.output.getvalue())

    def test_unexpected_duplicate_creation_is_also_cleaned_up(self):
        result, records, deleted = self.exercise_backend(allow_duplicates=True)
        self.assertEqual(result, 1)
        self.assertEqual(records, {"partners": [], "resources": []})
        self.assertEqual(len(deleted), 6)

    def test_http_failure_still_cleans_up_created_partners(self):
        with self.assertRaisesRegex(OSError, "HTTP unavailable"):
            self.exercise_backend(fail_listing=True)
        self.assertEqual(self.tables["partners"].delete_item.call_count, 3)

    def test_cleanup_failure_is_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, "Test cleanup incomplete"):
            self.exercise_backend(cleanup_failure=True)
        self.assertEqual(self.tables["resources"].delete_item.call_count, 2)
        self.assertEqual(self.tables["partners"].delete_item.call_count, 3)


if __name__ == "__main__":
    unittest.main()
