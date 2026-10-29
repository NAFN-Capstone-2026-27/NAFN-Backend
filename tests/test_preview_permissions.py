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
            app, "PermissionsTest", config["certificate_arn"], config["hosted_zone_id"],
            config["hosted_zone_name"], config["domain_name"],
            Environment(account=config["account"], region=config["region"]),
            enable_previews=config["enable_previews"],
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

    def test_preview_role_cannot_list_root_or_write_outside_previews(self):
        statements = self.policy("FrontendPreviewDeployRole")
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
        self.assertEqual(set(objects["Action"]), {"s3:PutObject", "s3:DeleteObject"})

    def test_preview_role_trusts_only_exact_pr_and_main_subjects(self):
        provider_ids = [k for k, v in self.resources.items()
                        if v["Type"] == "AWS::IAM::OIDCProvider"]
        self.assertEqual(len(provider_ids), 1)
        trust = self.resource(
            "PermissionsTest/FrontendPreviewDeployRole/Resource", "AWS::IAM::Role"
        )["AssumeRolePolicyDocument"]["Statement"]
        self.assertEqual(trust, [{
            "Effect": "Allow",
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Principal": {"Federated": {"Fn::GetAtt": [provider_ids[0], "Arn"]}},
            "Condition": {"StringEquals": {
                "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                "token.actions.githubusercontent.com:sub": [
                    self.config["github_preview_oidc_subject"],
                    self.config["github_oidc_subject"],
                ],
            }},
        }])
        self.assertTrue(self.config["github_preview_oidc_subject"].endswith(":pull_request"))
        self.assertTrue(self.config["github_oidc_subject"].endswith(":ref:refs/heads/main"))

    def test_dev_preview_routing_and_domains_exist(self):
        self.assertEqual(sum(r["Type"] == "AWS::CloudFront::Function"
                             for r in self.resources.values()), 1)
        distribution = next(r["Properties"]["DistributionConfig"]
                            for r in self.resources.values()
                            if r["Type"] == "AWS::CloudFront::Distribution")
        self.assertEqual(distribution["ViewerCertificate"]["AcmCertificateArn"],
                         self.config["certificate_arn"])
        self.assertFalse(any(r["Type"] == "AWS::CertificateManager::Certificate"
                             for r in self.resources.values()))
        self.assertEqual(distribution["Aliases"], [
            self.config["domain_name"], f"*.{self.config['domain_name']}"
        ])
        records = [r["Properties"]["Name"] for r in self.resources.values()
                   if r["Type"] == "AWS::Route53::RecordSet"]
        self.assertCountEqual(records, [
            f"{self.config['domain_name']}.",
            f"{self.config['domain_name']}.",
            f"*.{self.config['domain_name']}.",
            f"*.{self.config['domain_name']}.",
        ])
        self.assertEqual(len(distribution["DefaultCacheBehavior"]["FunctionAssociations"]), 1)

    def test_only_shared_preview_role_output_exists(self):
        role_id = self.resource_ids["PermissionsTest/FrontendPreviewDeployRole/Resource"]
        self.assertEqual(self.template["Outputs"]["FrontendPreviewDeployRoleArn"]["Value"],
                         {"Fn::GetAtt": [role_id, "Arn"]})
        self.assertNotIn("FrontendPreviewCleanupRoleArn", self.template["Outputs"])
        self.assertNotIn("PermissionsTest/FrontendPreviewCleanupRole/Resource", self.resource_ids)


class PreviewEnvironmentTests(unittest.TestCase):
    def test_staging_and_prod_have_no_preview_infrastructure(self):
        environments = json.loads(
            (Path(__file__).resolve().parents[1] / "cdk.json").read_text()
        )["context"]["environments"]
        for name in ("staging", "prod"):
            with self.subTest(environment=name), tempfile.TemporaryDirectory() as outdir:
                config = environments[name]
                self.assertFalse(config.get("enable_previews", False))
                app = App(outdir=outdir)
                stack = FrontendStack(
                    app, f"{name}Test", config["certificate_arn"], config["hosted_zone_id"],
                    config["hosted_zone_name"], config["domain_name"],
                    Environment(account=config["account"], region=config["region"]),
                    github_oidc_subject=config["github_oidc_subject"],
                    enable_previews=config.get("enable_previews", False),
                    github_preview_oidc_subject=config.get("github_preview_oidc_subject"),
                )
                template = Template.from_stack(stack).to_json()
                resources = list(template["Resources"].values())
                self.assertFalse(any(r["Type"] == "AWS::CloudFront::Function"
                                     for r in resources))
                distribution = next(r["Properties"]["DistributionConfig"]
                                    for r in resources
                                    if r["Type"] == "AWS::CloudFront::Distribution")
                self.assertEqual(distribution["Aliases"], [config["domain_name"]])
                self.assertFalse(distribution["DefaultCacheBehavior"].get("FunctionAssociations"))
                records = [r["Properties"]["Name"] for r in resources
                           if r["Type"] == "AWS::Route53::RecordSet"]
                self.assertEqual(records, [f"{config['domain_name']}."] * 2)
                self.assertEqual(distribution["ViewerCertificate"]["AcmCertificateArn"],
                                 config["certificate_arn"])
                self.assertFalse(any(r["Type"] == "AWS::CertificateManager::Certificate"
                                     for r in resources))
                roles = [r["Properties"] for r in resources
                         if r["Type"] == "AWS::IAM::Role"]
                self.assertFalse(any("preview" in r.get("Description", "").lower()
                                     for r in roles))
                self.assertFalse(any("Preview" in key for key in template["Outputs"]))
                self.assertIn("FrontendDeployRoleArn", template["Outputs"])


if __name__ == "__main__":
    unittest.main()
