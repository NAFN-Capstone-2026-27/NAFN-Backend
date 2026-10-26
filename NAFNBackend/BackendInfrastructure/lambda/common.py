"""Validators raise ValueError; optional blank values normalize to None."""

import json
import math
import re
from decimal import Decimal
from urllib.parse import urlsplit


FOCUS_AREAS = (
    "Domestic Violence",
    "Rehabilitation",
    "Assisted Living",
    "Medical Emergency",
    "Climate Emergency",
)

RESOURCE_CATEGORIES = (
    "Shelters & Rescues",
    "Emergency Vets",
    "Food Assistance",
    "Pet Supplies",
    "Wildlife",
    "Animal Control",
    "Other",
)


def normalize_text(value, field, *, required=True):
    if value is None:
        if required:
            raise ValueError(f"'{field}' is required.")
        return None
    if not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string.")
    cleaned = value.strip()
    if not cleaned:
        if required:
            raise ValueError(f"'{field}' is required.")
        return None
    return cleaned


def normalize_ein(value):
    value = normalize_text(value, "ein", required=False)
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]{2}-?[0-9]{7}", value):
        raise ValueError("'ein' must be 9 digits (e.g. 12-3456789).")
    return value.replace("-", "")


def normalize_string_list(value, field, *, required=False):
    if value is None:
        if required:
            raise ValueError(f"'{field}' is required.")
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"'{field}' must be a list of strings.")
    cleaned = list(dict.fromkeys(v.strip() for v in value if v.strip()))
    if not cleaned:
        if required:
            raise ValueError(f"'{field}' must contain at least one value.")
        return None
    return cleaned


def validate_focus_areas(value):
    cleaned = normalize_string_list(value, "focusAreas", required=True)
    if any(area not in FOCUS_AREAS for area in cleaned):
        raise ValueError("'focusAreas' contains an unsupported value.")
    return cleaned


def validate_category(value):
    cleaned = normalize_text(value, "category")
    if cleaned not in RESOURCE_CATEGORIES:
        raise ValueError("'category' must be one of the supported resource categories.")
    return cleaned


def validate_coordinates(lat, lng):
    return (
        _coordinate(lat, "lat", -90, 90),
        _coordinate(lng, "lng", -180, 180),
    )


def _coordinate(value, field, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"'{field}' must be a number.")
    number = Decimal(str(value))
    if not number.is_finite() or not minimum <= number <= maximum:
        raise ValueError(f"'{field}' must be between {minimum} and {maximum}.")
    return number


def normalize_email(value, *, required=False):
    cleaned = normalize_text(value, "email", required=required)
    if cleaned is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned):
        raise ValueError("'email' is not a valid email address.")
    return cleaned


def normalize_phone(value, *, required=False):
    cleaned = normalize_text(value, "phone", required=required)
    if cleaned is None:
        return None
    digits = re.sub(r"[^0-9]", "", cleaned)
    if len(digits) < 10:
        raise ValueError("'phone' must contain at least 10 digits.")
    return digits


def normalize_website(value):
    cleaned = normalize_text(value, "website", required=False)
    if cleaned is None:
        return None
    try:
        parsed = urlsplit(cleaned)
        valid = (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not any(char.isspace() for char in cleaned)
        )
        # Accessing port also validates malformed or out-of-range port numbers.
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("'website' must be an HTTP or HTTPS URL with a host.")
    return cleaned


def parse_json_body(body):
    """Parse a JSON object with Decimal numbers for DynamoDB."""
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Request body must be valid JSON.")
    try:
        payload = json.loads(body, parse_float=Decimal, parse_constant=_invalid_constant)
    except (ValueError, ArithmeticError) as error:
        raise ValueError("Request body must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    return payload


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


def _json_number(value):
    if not isinstance(value, Decimal):
        raise TypeError(f"Unsupported JSON type: {type(value).__name__}")
    if not value.is_finite():
        raise ValueError("Cannot serialize a non-finite number.")
    if value == value.to_integral_value():
        return int(value)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Number is outside the supported JSON range.")
    return number


def json_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=_json_number, allow_nan=False),
    }
