"""POST /partners: validate a partner organization and store it in DynamoDB."""

import json
import os
import re
from datetime import UTC, datetime

import boto3

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

table = boto3.resource("dynamodb").Table(os.environ["PARTNERS_TABLE_NAME"])


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "")
    except json.JSONDecodeError:
        return _response(400, {"error": "Request body must be valid JSON."})
    if not isinstance(body, dict):
        return _response(400, {"error": "Request body must be a JSON object."})

    partner, errors = validate_partner(body)
    if errors:
        return _response(400, {"errors": errors})

    partner["createdAt"] = datetime.now(UTC).isoformat()
    try:
        table.put_item(
            Item=partner,
            ConditionExpression="attribute_not_exists(ein)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return _response(409, {"error": "A partner with this EIN already exists."})

    return _response(201, partner)


def validate_partner(body):
    """Return (partner, errors). partner holds only the known, cleaned fields.

    Every problem is collected rather than stopping at the first, so a form can
    show the submitter everything to fix in one round trip.
    """
    errors = []
    # Built field by field from what we recognize, so unexpected keys in the
    # request never get written to the table.
    partner = {}

    # Required fields. A field that's absent, null, blank or empty counts as
    # missing; this is checked before format so the message says "required"
    # rather than describing a format problem with a value that isn't there.
    missing = [
        field
        for field in ("name", "address", "email", "phone", "ein", "focusAreas")
        if _is_blank(body.get(field))
    ]
    errors.extend(f"'{field}' is required." for field in missing)

    # Ensure name, address, email, and phone # are strings and add them to the partner item
    for field in ("name", "address", "email", "phone"):
        if field in missing:
            continue
        value = body[field]
        if not isinstance(value, str):
            errors.append(f"'{field}' must be a string.")
        else:
            partner[field] = value.strip()

    # A loose shape check (something@domain.tld) to catch typos. Proving the
    # address actually receives mail would take a confirmation email.
    if "email" in partner and not EMAIL_PATTERN.match(partner["email"]):
        errors.append("'email' is not a valid email address.")

    # Accept any formatting ("(303) 555-1234", "303.555.1234", "+1 ...") but
    # store only the digits, so the same number always looks the same. It stays
    # a string rather than a number so a leading 0 is never lost. Only require
    # enough digits for a full US number.
    if "phone" in partner:
        partner["phone"] = re.sub(r"\D", "", partner["phone"])
        if len(partner["phone"]) < 10:
            errors.append("'phone' must contain at least 10 digits.")

    # EINs are 9 digits. Submitters write them with or without the dash, so
    # strip everything else and store just the digits. It's kept as a string
    # because EINs can start with 0 (e.g. 01-2345678), which a number would
    # drop. EIN is the table key, so one canonical form is also what makes the
    # duplicate check reliable.
    if "ein" not in missing:
        ein_digits = re.sub(r"\D", "", str(body["ein"]))
        if len(ein_digits) != 9:
            errors.append("'ein' must be 9 digits (e.g. 12-3456789).")
        else:
            partner["ein"] = ein_digits

    # A partner can serve several focus areas. Any values are accepted, so new
    # categories don't need a code change; the form decides what's offered.
    if "focusAreas" not in missing:
        focus_areas = _string_list(body["focusAreas"])
        if focus_areas is None:
            errors.append("'focusAreas' must be a list of strings.")
        else:
            partner["focusAreas"] = focus_areas

    # Optional fields: stored when given, left out of the item when not. A value
    # of the wrong type is still an error, rather than being silently dropped.
    animals = body.get("animalsAccepted")
    if not _is_blank(animals):
        animals = _string_list(animals)
        if animals is None:
            errors.append("'animalsAccepted' must be a list of strings.")
        else:
            partner["animalsAccepted"] = animals

    time_limits = body.get("timeLimits")
    if not _is_blank(time_limits):
        if not isinstance(time_limits, str):
            errors.append("'timeLimits' must be a string.")
        else:
            partner["timeLimits"] = time_limits.strip()

    return partner, errors


def _is_blank(value):
    """True for values that should count as "not provided"."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return all(isinstance(v, str) and not v.strip() for v in value)
    return False


def _string_list(value):
    """Return a de-duplicated list of trimmed strings, or None if value isn't one.

    Blank entries are dropped; any non-string entry makes the whole list invalid.
    """
    # Ensure the function was provided a list of strings
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return None

    # De-duplicate values in the list and strip any white space
    cleaned = {v.strip() for v in value if v.strip()}
    # Convert back to a list to avoid funkyness with how DDB serializing sets
    return list(cleaned)


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
