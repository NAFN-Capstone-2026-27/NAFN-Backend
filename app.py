#!/usr/bin/env python3

from aws_cdk import App, Environment
import aws_cdk

from NAFNBackend.FrontendInfrastructure.frontend_stack import FrontendStack
from NAFNBackend.BackendInfrastructure.nafn_backend_stack import NafnBackendStack

# GET /partners returns every partner's contact details to anyone, so it may only
# be deployed where the data is throwaway. This is checked here, not only in
# cdk.json, because environments are added by copying an existing block, and a
# copied "enable_partner_listing": true would otherwise expose production.
LISTING_ALLOWED_ENVIRONMENTS = {"dev", "staging"}

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
cert_arn = config["certificate_arn"]
hosted_zone_id = config["hosted_zone_id"]
hosted_zone_name = config["hosted_zone_name"]
domain_name = config["domain_name"]
project_name = config["project_name"]

env = Environment(account=account, region=region)

enable_partner_listing = config.get("enable_partner_listing", False)
if enable_partner_listing:
    if environment_name not in LISTING_ALLOWED_ENVIRONMENTS:
        raise ValueError(
            f"enable_partner_listing is set for '{environment_name}', but it's only "
            f"allowed in {sorted(LISTING_ALLOWED_ENVIRONMENTS)}. Remove it from "
            "this environment in cdk.json."
        )

FrontendStack(
    app, f"{project_name}-Frontend", cert_arn, hosted_zone_id, hosted_zone_name, domain_name, env,
    github_oidc_subject=config["github_oidc_subject"],
)

NafnBackendStack(
    app,
    f"{project_name}-Backend",
    domain_name,
    # Off unless an environment opts in, so new environments don't expose it.
    enable_partner_listing=enable_partner_listing,
    env=env,
)


app.synth()
