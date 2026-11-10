"""POST /partners: validate and create a UUID-keyed organization record."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Attr
from common import (
    json_response,
    normalize_ein,
    normalize_email,
    normalize_phone,
    normalize_text,
    parse_json_body,
    validate_focus_areas,
)

table = boto3.resource("dynamodb").Table(os.environ["PARTNERS_TABLE_NAME"])


def handler(event, context):
    try:
        payload = parse_json_body(event.get("body"))
    except ValueError as error:
        return json_response(400, {"error": str(error)})

    partner, errors = validate_partner(payload)
    if errors:
        return json_response(400, {"errors": errors})

    # Concurrent requests can still insert the same EIN.
    if "ein" in partner and ein_exists(partner["ein"]):
        return json_response(409, {"error": "A partner with this EIN already exists."})

    partner["partnerId"] = str(uuid4())
    partner["createdAt"] = datetime.now(UTC).isoformat()
    table.put_item(Item=partner)

    return json_response(201, partner)


def ein_exists(ein):
    """Check every scan page; a filtered empty page can still have a next page."""
    paginator = table.meta.client.get_paginator("scan")
    pages = paginator.paginate(
        TableName=table.name,
        FilterExpression=Attr("ein").eq(ein),
        ProjectionExpression="partnerId",
        ConsistentRead=True,
    )
    return any(page.get("Items") for page in pages)


def validate_partner(body):
    partner = {}
    errors = []
    validators = {
        "name": lambda value: normalize_text(value, "name"),
        "address": lambda value: normalize_text(value, "address"),
        "email": lambda value: normalize_email(value, required=True),
        "phone": lambda value: normalize_phone(value, required=True),
        "ein": normalize_ein,
        "focusAreas": validate_focus_areas,
    }
    for field, validate in validators.items():
        try:
            value = validate(body.get(field))
            if value is not None:
                partner[field] = value
        except ValueError as error:
            errors.append(str(error))

    return partner, errors
