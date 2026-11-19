"""POST /partners: validate and create a UUID-keyed organization record."""

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError, ConnectionError, HTTPClientError
from common import (
    json_response,
    normalize_ein,
    normalize_email,
    normalize_phone,
    normalize_text,
    parse_json_body,
    validate_focus_areas,
)

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["PARTNERS_TABLE_NAME"])
reservations = dynamodb.Table(os.environ["PARTNER_EIN_RESERVATIONS_TABLE_NAME"])


def handler(event, context):
    try:
        payload = parse_json_body(event.get("body"))
    except ValueError as error:
        return json_response(400, {"error": str(error)})

    partner, errors = validate_partner(payload)
    if errors:
        return json_response(400, {"errors": errors})

    # Partners without an EIN can be created directly because no reservation is needed.
    if "ein" not in partner:
        partner["partnerId"] = str(uuid4())
        partner["createdAt"] = datetime.now(UTC).isoformat()
        table.put_item(Item=partner)
        return json_response(201, partner)

    headers = event.get("headers") or {}
    request_id = next(
        (value for name, value in headers.items() if name.lower() == "idempotency-key"),
        "",
    )
    try:
        request_uuid = UUID(request_id)
        if request_uuid.version != 4:
            raise ValueError
    except ValueError:
        return json_response(
            400, {"error": "A UUIDv4 Idempotency-Key header is required with an EIN."}
        )

    request_id = str(request_uuid)
    payload_hash = sha256(json.dumps(partner, sort_keys=True).encode()).hexdigest()
    reservation = reserve_ein(partner["ein"], request_id, payload_hash)
    if reservation.get("requestId") != request_id:
        return json_response(409, {"error": "A partner with this EIN already exists."})
    if reservation["payloadHash"] != payload_hash:
        return json_response(
            409,
            {
                "error": "This Idempotency-Key was already used with different data for this EIN."
            },
        )

    partner["partnerId"] = reservation["partnerId"]
    partner["createdAt"] = datetime.now(UTC).isoformat()
    saved = put_if_absent(table, partner, "partnerId")
    if saved != {**partner, "createdAt": saved.get("createdAt")}:
        raise RuntimeError("Reserved partner ID has an unexpected record.")
    return json_response(201, saved)


def reserve_ein(ein, request_id, payload_hash):
    key = {"ein": ein}
    existing = reservations.get_item(Key=key, ConsistentRead=True).get("Item")
    if existing is not None:
        return existing

    return put_if_absent(
        reservations,
        {
            "ein": ein,
            "requestId": request_id,
            "partnerId": str(uuid4()),
            "payloadHash": payload_hash,
        },
        "ein",
    )


def put_if_absent(target, item, key_name):
    try:
        target.put_item(Item=item, ConditionExpression=Attr(key_name).not_exists())
    except (ClientError, ConnectionError, HTTPClientError):
        # A competing write or a lost response can leave an item at this key.
        # The caller checks whether it can use that item before proceeding.
        existing = target.get_item(
            Key={key_name: item[key_name]}, ConsistentRead=True
        ).get("Item")
        if existing is None:
            raise
        return existing
    return item


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
