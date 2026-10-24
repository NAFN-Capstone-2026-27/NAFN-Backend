#!/usr/bin/env python3

from aws_cdk import App, Environment
import aws_cdk

from NAFNBackend.FrontendInfrastructure.frontend_stack import FrontendStack
from NAFNBackend.BackendInfrastructure.nafn_backend_stack import NafnBackendStack

app = App()

environment_name = app.node.try_get_context("env")

if not environment_name:
    raise ValueError(
        "Environment must be specified."
        "Example: cdk deploy -c env=dev"
    )

environments = app.node.try_get_context("environments")

if environment_name not in environments:
    raise ValueError(f"Unknown environment: {environment_name}")

config = environments[environment_name]

env = aws_cdk.Environment(
    account=config["account"],
    region=config["region"]
)

# Intentionally errors if doesn't exist
account = config["account"]
region = config["region"]
# TODO: These parameters should be tied to the account and region.
hosted_zone_id = config["hosted_zone_id"]
hosted_zone_name = config["hosted_zone_name"]
domain_name = config["domain_name"]
project_name = config["project_name"]

env = Environment(account=account, region=region)

FrontendStack(
    app, f"{project_name}-Frontend", hosted_zone_id, hosted_zone_name, domain_name, env,
    github_oidc_subject=config["github_oidc_subject"],
    github_preview_oidc_subject=config["github_preview_oidc_subject"],
)

NafnBackendStack(
    app,
    f"{project_name}-Backend",
    domain_name,
    env=env,
)


app.synth()
