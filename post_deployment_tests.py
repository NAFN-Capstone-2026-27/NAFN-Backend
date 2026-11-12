#!/usr/bin/env python3
"""Smoke-test the deployed partner/resource API, then delete this run's test records.

Usage: python post_deployment_tests.py --env dev --profile default
This script writes to AWS. Only explicitly allowed environments may be tested.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID, uuid4

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import BotoCoreError, ClientError

CDK_JSON = Path(__file__).parent / "cdk.json"
WRITABLE_ENVIRONMENTS = {"dev", "staging"}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", required=True)
    parser.add_argument("--profile", help="AWS profile; otherwise use the SDK default")
    args = parser.parse_args()
    try:
        environments = json.loads(CDK_JSON.read_text())["context"]["environments"]
        if args.env not in environments:
            raise ValueError(f"Unknown environment: {args.env}")
        config = environments[args.env]
        session = boto3.Session(profile_name=args.profile, region_name=config["region"])
        check_target_is_writable(args.env, environments, session)
        return run_checks(session, config)
    except (ValueError, RuntimeError, BotoCoreError, ClientError, OSError) as error:
        print(f"Smoke test failed: {error}", file=sys.stderr)
        return 1


def run_checks(session, config):
    outputs = stack_outputs(session, f"{config['project_name']}-Api")
    required = {"ApiUrl", "PartnersTableName", "ResourcesTableName"}
    if not required.issubset(outputs):
        raise ValueError(
            "Deploy the new backend first: required table/API outputs are missing."
        )
    base_url = outputs["ApiUrl"].rstrip("/")
    if not re.fullmatch(
        rf"https://[a-z0-9]+\.execute-api\.{re.escape(config['region'])}\.amazonaws\.com",
        base_url,
    ):
        raise ValueError("ApiUrl must be this region's generated API Gateway endpoint.")
    db = session.resource("dynamodb")
    partners_table = db.Table(outputs["PartnersTableName"])
    resources_table = db.Table(outputs["ResourcesTableName"])
    partners_url = f"{base_url}/partners"
    resources_url = f"{base_url}/resources"
    prefix = f"Post-Deployment Test {uuid4()}"
    partner = {
        "name": f"{prefix} Organization",
        "address": "1 Test St, Denver CO 80202",
        "email": "post-deployment-test@example.com",
        "phone": "(303) 555-0100",
        "focusAreas": ["Domestic Violence", "Climate Emergency"],
    }
    resource = {
        "name": f"{prefix} Location",
        "category": "Temporary Foster Services",
        "address": "1 Test St, Denver CO 80202",
        "lat": 39.7392,
        "lng": -104.9903,
    }
    created = []

    def post(url, payload, table, key):
        status, body = request("POST", url, payload)
        identifier = body.get(key)
        if is_uuid(identifier) and body.get("name") == payload["name"]:
            created.append((table, key, identifier, payload["name"]))
            return status, body, identifier
        return status, body, None

    try:
        status, body, partner_id = post(
            partners_url, partner, partners_table, "partnerId"
        )
        partner_created = check(
            "Create partner",
            status == 201 and partner_id is not None and bool(body.get("createdAt")),
            status,
            body,
        )
        if not partner_created:
            return 1
        results = [partner_created]

        status, body = request("GET", partners_url)
        results.append(
            check(
                "List the new partner",
                status == 200
                and any(
                    item.get("partnerId") == partner_id
                    for item in body.get("partners", [])
                ),
                status,
                body,
            )
        )

        status, body, resource_id = post(
            resources_url,
            {**resource, "partnerId": partner_id},
            resources_table,
            "resourceId",
        )
        results.append(
            check(
                "Create a resource linked to the new partner",
                status == 201
                and resource_id is not None
                and bool(body.get("createdAt"))
                and body.get("partnerId") == partner_id,
                status,
                body,
            )
        )

        status, body = request("GET", resources_url)
        results.append(
            check(
                "List the new resource with its coordinates",
                status == 200
                and resource_id is not None
                and any(
                    item.get("resourceId") == resource_id
                    and item.get("lat") == resource["lat"]
                    and item.get("lng") == resource["lng"]
                    for item in body.get("resources", [])
                ),
                status,
                body,
            )
        )
    finally:
        # Reverse creation order removes linked resources before partners.
        cleanup_errors = []
        for table, key, identifier, name in reversed(created):
            try:
                delete_test_record(table, key, identifier, name)
            except (BotoCoreError, ClientError, RuntimeError) as error:
                cleanup_errors.append(f"{key}={identifier}: {error}")
        if cleanup_errors:
            raise RuntimeError("Test cleanup incomplete: " + "; ".join(cleanup_errors))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


def is_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
        return True
    except ValueError:
        return False


def check_target_is_writable(env_name, environments, session):
    if env_name not in WRITABLE_ENVIRONMENTS:
        raise ValueError(
            f"Refusing to write to '{env_name}'; allowed: {sorted(WRITABLE_ENVIRONMENTS)}."
        )
    config = environments[env_name]
    account = session.client("sts").get_caller_identity()["Account"]
    if account != config["account"]:
        raise ValueError(
            f"Credentials are for account {account}, not {config['account']} ({env_name})."
        )
    for other_name, other in environments.items():
        if other_name not in WRITABLE_ENVIRONMENTS and all(
            other.get(key) == config[key]
            for key in ("account", "region", "project_name")
        ):
            raise ValueError(
                f"'{env_name}' shares a stack with disallowed environment '{other_name}'."
            )


def delete_test_record(table, key, identifier, name):
    if (
        key not in {"partnerId", "resourceId"}
        or not is_uuid(identifier)
        or not name.startswith("Post-Deployment Test ")
    ):
        raise ValueError("Refusing to delete a record without a valid test identity.")
    try:
        table.delete_item(
            Key={key: identifier}, ConditionExpression=Attr("name").eq(name)
        )
        print(f"Cleaned up {key}={identifier}")
    except table.meta.client.exceptions.ConditionalCheckFailedException as error:
        raise RuntimeError(
            f"Cleanup skipped: {key}={identifier} is missing or its name changed."
        ) from error


def stack_outputs(session, stack_name):
    stack = session.client("cloudformation").describe_stacks(StackName=stack_name)[
        "Stacks"
    ][0]
    return {
        output["OutputKey"]: output["OutputValue"]
        for output in stack.get("Outputs", [])
    }


def request(method, url, payload=None):
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
        parsed = json.loads(raw or b"{}")
        return status, parsed if isinstance(parsed, dict) else {"unexpected": parsed}
    except json.JSONDecodeError:
        return status, {"raw": raw.decode(errors="replace")}


def check(name, ok, status, body):
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"      status={status}, body={json.dumps(body)[:500]}")
    return bool(ok)


if __name__ == "__main__":
    sys.exit(main())
