"""The public mortgage DOM establishes scope and basis before exposing values."""
import copy
import json
from pathlib import Path
import unittest

from bs4 import BeautifulSoup

from pipeline.downloaders.nashdom_mortgage_dom import (
    AccessVerificationRequired, MortgageDOMError, discover_page, parse_page,
)


FIXTURE = Path(__file__).parent / "fixtures" / "mortgage_page.html"


def soup():
    return BeautifulSoup(FIXTURE.read_text(encoding="utf-8"), "html.parser")


def section(document, caption, *, main=False):
    root = document.select_one(".main-filters" if main else ".filters-panel__content")
    return next(item.parent for item in root.select(".lbl-def")
                if item.get_text(" ", strip=True) == caption)


def choose(control, value):
    radio = control.find("input", attrs={"value": value})
    for item in control.select("input[checked]"):
        del item["checked"]
    active = control.select("label.active")
    for label in active:
        label["class"] = [name for name in label.get("class", []) if name != "active"]
    radio["checked"] = ""
    if active:
        label = radio.find_parent("label")
        label["class"] = [*label.get("class", []), "active"]
    button = control.select_one(".filter-list__btn button")
    if button:
        button.string = radio.find_parent("label").get_text(" ", strip=True)


def monthly(document=None):
    document = document or soup()
    choose(section(document, "Период"), "month")
    return document


def parsed(document=None, **overrides):
    options = {"expected_region": "msk", "expected_period": "2026-08"}
    options.update(overrides)
    return parse_page(str(document if document is not None else monthly()), **options)


