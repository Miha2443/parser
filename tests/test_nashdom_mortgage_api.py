"""Offline normalization of sanitized, observed public mortgage API responses."""
import copy
import json
import unittest

from pipeline.downloaders.nashdom_mortgage_api import (
    ENDPOINTS, METRICS, MortgageAPIError, match_request, normalize_month,
)


def exchanges(region="rf", period="2025-08"):
    request = {"regionId": {"rf": 1, "msk": 1762}[region],
               "reportingPeriod": period + "-01", "periodType": "month"}
    general = {
        "totalCreditCount": 85094, "totalCreditCountDeltaPct": -15.01,
        "primaryCreditCount": 38639, "primaryCreditCountDeltaPct": 33.52,
        "secondaryCreditCount": 46455, "secondaryCreditCountDeltaPct": -34.74,
        "totalCreditAmount": 3.92274e11, "totalCreditAmountDeltaPct": 4.59,
        "primaryCreditAmount": 2.22906e11, "primaryCreditAmountDeltaPct": 38.38,
        "secondaryCreditAmount": 1.69368e11, "secondaryCreditAmountDeltaPct": -20.85,
        "totalCreditAvgRate": 7.58, "totalCreditAvgRateDelta": -1.74,
        "primaryCreditAvgRate": 6.08, "primaryCreditAvgRateDelta": -0.27,
        "secondaryCreditAvgRate": 9.82, "secondaryCreditAvgRateDelta": -2.08,
        "yearCreditAmount": 4.888357e12, "yearCreditAmountDeltaPct": -37.16,
        "yearCreditAvgRatePrimary": 6.21, "yearCreditAvgRatePrimaryDelta": 0.28,
        "yearCreditAvgRateSecondary": 11.51, "yearCreditAvgRateSecondaryDelta": -1.44,
    }
    details = [
        {"currency": "RUB", "creditAmountAvg": 4609890.0, "creditAmountAvgDeltaPct": 23.06,
         "creditAvgPeriod": 25.583334, "creditAvgPeriodDeltaPct": 2.92,
         "creditDebtsAmount": 2.055127e13, "creditDebtsAmountDeltaPct": 3.58,
         "creditDebtsOverduePercent": 0.85, "creditDebtsOverduePercentDelta": 0.46},
        {"currency": "OTH", "creditAmountAvg": None, "creditAmountAvgDeltaPct": None,
         "creditAvgPeriod": None, "creditAvgPeriodDeltaPct": None,
         "creditDebtsAmount": 4.683e9, "creditDebtsAmountDeltaPct": -31.39,
         "creditDebtsOverduePercent": 43.19, "creditDebtsOverduePercentDelta": 3.32},
    ]
    return [
        {"url": ENDPOINTS["general"], "method": "POST", "status": 200,
         "request": {**request, "currency": "ALL"}, "body": {"data": general}},
        {"url": ENDPOINTS["details"], "method": "POST", "status": 200,
         "request": request, "body": {"data": details}},
    ]


class MatchRequestTests(unittest.TestCase):
    def test_both_observed_endpoints_and_regions(self):
        for region in ("rf", "msk"):
            for exchange in exchanges(region):
                with self.subTest(region=region, url=exchange["url"]):
                    self.assertEqual(match_request(exchange["url"], exchange["method"],
                        exchange["request"], region, "2025-08"),
                        exchange["url"].rsplit("/", 1)[1])

    def test_json_request_and_lowercase_method(self):
        item = exchanges()[0]
        self.assertEqual(match_request(item["url"], "post", json.dumps(item["request"]),
                                       "rf", "2025-08"), "general")

    def test_wrong_url_is_not_a_match(self):
        item = exchanges()[0]
        urls = [None, 7, item["url"].replace("https:", "http:"),
                item["url"].replace("xn--80az8a.", "other."), item["url"] + "?x=1",
                item["url"] + "#fragment", item["url"] + "/",
                item["url"].replace("https://", "https://user@"),
                item["url"].replace("/portal-analytics", ":443/portal-analytics"),
                "/portal-analytics/api/mortgage/dashboard/general"]
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(match_request(url, "POST", item["request"], "rf", "2025-08"))

    def test_wrong_identity_basis_currency_and_extra_filters(self):
        for endpoint, item in zip(("general", "details"), exchanges()):
            changes = [{"regionId": 1762}, {"regionId": True}, {"regionId": 1.0},
                       {"regionId": "1"}, {"reportingPeriod": "2025-07-01"},
                       {"reportingPeriod": "2025-08-02"}, {"periodType": "year"},
                       {"periodType": None}, {"market": "primary"}, {"housing": "private"}]
            changes += [{"currency": "RUB"}, {"currency": "OTH"}, {"currency": None}]
            if endpoint == "details":
                changes.append({"currency": "ALL"})
            for change in changes:
                with self.subTest(endpoint=endpoint, change=change):
                    self.assertIsNone(match_request(item["url"], "POST",
                        {**item["request"], **change}, "rf", "2025-08"))
            for key in item["request"]:
                request = dict(item["request"])
                del request[key]
                self.assertIsNone(match_request(item["url"], "POST", request, "rf", "2025-08"))

    def test_bad_scope_method_and_payload_return_none(self):
        item = exchanges()[0]
        for region, period in [("other", "2025-08"), (None, "2025-08"), ([], "2025-08"),
                               ("rf", "2025-13"), ("rf", "2025-8"), ("rf", "0000-01"),
                               ("rf", None), ("rf", "2025-08-01")]:
            self.assertIsNone(match_request(item["url"], "POST", item["request"], region, period))
        for method in (None, 1, "GET", " POST"):
            self.assertIsNone(match_request(item["url"], method, item["request"], "rf", "2025-08"))
        for payload in (None, [], 7, "not JSON", "[]", '{"regionId":1,"regionId":1}',
                        '{"regionId":NaN}'):
            self.assertIsNone(match_request(item["url"], "POST", payload, "rf", "2025-08"))


