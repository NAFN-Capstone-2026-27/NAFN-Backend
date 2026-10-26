"""GET /partners: list UUID-keyed organizations, including partners without EINs."""

import os

import boto3

from common import json_response

table = boto3.resource("dynamodb").Table(os.environ["PARTNERS_TABLE_NAME"])


def handler(event, context):
    partners = []
    paginator = table.meta.client.get_paginator("scan")
    for page in paginator.paginate(TableName=table.name, ConsistentRead=True):
        partners.extend(page.get("Items", []))
    return json_response(200, {"partners": partners})
