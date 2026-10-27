import importlib
import json
import unittest
from decimal import Decimal


common = importlib.import_module("NAFNBackend.BackendInfrastructure.lambda.common")


class EinTests(unittest.TestCase):
    def test_optional_ein_is_omitted(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.assertIsNone(common.normalize_ein(value))

    def test_normalization_preserves_leading_zeros(self):
        self.assertEqual(common.normalize_ein(" 01-2345678 "), "012345678")
        self.assertEqual(common.normalize_ein("012345678"), "012345678")

    def test_malformed_or_non_string_eins_are_rejected(self):
        for value in (123456789, True, [], "12345678", "1234567890",
                      "abc123456789", "12--3456789", "１２３４５６７８９"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.normalize_ein(value)


class ChoiceTests(unittest.TestCase):
    def test_focus_areas_are_trimmed_and_deduplicated_in_order(self):
        self.assertEqual(
            common.validate_focus_areas([
                " Climate Emergency ", "Medical Emergency", "Climate Emergency", "",
            ]),
            ["Climate Emergency", "Medical Emergency"],
        )

    def test_focus_areas_require_nonempty_supported_strings(self):
        for value in (None, [], [" "], "Medical Emergency", [False], ["Other"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.validate_focus_areas(value)

    def test_all_agreed_focus_areas_are_accepted(self):
        self.assertEqual(common.validate_focus_areas([
            "Domestic Violence", "Rehabilitation", "Assisted Living",
            "Medical Emergency", "Climate Emergency",
        ]), list(common.FOCUS_AREAS))

    def test_all_seven_resource_categories_are_accepted(self):
        for category in (
            "Temporary Foster Services", "Emergency Vets", "Food Assistance",
            "Pet Supplies", "Wildlife", "Animal Control", "Other",
        ):
            with self.subTest(category=category):
                self.assertEqual(common.validate_category(f" {category} "), category)

    def test_unknown_or_missing_categories_are_rejected(self):
        for value in (None, "", "Unknown", "other", ["Other"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.validate_category(value)

    def test_optional_animal_list_needs_no_enum(self):
        self.assertEqual(
            common.normalize_string_list([" Cats ", "Dogs", "Cats", ""], "animalsAccepted"),
            ["Cats", "Dogs"],
        )
        for value in (None, [], [" "]):
            self.assertIsNone(common.normalize_string_list(value, "animalsAccepted"))
        for value in ("Dogs", ["Cats", 1], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.normalize_string_list(value, "animalsAccepted")


class CoordinateTests(unittest.TestCase):
    def test_coordinates_are_dynamodb_compatible_and_allow_zero(self):
        lat, lng = common.validate_coordinates(0, -104.9903)
        self.assertIsInstance(lat, Decimal)
        self.assertIsInstance(lng, Decimal)
        self.assertEqual((lat, lng), (Decimal("0"), Decimal("-104.9903")))

    def test_boundary_values_are_valid(self):
        self.assertEqual(common.validate_coordinates(-90, 180), (Decimal(-90), Decimal(180)))
        self.assertEqual(common.validate_coordinates(90, -180), (Decimal(90), Decimal(-180)))

    def test_invalid_latitude_or_longitude_is_rejected(self):
        for lat, lng in (
            (91, 0), (-91, 0), (0, 181), (0, -181),
            (None, 0), (0, None), ("39.7", 0), (True, 0), (0, False),
            (float("nan"), 0), (0, float("inf")),
            (Decimal("NaN"), 0), (0, Decimal("-Infinity")),
        ):
            with self.subTest(lat=lat, lng=lng), self.assertRaises(ValueError):
                common.validate_coordinates(lat, lng)


class ContactTests(unittest.TestCase):
    def test_text_required_and_optional_behavior(self):
        self.assertEqual(common.normalize_text(" Example ", "name"), "Example")
        self.assertIsNone(common.normalize_text(" ", "description", required=False))
        for value in (None, " ", 12):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.normalize_text(value, "name")

    def test_email_basic_validation_and_optional_values(self):
        self.assertEqual(common.normalize_email(" contact@example.org "), "contact@example.org")
        self.assertIsNone(common.normalize_email(None))
        for value in (" ", "no-domain", "a@@example.org", "a b@example.org", 10):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.normalize_email(value, required=True)

    def test_phone_normalization_preserves_leading_zero(self):
        self.assertEqual(common.normalize_phone("(012) 345-6789"), "0123456789")
        self.assertIsNone(common.normalize_phone(" "))
        for value in ("123", 1234567890, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.normalize_phone(value, required=True)

    def test_http_and_https_websites(self):
        for value in ("https://example.org/path", "http://example.org"):
            self.assertEqual(common.normalize_website(f" {value} "), value)
        self.assertIsNone(common.normalize_website(" "))
        for value in ("example.org", "javascript:alert(1)", "https://",
                      "https://bad host.org", "https://example.org:99999",
                      "https://user:password@example.org", "http://["):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.normalize_website(value)


class JsonTests(unittest.TestCase):
    def test_parse_preserves_decimal_coordinate_precision(self):
        payload = common.parse_json_body('{"lat":39.7392,"lng":-104.9903,"name":"Example"}')
        self.assertEqual(payload["lat"], Decimal("39.7392"))
        self.assertEqual(payload["lng"], Decimal("-104.9903"))

    def test_bad_json_and_non_object_bodies_are_rejected(self):
        for value in (None, "", " ", "{", "[]", "null", '"text"',
                      '{"lat":NaN}', '{"lng":Infinity}', '{"lat":-Infinity}'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.parse_json_body(value)

    def test_response_preserves_nested_json_numbers_and_types(self):
        response = common.json_response(201, {
            "resources": [{"lat": Decimal("39.7392"), "lng": Decimal("0")}],
            "partnerId": "uuid-string", "optional": None, "enabled": True,
        })
        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(response["headers"]["Content-Type"], "application/json")
        payload = json.loads(response["body"])
        self.assertEqual(payload["resources"][0], {"lat": 39.7392, "lng": 0})
        self.assertIsInstance(payload["resources"][0]["lat"], float)
        self.assertEqual(payload["partnerId"], "uuid-string")
        self.assertIsNone(payload["optional"])
        self.assertIs(payload["enabled"], True)

    def test_non_finite_response_numbers_are_rejected(self):
        for value in (float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                common.json_response(200, {"lat": value})

    def test_unsupported_objects_are_not_silently_converted_to_strings(self):
        with self.assertRaises(TypeError):
            common.json_response(200, {"unsupported": object()})


if __name__ == "__main__":
    unittest.main()