class NormalizeMonthTests(unittest.TestCase):
    def test_observed_month_is_not_ytd_or_previous_year_summary(self):
        result = normalize_month("rf", "2025-08", exchanges())
        self.assertEqual((result["region"], result["period"], result["basis"]),
                         ("rf", "2025-08", "month"))
        self.assertEqual(result["sourcePrecision"], "raw response")
        self.assertEqual(result["metrics"], {
            "all": {"volumeRub": 392274000000, "count": 85094, "ratePct": 7.58,
                    "termMonths": 307.000008, "averageAmountRub": 4609890},
            "primary": {"volumeRub": 222906000000, "count": 38639, "ratePct": 6.08,
                        "termMonths": None, "averageAmountRub": None},
            "secondary": {"volumeRub": 169368000000, "count": 46455, "ratePct": 9.82,
                          "termMonths": None, "averageAmountRub": None},
        })

    def test_pair_order_and_moscow_scope(self):
        items = exchanges("msk", "2009-01")
        result = normalize_month("msk", "2009-01", list(reversed(items)))
        self.assertEqual(result["region"], "msk")
        self.assertEqual(result["period"], "2009-01")
        self.assertEqual(result["metrics"]["all"]["count"], 85094)

    def test_json_response_bodies_and_requests_supported(self):
        items = exchanges()
        for item in items:
            item["body"] = json.dumps(item["body"])
            item["request"] = json.dumps(item["request"])
        result = normalize_month("rf", "2025-08", items)
        self.assertEqual(result["metrics"]["all"]["count"], 85094)
        self.assertEqual(result["rawMetrics"]["exchanges"], items)

    def test_raw_evidence_preserves_unplotted_data_and_is_detached(self):
        items = exchanges()
        items[0]["body"]["data"]["futureField"] = {"value": -1, "enabled": True}
        original = copy.deepcopy(items)
        result = normalize_month("rf", "2025-08", items)
        self.assertEqual(result["rawMetrics"]["exchanges"], original)
        items[0]["body"]["data"]["totalCreditCount"] = 0
        self.assertEqual(result["rawMetrics"]["exchanges"], original)
        result["rawMetrics"]["exchanges"][1]["body"]["data"][0]["creditAmountAvg"] = 0
        self.assertEqual(items[1]["body"]["data"][0]["creditAmountAvg"], 4609890)
        self.assertEqual(result["metrics"]["all"]["averageAmountRub"], 4609890)
        self.assertEqual(set(result["metrics"]["all"]), set(METRICS))

    def test_transport_credentials_are_never_persisted(self):
        items = exchanges()
        items[0].update(headers={"Cookie": "private", "Authorization": "Bearer private"},
                        cookies=["private"], sessionId="private")
        result = normalize_month("rf", "2025-08", items)
        evidence = result["rawMetrics"]["exchanges"]
        self.assertEqual(set(evidence[0]), {"url", "method", "status", "request", "body"})
        self.assertNotIn("private", json.dumps(evidence))

    def test_nulls_and_zero_are_preserved_without_inference(self):
        items = exchanges()
        for prefix in ("total", "primary", "secondary"):
            for suffix in ("CreditAmount", "CreditCount", "CreditAvgRate"):
                items[0]["body"]["data"][prefix + suffix] = None
        for key in ("creditAvgPeriod", "creditAmountAvg"):
            items[1]["body"]["data"][0][key] = None
        result = normalize_month("rf", "2025-08", items)
        self.assertTrue(all(value is None for metrics in result["metrics"].values()
                            for value in metrics.values()))
        for prefix in ("total", "primary", "secondary"):
            for suffix in ("CreditAmount", "CreditCount", "CreditAvgRate"):
                items[0]["body"]["data"][prefix + suffix] = 0
        for key in ("creditAvgPeriod", "creditAmountAvg"):
            items[1]["body"]["data"][0][key] = 0
        result = normalize_month("rf", "2025-08", items)
        self.assertTrue(all(value == 0 for value in result["metrics"]["all"].values()))
        self.assertIsNone(result["metrics"]["primary"]["termMonths"])

    def test_segments_and_rates_are_not_reconstructed(self):
        items = exchanges()
        items[0]["body"]["data"].update({"primaryCreditAmount": None,
            "secondaryCreditCount": None, "totalCreditAvgRate": None})
        result = normalize_month("rf", "2025-08", items)
        self.assertIsNone(result["metrics"]["primary"]["volumeRub"])
        self.assertIsNone(result["metrics"]["secondary"]["count"])
        self.assertIsNone(result["metrics"]["all"]["ratePct"])

    def test_term_conversion_uses_decimal_not_binary_multiplication(self):
        for years, months in [(24.2, 290.4), (25.583334, 307.000008), (20, 240),
                              (0.1, 1.2), (None, None)]:
            with self.subTest(years=years):
                items = exchanges()
                items[1]["body"]["data"][0]["creditAvgPeriod"] = years
                result = normalize_month("rf", "2025-08", items)
                self.assertEqual(result["metrics"]["all"]["termMonths"], months)

    def test_overflowing_input_and_converted_numbers_rejected(self):
        items = exchanges()
        items[0]["body"]["data"]["totalCreditAmount"] = 10 ** 400
        with self.assertRaises(MortgageAPIError):
            normalize_month("rf", "2025-08", items)
        items = exchanges()
        items[1]["body"]["data"][0]["creditAvgPeriod"] = 1e308
        with self.assertRaises(MortgageAPIError):
            normalize_month("rf", "2025-08", items)

    def test_foreign_currency_is_never_substituted_for_rubles(self):
        items = exchanges()
        items[1]["body"]["data"][0].update(creditAmountAvg=None, creditAvgPeriod=None)
        items[1]["body"]["data"][1].update(creditAmountAvg=123, creditAvgPeriod=10)
        result = normalize_month("rf", "2025-08", items)
        self.assertIsNone(result["metrics"]["all"]["termMonths"])
        self.assertIsNone(result["metrics"]["all"]["averageAmountRub"])

    def test_missing_core_general_fields_rejected(self):
        for prefix in ("total", "primary", "secondary"):
            for suffix in ("CreditAmount", "CreditCount", "CreditAvgRate"):
                key = prefix + suffix
                with self.subTest(key=key):
                    items = exchanges()
                    del items[0]["body"]["data"][key]
                    with self.assertRaises(MortgageAPIError):
                        normalize_month("rf", "2025-08", items)

    def test_missing_core_detail_fields_rejected(self):
        for key in ("creditAmountAvg", "creditAvgPeriod"):
            items = exchanges()
            del items[1]["body"]["data"][0][key]
            with self.assertRaises(MortgageAPIError):
                normalize_month("rf", "2025-08", items)

    def test_invalid_canonical_measurements_rejected(self):
        general_keys = [prefix + suffix for prefix in ("total", "primary", "secondary")
                        for suffix in ("CreditAmount", "CreditCount", "CreditAvgRate")]
        for index, key in [(0, key) for key in general_keys] + [
                (1, "creditAmountAvg"), (1, "creditAvgPeriod")]:
            for value in (True, False, -1, "12", [], {}, float("nan"), float("inf"), -float("inf")):
                with self.subTest(key=key, value=value):
                    items = exchanges()
                    data = items[index]["body"]["data"]
                    (data if index == 0 else data[0])[key] = value
                    with self.assertRaises(MortgageAPIError):
                        normalize_month("rf", "2025-08", items)

    def test_incomplete_duplicate_and_extra_pairs_rejected(self):
        items = exchanges()
        for pair in (None, {}, [], items[:1], items[1:], [items[0], items[0]],
                     items + [items[0]], [None, items[1]]):
            with self.subTest(pair=pair):
                with self.assertRaises(MortgageAPIError):
                    normalize_month("rf", "2025-08", pair)

    def test_failed_status_and_wrong_request_not_accepted(self):
        for index in (0, 1):
            for status in (None, True, 200.0, "200", 201, 403, 500):
                items = exchanges()
                items[index]["status"] = status
                with self.assertRaises(MortgageAPIError):
                    normalize_month("rf", "2025-08", items)
            items = exchanges()
            items[index]["request"]["periodType"] = "year"
            with self.assertRaises(MortgageAPIError):
                normalize_month("rf", "2025-08", items)
        with self.assertRaises(MortgageAPIError):
            normalize_month("msk", "2025-08", exchanges())
        with self.assertRaises(MortgageAPIError):
            normalize_month("rf", "2025-07", exchanges())

    def test_bad_body_and_data_shapes_rejected(self):
        for index in (0, 1):
            for body in (None, [], "<html>blocked</html>", "null", "{}", {"error": "blocked"},
                         {"data": None}, '{"data":{},"data":{}}', '{"data":NaN}'):
                items = exchanges()
                items[index]["body"] = body
                with self.assertRaises(MortgageAPIError):
                    normalize_month("rf", "2025-08", items)
        for general in ([], True, 1, "value"):
            items = exchanges()
            items[0]["body"]["data"] = general
            with self.assertRaises(MortgageAPIError):
                normalize_month("rf", "2025-08", items)
        for details in ({}, True, 1, "value", [None], [1]):
            items = exchanges()
            items[1]["body"]["data"] = details
            with self.assertRaises(MortgageAPIError):
                normalize_month("rf", "2025-08", items)

    def test_explicit_details_server_error_keeps_general_without_inventing_parameters(self):
        items = exchanges()
        items[1]["status"] = 500
        items[1]["body"] = {"status": "INTERNAL_SERVER_ERROR", "code": "500",
                             "title": "Internal Server Error", "message": "Source unavailable"}
        result = normalize_month("rf", "2025-08", items)
        self.assertEqual(result["metrics"]["all"]["volumeRub"], 392274000000)
        self.assertIsNone(result["metrics"]["all"]["termMonths"])
        self.assertIsNone(result["metrics"]["all"]["averageAmountRub"])
        self.assertEqual(result["rawMetrics"]["exchanges"][1], items[1])
        for status in (200, 403, 502):
            items[1]["status"] = status
            with self.assertRaises(MortgageAPIError):
                normalize_month("rf", "2025-08", items)

    def test_missing_and_ambiguous_ruble_details_rejected(self):
        items = exchanges()
        rows = items[1]["body"]["data"]
        for details in ([], rows[1:], [rows[0], rows[0]], [{**rows[0], "currency": "rub"}]):
            modified = exchanges()
            modified[1]["body"]["data"] = details
            with self.assertRaises(MortgageAPIError):
                normalize_month("rf", "2025-08", modified)

    def test_non_json_or_nonfinite_extra_evidence_rejected(self):
        for value in (object(), {1: "not a JSON key"}, float("nan"), float("inf")):
            items = exchanges()
            items[0]["body"]["extra"] = value
            with self.assertRaises(MortgageAPIError):
                normalize_month("rf", "2025-08", items)

    def test_invalid_normalization_scope_rejected(self):
        for region, period in [("unknown", "2025-08"), ("rf", "2025-00"), ("rf", "0001-01"),
                               ("rf", "2025-08-01"), ("rf", "2025-8")]:
            with self.assertRaises(MortgageAPIError):
                normalize_month(region, period, exchanges())

    def test_observation_is_compatible_with_existing_year_bundle(self):
        from pipeline.downloaders.nashdom_mortgage import year_bundle
        observation = normalize_month("rf", "2025-08", exchanges())
        bundle = year_bundle("rf", 2025, [(8, observation)])
        self.assertEqual(len(bundle["records"]), 3)
        row = next(row for row in bundle["records"] if row["segment"] == "all")
        self.assertEqual(row["volumeRub"], 392274000000)
        self.assertEqual(row["termMonths"], 307.000008)
        self.assertEqual(row["sourceEvidence"], observation["rawMetrics"])
        json.dumps(bundle, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
