#!/usr/bin/env python3

import aws_cdk
from aws_cdk import App, Environment

from NAFNBackend.BackendInfrastructure.nafn_backend_stack import NafnBackendStack
from NAFNBackend.DeploymentInfrastructure.github_oidc_stack import GitHubOidcStack
from NAFNBackend.FrontendInfrastructure.frontend_stack import FrontendStack

app = App()

environment_name = app.node.try_get_context("env")

if not environment_name:
    raise ValueError("Environment must be specified.Example: cdk deploy -c env=dev")

environments = app.node.try_get_context("environments")

if environment_name not in environments:
    raise ValueError(f"Unknown environment: {environment_name}")

config = environments[environment_name]

env = aws_cdk.Environment(account=config["account"], region=config["region"])

# Intentionally errors if doesn't exist
account = config["account"]
region = config["region"]
# TODO: These parameters should be tied to the account and region.
cert_arn = config["certificate_arn"]
hosted_zone_id = config["hosted_zone_id"]
hosted_zone_name = config["hosted_zone_name"]
domain_name = config["domain_name"]
project_name = config["project_name"]
enable_previews = config.get("enable_previews", False)
frontend_preview_github_oidc_subject = config.get(
    "frontend_preview_github_oidc_subject"
)

if enable_previews and not frontend_preview_github_oidc_subject:
    raise ValueError("Enabled previews require frontend_preview_github_oidc_subject")

env = Environment(account=account, region=region)

FrontendStack(
    app,
    f"{project_name}-Frontend",
    environment_name=environment_name,
    cert_arn=cert_arn,
    hosted_zone_id=hosted_zone_id,
    hosted_zone_name=hosted_zone_name,
    domain_name=domain_name,
    env=env,
    enable_previews=enable_previews,
)

NafnBackendStack(
    app,
    f"{project_name}-Backend",
    domain_name,
    env=env,
)

GitHubOidcStack(
    app,
    f"{project_name}-GitHubOidc",
    env,
    frontend_bucket_name=domain_name,
    frontend_github_oidc_subject=config["frontend_github_oidc_subject"],
    frontend_preview_github_oidc_subject=frontend_preview_github_oidc_subject,
    cdk_deploy_github_oidc_subject=config.get("cdk_deploy_github_oidc_subject"),
)

app.synth()
