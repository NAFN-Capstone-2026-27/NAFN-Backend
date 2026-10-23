"""GET /partners: return every partner in the table."""

import json
import os

import boto3

table = boto3.resource("dynamodb").Table(os.environ["PARTNERS_TABLE_NAME"])


def handler(event, context):
    partners = []
    scan_kwargs = {}
    # A Scan returns at most 1 MB per call, so follow the pages to the end.
    while True:
        page = table.scan(**scan_kwargs)
        partners.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            break
        scan_kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        # DynamoDB returns numbers as Decimal, which json can't serialize.
        "body": json.dumps({"partners": partners}, default=str),
    }
