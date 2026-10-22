#!/usr/bin/env python3
"""Smoke-test a deployed backend by creating, listing and removing a dummy partner.

Usage: python post_deployment_tests.py --env dev

Needs AWS credentials for the environment's account: the script reads the stack
outputs and deletes the dummy partner from DynamoDB when it's done.

The script writes to the environment it tests, so it only runs against the
environments in WRITABLE_ENVIRONMENTS and refuses everything else, including
production. See check_target_is_writable for the guards.
"""

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Attr

CDK_JSON = Path(__file__).parent / "cdk.json"

# The only environments this script may create or delete records in. This lives
# in code rather than cdk.json on purpose: environments are added by copying an
# existing block in cdk.json, and a copied opt-in flag would silently enable
# writes in the new environment. Adding a name here takes a reviewed code change.
WRITABLE_ENVIRONMENTS = {"dev", "staging"}

DUMMY_PARTNER_NAME = "Post-Deployment Test Partner"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", required=True, help="Environment name from cdk.json")
    args = parser.parse_args()

    environments = json.loads(CDK_JSON.read_text())["context"]["environments"]
    if args.env not in environments:
        sys.exit(f"Unknown environment: {args.env}")
    config = environments[args.env]
    listing_enabled = config.get("enable_partner_listing", False)

    # Every AWS call below goes through this session, so the account checked
    # here is the account the script actually touches.
    session = boto3.Session(region_name=config["region"])
    check_target_is_writable(args.env, environments, session)

    outputs = stack_outputs(session, f"{config['project_name']}-Backend")
    partners_url = f"{outputs['ApiUrl']}/partners"
    table = session.resource("dynamodb").Table(outputs["PartnersTableName"])

    # The IRS never issues EINs starting with 00, so this can't collide with a
    # real partner. It's sent with a dash and stored as digits only.
    ein = f"00{random.randint(0, 9_999_999):07d}"
    partner = {
        "name": DUMMY_PARTNER_NAME,
        "address": "1 Test St, Denver CO 80202",
        "email": "post-deployment-test@example.com",
        "phone": "(303) 555-0100",
        "ein": f"{ein[:2]}-{ein[2:]}",
        "focusAreas": ["Domestic Violence", "Climate Emergency"],
        "animalsAccepted": ["Dogs", "Cats"],
        "timeLimits": "Up to 30 days",
    }

    results = []
    try:
        status, body = request("POST", partners_url, partner)
        results.append(check(
            "POST valid partner returns 201",
            status == 201
            and body.get("ein") == ein
            and body.get("phone") == "3035550100",
            status, body,
        ))

        status, body = request("POST", partners_url, partner)
        results.append(check("POST duplicate EIN returns 409", status == 409, status, body))

        status, body = request("POST", partners_url, {"name": "Missing fields"})
        results.append(check(
            "POST invalid partner returns 400",
            status == 400 and body.get("errors"),
            status, body,
        ))

        status, body = request("GET", partners_url)
        if listing_enabled:
            eins = {p.get("ein") for p in body.get("partners", [])}
            results.append(check(
                "GET lists the dummy partner", status == 200 and ein in eins, status, body
            ))
        else:
            results.append(check(
                "GET listing is not exposed", not 200 <= status < 300, status, body
            ))
    finally:
        delete_dummy_partner(table, ein)

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    sys.exit(0 if passed == len(results) else 1)


def check_target_is_writable(env_name, environments, session):
    """Exit before any request is sent unless the target is safe to write to."""
    # 1. The environment must be explicitly allowed.
    if env_name not in WRITABLE_ENVIRONMENTS:
        sys.exit(
            f"Refusing to run against '{env_name}': this script creates and deletes "
            f"records, and only runs against {sorted(WRITABLE_ENVIRONMENTS)}."
        )

    # 2. The AWS credentials must belong to that environment's account. Stacks
    # are looked up by name in whatever account the credentials point at, so
    # without this, running "--env dev" while logged in to the production
    # account would test production's stack.
    config = environments[env_name]
    account = session.client("sts").get_caller_identity()["Account"]
    if account != config["account"]:
        sys.exit(
            f"Refusing to run: your AWS credentials are for account {account}, but "
            f"'{env_name}' is in account {config['account']}."
        )

    # 3. The stack mustn't be shared with an environment that isn't allowed.
    # Two environments with the same account, region and project_name deploy
    # to the same stack, so the same table.
    for other_name, other in environments.items():
        if other_name in WRITABLE_ENVIRONMENTS:
            continue
        same_stack = all(
            other.get(key) == config[key]
            for key in ("account", "region", "project_name")
        )
        if same_stack:
            sys.exit(
                f"Refusing to run: '{env_name}' deploys to the same stack as "
                f"'{other_name}', which this script must not write to."
            )


def delete_dummy_partner(table, ein):
    """Delete the dummy partner, but only if the item really is the dummy.

    The condition is a last line of defense: even if every other guard failed,
    this can only ever remove a record this script created.
    """
    try:
        table.delete_item(
            Key={"ein": ein},
            ConditionExpression=(
                Attr("name").eq(DUMMY_PARTNER_NAME) & Attr("ein").begins_with("00")
            ),
        )
        print(f"Cleaned up dummy partner {ein}")
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        # Also raised when the item doesn't exist, e.g. the create step failed.
        print(f"No dummy partner {ein} to clean up; nothing deleted")


def stack_outputs(session, stack_name):
    cloudformation = session.client("cloudformation")
    stack = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def request(method, url, payload=None):
    """Send a JSON request and return (status, parsed body), including for 4xx/5xx."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    try:
        return status, json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return status, {"raw": raw.decode(errors="replace")}


def check(name, ok, status, body):
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"      got {status}: {json.dumps(body)[:500]}")
    return bool(ok)


if __name__ == "__main__":
    main()