class MortgageDOMTests(unittest.TestCase):
    def test_discovery_reads_actual_checked_hidden_filters_not_visible_words(self):
        metadata = discover_page(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(metadata["region"], "msk")
        self.assertEqual(metadata["period"], "2026-08")
        self.assertEqual(metadata["basis"], "year")
        self.assertEqual(metadata["years"], [2009, 2025, 2026])
        self.assertEqual(metadata["months"], list(range(1, 9)))
        self.assertEqual(metadata["filters"], {"currency": "ALL", "market": "total", "housing": "all"})
        self.assertEqual(metadata["bottomCurrency"], "RUB")
        self.assertIsNone(metadata["sourceDate"])
        self.assertEqual(metadata["regions"]["rf"], {"value": "1", "label": "Российская Федерация"})
        self.assertEqual(metadata["regions"]["msk"]["value"], "1762")
        self.assertEqual(metadata["selection"]["creditOrganizations"], [])
        self.assertEqual(metadata["selection"]["includeAutonomous"], "yes")
        self.assertEqual(metadata["selection"]["dynamicStartYear"], "2009")

    def test_cumulative_document_rejected_as_monthly_even_when_identity_matches(self):
        with self.assertRaisesRegex(MortgageDOMError, "cumulative"):
            parsed(soup())

    def test_cumulative_evidence_can_be_read_but_never_relabelled_monthly(self):
        result = parsed(soup(), require_month=False)
        self.assertEqual(result["basis"], "year")
        self.assertEqual(result["metrics"]["all"]["volumeRub"], 374_000_000_000)
        self.assertEqual(result["rawMetrics"]["current"]["all"]["volumeRub"]["basis"], "year")
        self.assertEqual(result["rawMetrics"]["current"]["all"]["volumeRub"]["startPeriod"], "2026-01")

    def test_monthly_numbers_are_literal_not_divided_by_elapsed_months(self):
        result = parsed()
        self.assertEqual(result["basis"], "month")
        self.assertEqual(result["metrics"], {
            "all": {"volumeRub": 374_000_000_000, "count": 48_116,
                    "ratePct": 10.95, "termMonths": None, "averageAmountRub": None},
            "primary": {"volumeRub": 189_000_000_000, "count": 21_321,
                        "ratePct": 8.47, "termMonths": None, "averageAmountRub": None},
            "secondary": {"volumeRub": 185_000_000_000, "count": 26_795,
                          "ratePct": 13.87, "termMonths": None, "averageAmountRub": None},
        })
        observation = result["rawMetrics"]["current"]["all"]["count"]
        self.assertEqual(observation["rawValue"], "48\u00a0116")
        self.assertEqual(observation["rawUnit"], "шт.")
        self.assertEqual(observation["startPeriod"], "2026-08")
        self.assertEqual(observation["change"]["value"], 24)
        self.assertEqual(result["rawMetrics"]["selection"]["housing"], "all")
        self.assertEqual(result["rawMetrics"]["sourceCaption"], "Ипотечное кредитование")
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_region_is_selected_radio_and_caption_not_first_available_option(self):
        document = monthly()
        choose(section(document, "Регион", main=True), "1")
        result = parsed(document, expected_region="rf")
        self.assertEqual(result["region"], "rf")
        self.assertEqual(result["selection"]["regionValue"], "1")
        with self.assertRaisesRegex(MortgageDOMError, "differs from request"):
            parsed(document)

    def test_wrong_period_or_unsupported_expected_identity_rejected(self):
        for options in ({"expected_period": "2026-07"}, {"expected_period": "2026-13"},
                        {"expected_period": "2026-8"}, {"expected_period": None},
                        {"expected_region": "77"}, {"expected_region": "moscow"}):
            with self.subTest(options=options), self.assertRaises(MortgageDOMError):
                parsed(**options)

    def test_unsupported_selected_region_is_not_assumed_moscow(self):
        document = monthly()
        choose(section(document, "Регион", main=True), "1768")
        with self.assertRaisesRegex(MortgageDOMError, "not Moscow or Russia"):
            discover_page(str(document))

    def test_bottom_ytd_values_keep_own_period_currency_and_original_units(self):
        result = parsed()
        self.assertIsNone(result["metrics"]["all"]["averageAmountRub"])
        self.assertIsNone(result["metrics"]["all"]["termMonths"])
        amount = result["rawMetrics"]["details"]["averageAmountRub"]
        self.assertEqual(amount["value"], 7_769_000)
        self.assertEqual(amount["rawUnit"], "тыс. ₽")
        self.assertEqual(amount["currency"], "RUB")
        self.assertEqual(amount["basis"], "year")
        self.assertEqual(amount["periodLabel"], "за январь-август 2026 года")
        self.assertEqual(amount["startPeriod"], "2026-01")
        term = result["rawMetrics"]["details"]["termMonths"]
        self.assertEqual(term["value"], 290.4)
        self.assertEqual(term["unit"], "months")
        self.assertEqual(term["rawValue"], "24,2")
        self.assertEqual(term["rawUnit"], "лет")

    def test_detail_month_caption_is_preserved_independently_not_guessed_from_sidebar(self):
        document = monthly()
        for item in document.select(".card_item_body .item_period")[:2]:
            item.string = "за август 2026 года"
        result = parsed(document)
        self.assertEqual(result["details"]["termMonths"]["basis"], "month")
        self.assertEqual(result["details"]["termMonths"]["startPeriod"], "2026-08")
        self.assertEqual(result["metrics"]["all"]["termMonths"], 290.4)
        self.assertEqual(result["metrics"]["all"]["averageAmountRub"], 7_769_000)

    def test_detail_month_is_not_promoted_from_wrong_period_currency_or_scope(self):
        for mutation in ("period", "currency", "scope"):
            document = monthly()
            for item in document.select(".card_item_body .item_period")[:2]:
                item.string = "за июль 2026 года" if mutation == "period" else "за август 2026 года"
            if mutation == "currency":
                choose(document.select_one(".section-btn .btn-group"), "В валюте")
            elif mutation == "scope":
                choose(section(document, "Рынок недвижимости"), "primary")
            result = parsed(document)
            with self.subTest(mutation=mutation):
                self.assertIsNone(result["metrics"]["all"]["termMonths"])
                self.assertIsNone(result["metrics"]["all"]["averageAmountRub"])

    def test_stock_observations_do_not_become_monthly_flow_or_publication_date(self):
        result = parsed()
        debt = result["details"]["debtRub"]
        self.assertEqual(debt["value"], 3_093_000_000_000)
        self.assertEqual(debt["basis"], "stock")
        self.assertEqual(debt["asOf"], "2026-09-01")
        self.assertEqual(debt["period"], "2026-09")
        self.assertIsNone(debt["startPeriod"])
        self.assertEqual(result["details"]["delinquencyPct"]["value"], 1)
        self.assertEqual(result["details"]["delinquencyPct"]["change"]["unit"], "pp")
        self.assertIsNone(result["sourceDate"])

    def test_previous_annual_summary_is_not_current_month_or_invented_count(self):
        result = parsed()
        previous = result["rawMetrics"]["previousYear"]
        self.assertEqual(previous["year"], 2025)
        self.assertEqual(previous["metrics"]["all"]["volumeRub"], 550_000_000_000)
        self.assertIsNone(previous["metrics"]["all"]["count"])
        self.assertEqual(previous["metrics"]["primary"]["ratePct"], 7.09)
        self.assertEqual(previous["metrics"]["secondary"]["ratePct"], 12.72)
        self.assertEqual(previous["metricEvidence"]["all"]["volumeRub"]["endPeriod"], "2025-12")
        self.assertEqual(result["metrics"]["all"]["volumeRub"], 374_000_000_000)

    def test_previous_annual_summary_can_be_absent_without_creating_it(self):
        document = monthly()
        document.select(".main-card_item_body")[-1].decompose()
        result = parsed(document)
        self.assertIsNone(result["previousYear"])
        self.assertEqual(result["metrics"]["all"]["volumeRub"], 374_000_000_000)

    def test_missing_segment_is_none_not_total_minus_other_segment(self):
        document = monthly()
        for item in document.select(".item_secondary"):
            item.decompose()
        result = parsed(document)
        self.assertTrue(all(value is None for value in result["metrics"]["secondary"].values()))
        self.assertEqual(result["metricEvidence"]["secondary"], {})
        self.assertEqual(result["metrics"]["all"]["count"], 48_116)

    def test_zero_is_not_missing_and_placeholder_is_not_zero(self):
        document = monthly()
        values = document.select(".main-card_item_body")[0].select(".a-data__data")
        values[0].string = "0"
        values[1].string = "—"
        result = parsed(document)
        self.assertEqual(result["metrics"]["all"]["count"], 0)
        self.assertIsNone(result["metrics"]["primary"]["count"])
        self.assertEqual(result["metricEvidence"]["primary"]["count"]["rawValue"], "—")

    def test_metric_change_uses_literal_sign_not_badge_color(self):
        result = parsed()
        evidence = result["metricEvidence"]
        self.assertEqual(evidence["all"]["ratePct"]["change"], {
            "value": 2.46, "unit": "pp", "rawValue": "+2,46",
            "rawUnit": "п.п.", "rawText": "+2,46 п.п.",
        })
        self.assertEqual(evidence["primary"]["count"]["change"]["value"], -11)

    def test_nondefault_filters_are_reported_not_silently_reset_to_defaults(self):
        document = monthly()
        choose(section(document, "Валюта кредита"), "RUB")
        choose(section(document, "Рынок недвижимости"), "primary")
        choose(section(document, "Тип жилья"), "private")
        choose(document.select_one(".section-btn .btn-group"), "В валюте")
        result = parsed(document)
        self.assertEqual(result["filters"], {"currency": "RUB", "market": "primary", "housing": "private"})
        self.assertEqual(result["bottomCurrency"], "OTH")
        self.assertEqual(result["details"]["termMonths"]["currency"], "OTH")
        self.assertEqual(result["metricEvidence"]["all"]["ratePct"]["currency"], "RUB")

    def test_document_order_is_not_metric_or_date_control_identity(self):
        document = monthly()
        cards = document.select_one(".main__body > .main-card")
        first = cards.find(recursive=False)
        first.extract()
        cards.append(first)
        dates = document.select_one(".range-select")
        item = dates.find(recursive=False)
        item.extract()
        dates.append(item)
        self.assertEqual(parsed(document)["metrics"], parsed()["metrics"])

    def test_conflicting_selected_attributes_or_caption_is_rejected(self):
        for mutation in ("multiple", "active", "button"):
            document = monthly()
            if mutation == "multiple":
                section(document, "Регион", main=True).find("input", value="1")["checked"] = ""
            elif mutation == "active":
                control = section(document, "Период")
                control.find("input", value="month").find_parent("label")["class"] = ["btn"]
            else:
                section(document, "Регион", main=True).select_one("button").string = "Российская Федерация"
            with self.subTest(mutation=mutation), self.assertRaises(MortgageDOMError):
                discover_page(str(document))

    def test_stale_month_year_selection_is_not_valid_historical_data(self):
        document = monthly()
        year_control = document.select(".range-select > .filter-list")[1]
        choose(year_control, "2025")
        with self.assertRaisesRegex(MortgageDOMError, "differ from report year"):
            discover_page(str(document))

    def test_disabled_month_and_year_are_not_available(self):
        document = monthly()
        july = document.find("input", value="2026-07-01")
        july["disabled"] = ""
        year = document.select_one(".range-select").find("input", value="2025")
        year.find_parent("li")["aria-disabled"] = "true"
        result = discover_page(str(document))
        self.assertEqual(result["months"], [1, 2, 3, 4, 5, 6, 8])
        self.assertEqual(result["years"], [2009, 2026])
        self.assertNotIn("2026-07", result["availablePeriods"])
        self.assertNotIn("2025", [item["value"] for item in result["controls"]["year"]["options"]])

    def test_selected_disabled_month_or_year_is_rejected(self):
        for value, attribute, literal in (("2026-08-01", "disabled", ""),
                                          ("2026-08-01", "aria-disabled", "true"),
                                          ("2026", "disabled", "false"),
                                          ("2026", "aria-disabled", "true")):
            document = monthly()
            radio = document.select_one(".range-select").find("input", value=value)
            radio[attribute] = literal
            with self.subTest(value=value, attribute=attribute), self.assertRaisesRegex(MortgageDOMError, "disabled"):
                discover_page(str(document))

    def test_aria_disabled_false_does_not_remove_available_option(self):
        document = monthly()
        document.find("input", value="2026-07-01")["aria-disabled"] = "false"
        self.assertEqual(discover_page(str(document))["months"], list(range(1, 9)))

    def test_invalid_month_caption_is_not_converted_by_position(self):
        document = monthly()
        control = document.select(".range-select > .filter-list")[0]
        radio = control.find("input", value="2026-08-01")
        radio.find_next("span").string = "сентябрь"
        control.select_one("button").string = "сентябрь"
        with self.assertRaises(MortgageDOMError):
            discover_page(str(document))

    def test_no_default_basis_when_period_filter_missing(self):
        document = monthly()
        section(document, "Период").decompose()
        with self.assertRaisesRegex(MortgageDOMError, "control: Период"):
            discover_page(str(document))

    def test_invalid_or_unknown_literals_are_rejected_not_cleaned_into_numbers(self):
        for value in ("примерно 12", "1e9", "NaN", "∞", "-12", "12/13", "1,2,3"):
            document = monthly()
            document.select_one(".main-card_item_body .a-data__data").string = value
            with self.subTest(value=value), self.assertRaises(MortgageDOMError):
                parsed(document)

    def test_unknown_units_and_changes_are_rejected(self):
        document = monthly()
        document.select_one(".main-card_item_body .a-data__unit").string = "тыс. неизвестных"
        with self.assertRaisesRegex(MortgageDOMError, "unsupported count unit"):
            parsed(document)
        document = monthly()
        document.select_one(".badge-box__txt").string = "руб."
        with self.assertRaisesRegex(MortgageDOMError, "change unit"):
            parsed(document)

    def test_missing_badge_is_none_not_an_invented_zero_change(self):
        document = monthly()
        document.select_one(".main-card_item_body .item_delta").decompose()
        result = parsed(document)
        self.assertIsNone(result["metricEvidence"]["all"]["count"]["change"])

    def test_missing_unknown_duplicate_main_cards_fail_closed(self):
        for mutation in ("missing", "duplicate", "unknown"):
            document = monthly()
            card = document.select_one(".main-card_item_body")
            if mutation == "missing":
                card.decompose()
            elif mutation == "duplicate":
                card.parent.append(copy.copy(card))
            else:
                card.select_one("h5").string = "Новый неизвестный показатель"
            with self.subTest(mutation=mutation), self.assertRaises(MortgageDOMError):
                parsed(document)

    def test_wrong_segment_caption_does_not_relabel_primary_as_secondary(self):
        document = monthly()
        document.select_one(".item_primary h6").string = "Вторичный рынок"
        with self.assertRaisesRegex(MortgageDOMError, "segment label"):
            parsed(document)

    def test_all_empty_current_cards_are_not_a_successful_snapshot(self):
        document = monthly()
        for card in document.select(".main-card_item_body")[:3]:
            for value in card.select(".a-data__data"):
                value.string = "—"
        with self.assertRaisesRegex(MortgageDOMError, "no mortgage measurements"):
            parsed(document)

    def test_invalid_detail_period_and_stock_date_fail_closed(self):
        for label in ("за неизвестный период", "за август-январь 2026 года", "на 31.02.2026"):
            document = monthly()
            document.select_one(".card_item_body .item_period").string = label
            with self.subTest(label=label), self.assertRaises(MortgageDOMError):
                parsed(document)

    def test_previous_year_period_disagreement_is_rejected(self):
        document = monthly()
        card = document.select(".main-card_item_body")[-1]
        card.select_one(".item_bottom > .card_title > .card-subtitle").string = "за 2024 год"
        with self.assertRaisesRegex(MortgageDOMError, "periods disagree"):
            parsed(document)

    def test_access_verification_is_not_accepted_even_with_embedded_stale_markup(self):
        cases = [
            '<html><head><title>Доступ заблокирован [403]</title></head><body></body></html>',
            '<html><head><title>Access denied</title></head><body></body></html>',
            "<html><body><js-challenge-loader></js-challenge-loader></body></html>",
            '<html><body><div id="id_captcha_frame_div"></div></body></html>',
            "<html><body>Подтвердите, что вы не робот</body></html>",
        ]
        for html in cases:
            with self.subTest(html=html), self.assertRaises(AccessVerificationRequired):
                discover_page(html)
        document = monthly()
        document.body.append(BeautifulSoup("<p>Подтвердите, что вы не робот</p>", "html.parser"))
        with self.assertRaises(AccessVerificationRequired):
            parsed(document)

    def test_unrelated_or_incomplete_html_is_not_a_default_mortgage_page(self):
        for html in ("", "not html", "<html><body>48 116 шт.</body></html>",
                     "<h1 class='head-title'>Другой раздел</h1><div class='main__body'></div>"):
            with self.subTest(html=html), self.assertRaises(MortgageDOMError):
                discover_page(html)

    def test_fixture_omits_scripts_contacts_and_external_content(self):
        document = soup()
        self.assertFalse(document.select("script, iframe, img, a[href]"))
        self.assertNotIn("servicepipe", str(document))
        self.assertNotIn("nonce=", str(document))


if __name__ == "__main__":
    unittest.main()
