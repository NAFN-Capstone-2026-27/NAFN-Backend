import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from NAFNBackend.BackendInfrastructure.nafn_backend_stack import NafnBackendStack

ROOT = Path(__file__).resolve().parents[1]


class AppEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "cdk.json").read_text())["context"][
            "environments"
        ]

    def synthesize(self, environment):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                [sys.executable, str(ROOT / "app.py")],
                cwd=directory,
                env={
                    **os.environ,
                    "CDK_OUTDIR": directory,
                    "CDK_CONTEXT_JSON": json.dumps(
                        {
                            "env": environment,
                            "environments": self.config,
                        }
                    ),
                },
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            root = Path(directory)
            manifest = json.loads((root / "manifest.json").read_text())
            stacks = {
                name: artifact
                for name, artifact in manifest["artifacts"].items()
                if artifact["type"] == "aws:cloudformation:stack"
            }
            templates = {
                name: json.loads(
                    (root / artifact["properties"]["templateFile"]).read_text()
                )
                for name, artifact in stacks.items()
            }
            return stacks, templates

    def test_dev_synthesizes_frontend_backend_and_github_oidc(self):
        stacks, templates = self.synthesize("dev")
        self.assertEqual(
            set(stacks), {"NAFN-Frontend", "NAFN-Backend", "NAFN-GitHubOidc"}
        )
        self.assertTrue(
            all(
                a["environment"] == "aws://463735866782/us-east-1"
                for a in stacks.values()
            )
        )
        self.assert_api_domain(templates["NAFN-Backend"], "api.dev.nafn.us")
        api = templates["NAFN-Backend"]["Resources"]["NafnApi5CAE70CD"]
        self.assertEqual(api["Properties"]["CorsConfiguration"]["AllowOrigins"], ["*"])

    def test_prod_backend_configuration_preserves_existing_api_and_allows_any_origin(
        self,
    ):
        config = self.config["prod"]
        with tempfile.TemporaryDirectory() as directory:
            app = App(outdir=directory)
            stack = NafnBackendStack(
                app,
                f"{config['project_name']}-Backend",
                db_instance_class=config["db_instance_class"],
                db_instance_size=config["db_instance_size"],
                domain_name=config["domain_name"],
                hosted_zone_id=config["hosted_zone_id"],
                hosted_zone_name=config["hosted_zone_name"],
                cors_allow_origins=config["cors_allow_origins"],
                env=Environment(account=config["account"], region=config["region"]),
            )
            template = Template.from_stack(stack).to_json()
        self.assertEqual(stack.stack_name, "NAFN-Backend")
        self.assertEqual(stack.environment, "aws://892077328991/us-east-1")
        resources = template["Resources"]
        self.assertIn("NafnApi5CAE70CD", resources)
        self.assertEqual(resources["NafnApi5CAE70CD"]["Type"], "AWS::ApiGatewayV2::Api")
        self.assertEqual(
            resources["NafnApi5CAE70CD"]["Properties"]["CorsConfiguration"][
                "AllowOrigins"
            ],
            ["*"],
        )
        self.assert_api_domain(template, "api.nafn.us")

    def assert_api_domain(self, template, domain):
        assertions = Template.from_json(template)
        assertions.has_resource_properties(
            "AWS::CertificateManager::Certificate",
            {"DomainName": domain, "ValidationMethod": "DNS"},
        )
        assertions.has_resource_properties(
            "AWS::ApiGatewayV2::DomainName", {"DomainName": domain}
        )
        assertions.has_resource_properties(
            "AWS::ApiGatewayV2::ApiMapping",
            {"ApiId": {"Ref": "NafnApi5CAE70CD"}},
        )
        assertions.has_resource_properties(
            "AWS::Route53::RecordSet", {"Name": f"{domain}.", "Type": "A"}
        )
        self.assertEqual(template["Outputs"]["ApiUrl"]["Value"], f"https://{domain}")


if __name__ == "__main__":
    unittest.main()
