import tempfile
import unittest

from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from NAFNBackend.BackendInfrastructure.nafn_backend_stack import NafnBackendStack


class BackendTablesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output_dir = tempfile.TemporaryDirectory()
        app = App(outdir=cls.output_dir.name)
        cls.stack = NafnBackendStack(
            app, "BackendTest", "dev.example.org",
            env=Environment(account="123456789012", region="us-east-1"),
        )
        cls.template = Template.from_stack(cls.stack).to_json()

    @classmethod
    def tearDownClass(cls):
        cls.output_dir.cleanup()

    def table_resource(self, table):
        resource = table.node.default_child
        return self.template["Resources"][self.stack.get_logical_id(resource)]

    def table_reference(self, table):
        return {"Ref": self.stack.get_logical_id(table.node.default_child)}

    def policy_statements(self, function):
        role_id = self.stack.get_logical_id(function.role.node.default_child)
        return [
            statement
            for resource in self.template["Resources"].values()
            if resource["Type"] == "AWS::IAM::Policy"
            and {"Ref": role_id} in resource["Properties"]["Roles"]
            for statement in resource["Properties"]["PolicyDocument"]["Statement"]
        ]

    def table_arn(self, table):
        return {"Fn::GetAtt": [self.stack.get_logical_id(table.node.default_child), "Arn"]}

    def test_new_tables_use_independent_string_ids_without_indexes(self):
        for table, key in (
            (self.stack.partners_table, "partnerId"),
            (self.stack.resources_table, "resourceId"),
        ):
            with self.subTest(key=key):
                resource = self.table_resource(table)
                props = resource["Properties"]
                self.assertEqual(props["KeySchema"], [{"AttributeName": key, "KeyType": "HASH"}])
                self.assertEqual(props["AttributeDefinitions"], [{"AttributeName": key, "AttributeType": "S"}])
                self.assertEqual(props["BillingMode"], "PAY_PER_REQUEST")
                self.assertNotIn("GlobalSecondaryIndexes", props)
                self.assertNotIn("LocalSecondaryIndexes", props)
                self.assertTrue(props["Replicas"][0]["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"])
                self.assertEqual(resource["DeletionPolicy"], "Retain")

    def test_only_new_tables_remain(self):
        tables = {
            key for key, resource in self.template["Resources"].items()
            if resource["Type"] == "AWS::DynamoDB::GlobalTable"
        }
        self.assertEqual(tables, {
            self.stack.get_logical_id(self.stack.partners_table.node.default_child),
            self.stack.get_logical_id(self.stack.resources_table.node.default_child),
        })

    def test_partner_handlers_and_output_use_the_new_table(self):
        new_table = self.table_reference(self.stack.partners_table)
        for function in (self.stack.create_partner_function, self.stack.list_partners_function):
            resource = self.template["Resources"][self.stack.get_logical_id(function.node.default_child)]
            self.assertEqual(resource["Properties"]["Environment"]["Variables"]["PARTNERS_TABLE_NAME"], new_table)
        self.assertEqual(self.template["Outputs"]["PartnersTableName"]["Value"], new_table)
        self.assertEqual(
            self.template["Outputs"]["ResourcesTableName"]["Value"],
            self.table_reference(self.stack.resources_table),
        )

    def test_partner_create_permissions_are_limited_to_scan_and_insert_on_new_table(self):
        statements = self.policy_statements(self.stack.create_partner_function)
        self.assertEqual(len(statements), 1)
        self.assertEqual(set(statements[0]["Action"]), {"dynamodb:PutItem", "dynamodb:Scan"})
        self.assertEqual(statements[0]["Resource"], {
            "Fn::GetAtt": [self.stack.get_logical_id(self.stack.partners_table.node.default_child), "Arn"],
        })

    def test_resource_handlers_receive_only_the_tables_they_use(self):
        expected = (
            (self.stack.create_resource_function, {
                "PARTNERS_TABLE_NAME": self.table_reference(self.stack.partners_table),
                "RESOURCES_TABLE_NAME": self.table_reference(self.stack.resources_table),
            }),
            (self.stack.list_resources_function, {
                "RESOURCES_TABLE_NAME": self.table_reference(self.stack.resources_table),
            }),
        )
        for function, variables in expected:
            with self.subTest(function=function.node.id):
                props = self.template["Resources"][self.stack.get_logical_id(function.node.default_child)]["Properties"]
                self.assertEqual(props["Environment"]["Variables"], variables)
                self.assertEqual(props["Runtime"], "python3.13")
                self.assertEqual(props["Architectures"], ["arm64"])
                self.assertEqual(props["MemorySize"], 256)
                self.assertEqual(props["Timeout"], 10)
                self.assertIn("LogGroup", props["LoggingConfig"])

    def test_resource_permissions_are_table_scoped_and_have_no_delete_or_update(self):
        create_statements = self.policy_statements(self.stack.create_resource_function)
        self.assertEqual(len(create_statements), 2)
        self.assertCountEqual(create_statements, [
            {"Effect": "Allow", "Action": "dynamodb:PutItem", "Resource": self.table_arn(self.stack.resources_table)},
            {"Effect": "Allow", "Action": "dynamodb:GetItem", "Resource": self.table_arn(self.stack.partners_table)},
        ])
        self.assertEqual(self.policy_statements(self.stack.list_resources_function), [
            {"Effect": "Allow", "Action": "dynamodb:Scan", "Resource": self.table_arn(self.stack.resources_table)},
        ])

    def test_each_route_integrates_with_the_correct_handler_on_the_existing_api(self):
        resources = self.template["Resources"]
        routes = {
            r["Properties"]["RouteKey"]: r["Properties"]
            for r in resources.values() if r["Type"] == "AWS::ApiGatewayV2::Route"
        }
        expected = {
            "POST /partners": self.stack.create_partner_function,
            "GET /partners": self.stack.list_partners_function,
            "POST /resources": self.stack.create_resource_function,
            "GET /resources": self.stack.list_resources_function,
        }
        self.assertEqual(set(routes), set(expected))
        api_ref = {"Ref": self.stack.get_logical_id(self.stack.api.node.default_child)}
        for route_key, function in expected.items():
            with self.subTest(route=route_key):
                function_id = self.stack.get_logical_id(function.node.default_child)
                integrations = [
                    key for key, r in resources.items()
                    if r["Type"] == "AWS::ApiGatewayV2::Integration"
                    and r["Properties"]["IntegrationUri"] == {"Fn::GetAtt": [function_id, "Arn"]}
                ]
                self.assertEqual(len(integrations), 1)
                self.assertEqual(routes[route_key]["ApiId"], api_ref)
                self.assertEqual(routes[route_key]["Target"], {
                    "Fn::Join": ["", ["integrations/", {"Ref": integrations[0]}]],
                })


if __name__ == "__main__":
    unittest.main()
