#!/usr/bin/env python3
"""Smoke-test the deployed partner/resource API, then delete this run's test records.

Usage: python post_deployment_tests.py --env dev --profile default
This script writes to AWS. Only explicitly allowed environments may be tested.
"""

import argparse
import json
import random
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
    outputs = stack_outputs(session, f"{config['project_name']}-Backend")
    required = {"ApiUrl", "PartnersTableName", "ResourcesTableName"}
    if not required.issubset(outputs):
        raise ValueError("Deploy the new backend first: required table/API outputs are missing.")
    base_url = outputs["ApiUrl"].rstrip("/")
    if not re.fullmatch(rf"https://[a-z0-9]+\.execute-api\.{re.escape(config['region'])}\.amazonaws\.com", base_url):
        raise ValueError("ApiUrl must be this region's generated API Gateway endpoint.")
    db = session.resource("dynamodb")
    partners_table = db.Table(outputs["PartnersTableName"])
    resources_table = db.Table(outputs["ResourcesTableName"])
    partners_url = f"{base_url}/partners"
    resources_url = f"{base_url}/resources"
    run_id = str(uuid4())
    prefix = f"Post-Deployment Test {run_id}"
    # A synthetic EIN beginning with 00 avoids using a real organization's EIN.
    ein = f"00{random.randint(0, 9_999_999):07d}"
    partner = {
        "name": f"{prefix} Organization",
        "address": "1 Test St, Denver CO 80202",
        "email": "post-deployment-test@example.com",
        "phone": "(303) 555-0100",
        "ein": f"{ein[:2]}-{ein[2:]}",
        "focusAreas": ["Domestic Violence", "Climate Emergency"],
    }
    resource = {
        "name": f"{prefix} Location", "category": "Shelters & Rescues",
        "address": "1 Test St, Denver CO 80202", "lat": 39.7392, "lng": -104.9903,
        "animalsAccepted": ["Dogs", "Cats"], "timeLimits": "Up to 30 days",
    }
    created = []
    results = []

    def post(url, payload, table, key):
        status, body = request("POST", url, payload)
        # Also clean up records created by unexpectedly successful requests.
        identifier = body.get(key)
        if is_uuid(identifier) and body.get("name") == payload.get("name"):
            created.append((table, key, identifier, payload["name"]))
        return status, body

    try:
        status, body = post(partners_url, partner, partners_table, "partnerId")
        partner_id = body.get("partnerId")
        results.append(check("Create partner with normalized EIN and generated UUID/time",
            status == 201 and is_uuid(partner_id) and bool(body.get("createdAt"))
            and body.get("ein") == ein and body.get("phone") == "3035550100", status, body))

        status, body = post(partners_url, partner, partners_table, "partnerId")
        results.append(check("Duplicate normalized EIN returns 409", status == 409, status, body))

        for label, ein_value in (("omitted", None), ("blank", " ")):
            payload = {**partner, "name": f"{prefix} EIN {label}"}
            if ein_value is None:
                del payload["ein"]
            else:
                payload["ein"] = ein_value
            status, body = post(partners_url, payload, partners_table, "partnerId")
            results.append(check(f"Optional EIN {label}: partner created without EIN attribute",
                status == 201 and is_uuid(body.get("partnerId")) and "ein" not in body, status, body))

        status, body = post(partners_url, {"name": f"{prefix} Invalid"}, partners_table, "partnerId")
        results.append(check("Missing partner fields return 400", status == 400 and bool(body.get("errors")), status, body))

        status, body = request("GET", partners_url)
        expected_ids = {item[2] for item in created if item[1] == "partnerId"}
        listed_ids = {item.get("partnerId") for item in body.get("partners", [])}
        results.append(check("Partner listing contains this run's created partners",
            status == 200 and bool(expected_ids) and expected_ids.issubset(listed_ids), status, body))

        if is_uuid(partner_id):
            status, body = post(resources_url, {**resource, "partnerId": partner_id}, resources_table, "resourceId")
            results.append(check("Create linked resource with numeric coordinates",
                status == 201 and is_uuid(body.get("resourceId")) and bool(body.get("createdAt"))
                and body.get("partnerId") == partner_id and coordinates_are_numbers(body)
                and body.get("lat") == resource["lat"] and body.get("lng") == resource["lng"], status, body))
        else:
            results.append(check("Create linked resource", False, 0, {"error": "Partner creation failed"}))

        status, body = post(resources_url, {**resource, "name": f"{prefix} Independent", "lat": 0, "lng": 0}, resources_table, "resourceId")
        results.append(check("Create independent resource with zero coordinates",
            status == 201 and is_uuid(body.get("resourceId")) and "partnerId" not in body
            and coordinates_are_numbers(body) and body.get("lat") == 0 and body.get("lng") == 0, status, body))

        for label, override in (
            ("invalid category", {"category": "Unknown"}),
            ("out-of-range coordinates", {"lat": 91}),
            ("nonexistent partner", {"partnerId": str(uuid4())}),
        ):
            payload = {**resource, "name": f"{prefix} {label}", **override}
            status, body = post(resources_url, payload, resources_table, "resourceId")
            results.append(check(f"Resource {label} returns 400", status == 400 and bool(body.get("errors")), status, body))

        status, body = request("GET", resources_url)
        expected_ids = {item[2] for item in created if item[1] == "resourceId"}
        listed = {item.get("resourceId"): item for item in body.get("resources", [])}
        results.append(check("Resource listing includes created records with numeric coordinates",
            status == 200 and bool(expected_ids) and expected_ids.issubset(listed)
            and all(coordinates_are_numbers(listed[key]) for key in expected_ids), status, body))
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


def coordinates_are_numbers(item):
    return all(isinstance(item.get(key), (int, float)) and not isinstance(item[key], bool) for key in ("lat", "lng"))


def check_target_is_writable(env_name, environments, session):
    if env_name not in WRITABLE_ENVIRONMENTS:
        raise ValueError(f"Refusing to write to '{env_name}'; allowed: {sorted(WRITABLE_ENVIRONMENTS)}.")
    config = environments[env_name]
    account = session.client("sts").get_caller_identity()["Account"]
    if account != config["account"]:
        raise ValueError(f"Credentials are for account {account}, not {config['account']} ({env_name}).")
    if session.region_name != config["region"]:
        raise ValueError("AWS session region does not match the configured environment.")
    for other_name, other in environments.items():
        if other_name not in WRITABLE_ENVIRONMENTS and all(
            other.get(key) == config[key] for key in ("account", "region", "project_name")
        ):
            raise ValueError(f"'{env_name}' shares a stack with disallowed environment '{other_name}'.")


def delete_test_record(table, key, identifier, name):
    if key not in {"partnerId", "resourceId"} or not is_uuid(identifier) or not name.startswith("Post-Deployment Test "):
        raise ValueError("Refusing to delete a record without a valid test identity.")
    try:
        table.delete_item(Key={key: identifier}, ConditionExpression=Attr("name").eq(name))
        print(f"Cleaned up {key}={identifier}")
    except table.meta.client.exceptions.ConditionalCheckFailedException as error:
        raise RuntimeError(f"Cleanup skipped: {key}={identifier} is missing or its name changed.") from error


def stack_outputs(session, stack_name):
    stack = session.client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"][0]
    return {output["OutputKey"]: output["OutputValue"] for output in stack.get("Outputs", [])}


def request(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
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
