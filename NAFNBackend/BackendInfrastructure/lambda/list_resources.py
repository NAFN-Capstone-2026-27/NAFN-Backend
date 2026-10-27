"""GET /resources: list locations/services with numeric coordinates."""

import os

import boto3

from common import json_response

table = boto3.resource("dynamodb").Table(os.environ["RESOURCES_TABLE_NAME"])


def handler(event, context):
    resources = []
    paginator = table.meta.client.get_paginator("scan")
    for page in paginator.paginate(TableName=table.name, ConsistentRead=True):
        resources.extend(page.get("Items", []))
    return json_response(200, {"resources": resources})
