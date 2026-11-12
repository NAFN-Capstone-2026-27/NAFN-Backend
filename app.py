#!/usr/bin/env python3

from aws_cdk import App, Environment

from NAFNBackend.BackendInfrastructure.api_stack import ApiStack
from NAFNBackend.DataInfrastructure.data_stack import DataStack
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

# Intentionally errors if doesn't exist
account = config["account"]
region = config["region"]
# TODO: These parameters should be tied to the account and region.
cert_arn = config["certificate_arn"]
hosted_zone_id = config["hosted_zone_id"]
hosted_zone_name = config["hosted_zone_name"]
domain_name = config["domain_name"]
project_name = config["project_name"]
frontend_preview_github_oidc_subject = config.get(
    "frontend_preview_github_oidc_subject"
)

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
    enable_previews=bool(frontend_preview_github_oidc_subject),
)

data_stack = DataStack(app, f"{project_name}-Data", env=env)

ApiStack(
    app,
    f"{project_name}-Api",
    partners_table=data_stack.partners_table,
    resources_table=data_stack.resources_table,
    db_instance_class=config["db_instance_class"],
    db_instance_size=config["db_instance_size"],
    domain_name=domain_name,
    hosted_zone_id=hosted_zone_id,
    hosted_zone_name=hosted_zone_name,
    cors_allow_origins=config.get("cors_allow_origins"),
    env=env,
)

GitHubOidcStack(
    app,
    f"{project_name}-GitHubOidc",
    env,
    environment_name=environment_name,
    frontend_bucket_name=domain_name,
    frontend_github_oidc_subject=config.get("frontend_github_oidc_subject"),
    frontend_preview_github_oidc_subject=frontend_preview_github_oidc_subject,
    cdk_deploy_github_oidc_subject=config.get("cdk_deploy_github_oidc_subject"),
)

app.synth()
