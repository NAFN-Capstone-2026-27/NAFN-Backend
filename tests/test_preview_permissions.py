import json
from pathlib import Path
import tempfile
import unittest

from aws_cdk import App, CfnResource, Environment
from aws_cdk.assertions import Template

from NAFNBackend.FrontendInfrastructure.frontend_stack import FrontendStack


class PreviewPermissionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = json.loads(
            (Path(__file__).resolve().parents[1] / "cdk.json").read_text()
        )["context"]["environments"]["dev"]
        cls.config = config
        cls.output_dir = tempfile.TemporaryDirectory()
        app = App(outdir=cls.output_dir.name)
        stack = FrontendStack(
            app, "PermissionsTest", config["hosted_zone_id"],
            config["hosted_zone_name"], config["domain_name"],
            Environment(account=config["account"], region=config["region"]),
            github_oidc_subject=config["github_oidc_subject"],
            github_preview_oidc_subject=config["github_preview_oidc_subject"],
        )
        cls.template = Template.from_stack(stack).to_json()
        cls.resources = cls.template["Resources"]
        cls.resource_ids = {
            construct.node.path: stack.get_logical_id(construct)
            for construct in stack.node.find_all()
            if isinstance(construct, CfnResource)
        }
        cls.bucket_id = next(
            key for key, value in cls.resources.items()
            if value["Type"] == "AWS::S3::Bucket"
        )

    @classmethod
    def tearDownClass(cls):
        cls.output_dir.cleanup()

    def resource(self, path, resource_type):
        resource = self.resources[self.resource_ids[path]]
        self.assertEqual(resource["Type"], resource_type)
        return resource["Properties"]

    def policy(self, role):
        return self.resource(
            f"PermissionsTest/{role}/DefaultPolicy/Resource", "AWS::IAM::Policy"
        )["PolicyDocument"]["Statement"]

    def preview_objects(self):
        return {"Fn::Join": ["", [
            {"Fn::GetAtt": [self.bucket_id, "Arn"]}, "/previews/*"
        ]]}

    def test_preview_roles_cannot_list_root_or_write_outside_previews(self):
        for role in ("FrontendPreviewDeployRole", "FrontendPreviewCleanupRole"):
            with self.subTest(role=role):
                statements = self.policy(role)
                self.assertEqual(len(statements), 2)
                listing, objects = statements
                self.assertEqual(listing["Effect"], "Allow")
                self.assertEqual(listing["Action"], "s3:ListBucket")
                self.assertEqual(listing["Resource"], {"Fn::GetAtt": [self.bucket_id, "Arn"]})
                self.assertEqual(listing["Condition"], {
                    "StringLike": {"s3:prefix": "previews/*"}
                })
                self.assertEqual(objects["Effect"], "Allow")
                self.assertEqual(objects["Resource"], self.preview_objects())
                expected = (["s3:PutObject", "s3:DeleteObject"]
                            if role == "FrontendPreviewDeployRole" else "s3:DeleteObject")
                self.assertEqual(objects["Action"], expected)

    def test_pr_and_cleanup_roles_have_distinct_exact_trust(self):
        provider_ids = [k for k, v in self.resources.items()
                        if v["Type"] == "AWS::IAM::OIDCProvider"]
        self.assertEqual(len(provider_ids), 1)
        for role, subject in (
            ("FrontendPreviewDeployRole", self.config["github_preview_oidc_subject"]),
            ("FrontendPreviewCleanupRole", self.config["github_oidc_subject"]),
        ):
            with self.subTest(role=role):
                trust = self.resource(
                    f"PermissionsTest/{role}/Resource", "AWS::IAM::Role"
                )["AssumeRolePolicyDocument"]["Statement"]
                self.assertEqual(trust, [{
                    "Effect": "Allow",
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Principal": {"Federated": {"Fn::GetAtt": [provider_ids[0], "Arn"]}},
                    "Condition": {"StringEquals": {
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        "token.actions.githubusercontent.com:sub": subject,
                    }},
                }])
        self.assertTrue(self.config["github_preview_oidc_subject"].endswith(":pull_request"))
        self.assertTrue(self.config["github_oidc_subject"].endswith(":ref:refs/heads/main"))

    def test_preview_role_outputs_exist(self):
        for output, role in (
            ("FrontendPreviewDeployRoleArn", "FrontendPreviewDeployRole"),
            ("FrontendPreviewCleanupRoleArn", "FrontendPreviewCleanupRole"),
        ):
            role_id = self.resource_ids[f"PermissionsTest/{role}/Resource"]
            self.assertEqual(self.template["Outputs"][output]["Value"],
                             {"Fn::GetAtt": [role_id, "Arn"]})


if __name__ == "__main__":
    unittest.main()
