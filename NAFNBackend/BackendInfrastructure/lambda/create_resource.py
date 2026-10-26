"""POST /resources: create a location/service, optionally linked to a partner."""

import base64
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import boto3
from boto3.dynamodb.conditions import Attr

from common import (
    json_response,
    normalize_email,
    normalize_phone,
    normalize_string_list,
    normalize_text,
    normalize_website,
    parse_json_body,
    validate_category,
    validate_coordinates,
)


dynamodb = boto3.resource("dynamodb")
resources_table = dynamodb.Table(os.environ["RESOURCES_TABLE_NAME"])
partners_table = dynamodb.Table(os.environ["PARTNERS_TABLE_NAME"])


def handler(event, context):
    try:
        body = event.get("body")
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body, validate=True).decode("utf-8")
        payload = parse_json_body(body)
    except (ValueError, TypeError, UnicodeError) as error:
        return json_response(400, {"error": str(error)})

    resource, errors = validate_resource(payload)
    if errors:
        return json_response(400, {"errors": errors})

    if "partnerId" in resource:
        partner = partners_table.get_item(
            Key={"partnerId": resource["partnerId"]},
            ConsistentRead=True,
            ProjectionExpression="partnerId",
        ).get("Item")
        if not partner:
            return json_response(400, {"errors": ["'partnerId' does not reference an existing partner."]})

    resource["resourceId"] = str(uuid4())
    resource["createdAt"] = datetime.now(timezone.utc).isoformat()
    try:
        resources_table.put_item(
            Item=resource,
            ConditionExpression=Attr("resourceId").not_exists(),
        )
    except resources_table.meta.client.exceptions.ConditionalCheckFailedException:
        return json_response(409, {"error": "Resource ID collision; please retry."})

    return json_response(201, resource)


def validate_resource(body):
    resource = {}
    errors = []
    validators = {
        "name": lambda value: normalize_text(value, "name"),
        "category": validate_category,
        "address": lambda value: normalize_text(value, "address"),
        "partnerId": lambda value: normalize_text(value, "partnerId", required=False),
        "phone": normalize_phone,
        "website": normalize_website,
        "email": normalize_email,
        "description": lambda value: normalize_text(value, "description", required=False),
        "animalsAccepted": lambda value: normalize_string_list(value, "animalsAccepted"),
        "timeLimits": lambda value: normalize_text(value, "timeLimits", required=False),
    }
    for field, validate in validators.items():
        try:
            value = validate(body.get(field))
            if value is not None:
                resource[field] = value
        except ValueError as error:
            errors.append(str(error))

    try:
        resource["lat"], resource["lng"] = validate_coordinates(body.get("lat"), body.get("lng"))
    except ValueError as error:
        errors.append(str(error))

    if "partnerId" in resource:
        try:
            resource["partnerId"] = str(UUID(resource["partnerId"]))
        except ValueError:
            errors.append("'partnerId' must be a UUID string.")

    return resource, errors
