import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from NAFNBackend.BackendInfrastructure.nafn_backend_stack import NafnBackendStack


ROOT = Path(__file__).resolve().parents[1]


class AppEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "cdk.json").read_text())["context"]["environments"]

    def synthesize(self, environment):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                [sys.executable, str(ROOT / "app.py")],
                cwd=directory,
                env={
                    **os.environ,
                    "CDK_OUTDIR": directory,
                    "CDK_CONTEXT_JSON": json.dumps({
                        "env": environment, "environments": self.config,
                    }),
                },
                capture_output=True, text=True, check=True, timeout=60,
            )
            root = Path(directory)
            manifest = json.loads((root / "manifest.json").read_text())
            stacks = {
                name: artifact for name, artifact in manifest["artifacts"].items()
                if artifact["type"] == "aws:cloudformation:stack"
            }
            templates = {
                name: json.loads((root / artifact["properties"]["templateFile"]).read_text())
                for name, artifact in stacks.items()
            }
            return stacks, templates

    def test_dev_synthesizes_frontend_and_backend(self):
        stacks, templates = self.synthesize("dev")
        self.assertEqual(set(stacks), {"NAFN-Frontend", "NAFN-Backend"})
        self.assertTrue(all(a["environment"] == "aws://463735866782/us-east-1" for a in stacks.values()))
        api = templates["NAFN-Backend"]["Resources"]["NafnApi5CAE70CD"]
        self.assertEqual(api["Properties"]["CorsConfiguration"]["AllowOrigins"], ["*"])

    def test_prod_backend_configuration_preserves_existing_api_and_cors(self):
        config = self.config["prod"]
        with tempfile.TemporaryDirectory() as directory:
            app = App(outdir=directory)
            stack = NafnBackendStack(
                app, f"{config['project_name']}-Backend", config["domain_name"],
                cors_allow_origins=config["cors_allow_origins"],
                env=Environment(account=config["account"], region=config["region"]),
            )
            template = Template.from_stack(stack).to_json()
        self.assertEqual(stack.stack_name, "NAFN-Backend")
        self.assertEqual(stack.environment, "aws://892077328991/us-east-1")
        resources = template["Resources"]
        self.assertIn("NafnApi5CAE70CD", resources)
        self.assertEqual(resources["NafnApi5CAE70CD"]["Type"], "AWS::ApiGatewayV2::Api")
        self.assertEqual(resources["NafnApi5CAE70CD"]["Properties"]["CorsConfiguration"]["AllowOrigins"], [
            "https://PLACEHOLDER", "https://nafn.us", "https://dev.nafn.us",
        ])
        self.assertFalse(any(r["Type"].startswith(("AWS::CloudFront::", "AWS::Route53::", "AWS::CertificateManager::")) for r in resources.values()))


if __name__ == "__main__":
    unittest.main()
