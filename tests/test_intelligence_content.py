"""Synthetic source-shape fixtures, not live provider validation."""

from datetime import datetime, timedelta, timezone
import threading
import unittest
from unittest.mock import Mock

from market_intelligence.content import (
    CONTENT_SOURCES, MAX_CONTENT_BYTES, ContentError,
    collect_content, parse_content,
)


NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)


def page(body):
    return ("<!doctype html><html><head><title>Synthetic fixture</title></head>"
            "<body>" + body + "</body></html>").encode()


def ons():
    return page(
        "<h1>Consumer price inflation, UK: Synthetic month</h1>"
        "<p><span>Release date: </span><br/>18 November 2026<br/></p>"
        '<div id="main-points"><section><h2>1. Main points</h2><ul>'
        "<li>Synthetic Consumer Prices Index including housing costs (CPIH) rose.</li>"
        "<li>Synthetic Consumer Prices Index (CPI) was unchanged &amp; revised.</li>"
        '</ul><a href="http://127.0.0.1/private">Back to contents</a></section></div>'
        '<div id="other">Unselected table and forecast, not an outcome.</div>')


def boe():
    return page(
        "<h1>Synthetic Bank Rate decision - November 2026 Monetary Policy Summary and Minutes</h1>"
        '<div class="published-date">Published on 05 November 2026</div>'
        '<section class="page-section"><h2>Monetary Policy Summary, November 2026</h2>'
        "<p>The Monetary Policy Committee considered synthetic evidence about Bank Rate.</p>"
        "<p>Policy will depend on incoming evidence.</p></section>"
        '<section class="page-section"><h2>Minutes</h2><p>Not selected.</p></section>'
        '<p>Next meeting: December 2026. Not an actual decision.</p>')


def fed():
    # releaseTime intentionally has an omitted closing p like the source.
    return page(
        '<div id="article"><div class="heading col-md-8">'
        '<p class="article__time">October 28, 2026</p>'
        '<h3 class="title">Federal Reserve issues FOMC statement</h3>'
        '<p class="releaseTime">For release at 2:00 p.m. EDT<ul><li>Share</li></ul></div>'
        '<div class="col-xs-12 col-sm-8 col-md-8">'
        "<p>The Federal Open Market Committee considered synthetic evidence.</p>"
        "<p>The federal funds rate decision is described in synthetic terms.</p>"
        "<p>Voting members assessed the outlook.</p>"
        "<p>For media inquiries, use the official contact.</p>"
        '<p><a href="http://169.254.169.254/private">Implementation note</a></p>'
        "</div></div>")


def pce():
    return page(
        "<h1>News Release</h1><article>"
        "<h1>Personal Income and Outlays, September 2026</h1>"
        '<div class="field--name-field-release-date">'
        "EMBARGOED UNTIL RELEASE AT 8:30 a.m. EDT, Thursday, October 29, 2026</div>"
        '<div class="field--name-body">'
        "<p>Synthetic personal income and consumption spending.</p>"
        "<p>From the preceding month, the <strong>PCE price index</strong> "
        "was unchanged in this synthetic fixture.</p>"
        "<p>From the same month one year ago, the <strong>PCE price index</strong> "
        "was revised in this synthetic fixture.</p>"
        '<div class="table-responsive"><p>Not selected: PCE price index table.</p></div>'
        "<h2>Technical Notes</h2><p>Not selected.</p></div></article>")


def gdp(description="Real gross domestic product was revised in this synthetic fixture.",
        date="Thu, 29 Oct 2026 08:30:00 EDT", extra=""):
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<title>U.S. Bureau of Economic Analysis</title>"
        "<item><title>Gross Domestic Product by County</title><description>Ignore.</description></item>"
        "<item><title>GDP (Advance Estimate), 3rd Quarter 2026</title>"
        "<link>https://www.bea.gov/news/2026/synthetic-gdp</link>"
        "<description><![CDATA[" + description + "]]></description>"
        + ("<pubDate>" + date + "</pubDate>" if date is not None else "")
        + extra + "</item>"
        "<item><title>GDP (Second Estimate), 1st Quarter 2026</title></item>"
        "</channel></rss>"
    ).encode()


FIXTURES = {"ons_cpi": ons, "boe_policy": boe, "fed_statement": fed,
            "bea_pce": pce, "bea_gdp": gdp}


class OfficialContentTests(unittest.TestCase):
    def test_registry_is_bounded_explicit_and_immutable(self):
        self.assertEqual(len(CONTENT_SOURCES), 7)
        for source in CONTENT_SOURCES.values():
            self.assertTrue(source.institution)
            self.assertTrue(source.reason)
            self.assertIn(source.geography, ("UK", "US"))
            self.assertIn(source.kind, ("release", "policy"))
            self.assertTrue(source.url.startswith("https://"))
        with self.assertRaises(TypeError):
            CONTENT_SOURCES["arbitrary"] = CONTENT_SOURCES["ons_cpi"]

    def test_ons_quotes_only_main_points_preserves_cpi_and_cpih(self):
        doc, = parse_content("ons_cpi", ons(), NOW)
        self.assertEqual(doc.source, "ons_cpi")
        self.assertEqual(doc.geography, "UK")
        self.assertEqual(doc.topic, "inflation")
        self.assertEqual(doc.kind, "release")
        self.assertIn("(CPIH)", doc.text)
        self.assertIn("(CPI)", doc.text)
        self.assertIn("unchanged & revised.", doc.text)
        self.assertNotIn("Back to contents", doc.text)
        self.assertNotIn("Unselected", doc.text)
        self.assertNotIn("<", doc.text)
        self.assertIsNone(doc.published_at)  # Date-only is not UTC midnight.
        self.assertIsNone(doc.scheduled_at)
        self.assertEqual(doc.received_at, NOW)

    def test_boe_summary_is_not_minutes_or_schedule(self):
        doc, = parse_content("boe_policy", boe(), NOW)
        self.assertEqual(doc.kind, "policy")
        self.assertEqual(doc.topic, "monetary_policy")
        self.assertIn("Bank Rate", doc.text)
        self.assertNotIn("Not selected", doc.text)
        self.assertNotIn("Next meeting", doc.text)
        self.assertIsNone(doc.published_at)
        self.assertIsNone(doc.scheduled_at)

    def test_fed_statement_not_rss_headline_or_implementation_note(self):
        doc, = parse_content("fed_statement", fed(), NOW)
        self.assertEqual(doc.published_at, datetime(2026, 10, 28, 18, tzinfo=timezone.utc))
        self.assertIn("federal funds rate", doc.text)
        self.assertNotIn("media inquiries", doc.text)
        self.assertNotIn("Implementation", doc.text)
        self.assertEqual(doc.kind, "policy")

    def test_pce_selects_price_paragraphs_not_nominal_spending(self):
        doc, = parse_content("bea_pce", pce(), NOW)
        self.assertEqual(doc.topic, "inflation")
        self.assertIn("preceding month", doc.text)
        self.assertIn("same month one year ago", doc.text)
        self.assertNotIn("consumption spending", doc.text)
        self.assertNotIn("table.", doc.text)
        self.assertEqual(doc.published_at, datetime(2026, 10, 29, 12, 30, tzinfo=timezone.utc))

    def test_gdp_selects_first_national_release_not_regional_or_historical(self):
        doc, = parse_content("bea_gdp", gdp("<p>Real <b>gross domestic product</b> "
                                        "was revised &amp; bounded.</p>"), NOW)
        self.assertEqual(doc.text, "Real gross domestic product was revised & bounded.")
        self.assertEqual(doc.source_url, "https://www.bea.gov/news/2026/synthetic-gdp")
        self.assertEqual(doc.published_at, datetime(2026, 10, 29, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(doc.topic, "growth")

    def test_missing_publication_stays_unknown_not_receipt(self):
        doc, = parse_content("bea_gdp", gdp(date=None), NOW)
        self.assertIsNone(doc.published_at)
        doc, = parse_content("fed_statement", fed().replace(
            b'<p class="article__time">October 28, 2026</p>', b"").replace(
            b'<p class="releaseTime">For release at 2:00 p.m. EDT<ul><li>Share</li></ul>', b""), NOW)
        self.assertIsNone(doc.published_at)

    def test_semantic_id_excludes_receipt_and_html_decoration(self):
        for name, fixture in FIXTURES.items():
            with self.subTest(name=name):
                first, = parse_content(name, fixture(), NOW)
                later, = parse_content(name, fixture(), NOW + timedelta(hours=1))
                self.assertEqual(first.evidence_id, later.evidence_id)
                self.assertNotEqual(first.received_at, later.received_at)
                self.assertLessEqual(len(first.title), 500)
                self.assertLessEqual(len(first.text), 12000)
        decorated, = parse_content("ons_cpi", ons().replace(
            b"<body>", b'<body><div class="banner">Not evidence</div>'), NOW)
        original, = parse_content("ons_cpi", ons(), NOW)
        self.assertEqual(original.evidence_id, decorated.evidence_id)

    def test_revision_and_publication_change_semantic_id(self):
        first, = parse_content("bea_gdp", gdp(), NOW)
        revision, = parse_content("bea_gdp", gdp().replace(b"was revised", b"was corrected"), NOW)
        dated, = parse_content("bea_gdp", gdp(date="Thu, 29 Oct 2026 08:31:00 EDT"), NOW)
        self.assertNotEqual(first.evidence_id, revision.evidence_id)
        self.assertNotEqual(first.evidence_id, dated.evidence_id)

    def test_unsupported_unknown_and_arbitrary_sources_never_fetch(self):
        for name in ("bls_cpi", "bls_employment", "https://localhost/", "arbitrary", None):
            with self.subTest(name=name):
                transport = Mock()
                with self.assertRaises(ContentError):
                    collect_content(name, transport, lambda: NOW, None)
                transport.fetch.assert_not_called()
                with self.assertRaises(ContentError):
                    parse_content(name, b"not supported", NOW)

    def test_fixed_fetch_returns_exact_raw_bytes_without_following_links(self):
        for name, fixture in FIXTURES.items():
            with self.subTest(name=name):
                transport = Mock()
                raw = fixture()
                transport.fetch.return_value = raw
                event = threading.Event()
                returned, docs = collect_content(name, transport, lambda: NOW, event)
                self.assertIs(returned, raw)
                self.assertEqual(len(docs), 1)
                transport.fetch.assert_called_once_with(CONTENT_SOURCES[name].url, cancel_event=event)

    def test_cancellation_before_and_after_fetch_never_publishes(self):
        event = threading.Event()
        transport = Mock()
        event.set()
        with self.assertRaises(ContentError):
            collect_content("ons_cpi", transport, lambda: NOW, event)
        transport.fetch.assert_not_called()
        event.clear()
        def fetch(*args, **kwargs):
            event.set()
            return ons()
        transport.fetch.side_effect = fetch
        clock = Mock(return_value=NOW)
        with self.assertRaises(ContentError):
            collect_content("ons_cpi", transport, clock, event)
        clock.assert_not_called()
        event.clear()
        transport.fetch.side_effect = None
        transport.fetch.return_value = ons()
        def cancelling_clock():
            event.set()
            return NOW
        with self.assertRaises(ContentError):
            collect_content("ons_cpi", transport, cancelling_clock, event)

    def test_transport_failure_has_no_fallback(self):
        transport = Mock()
        transport.fetch.side_effect = RuntimeError("synthetic transport failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic transport failure"):
            collect_content("ons_cpi", transport, lambda: NOW, None)
        self.assertEqual(transport.fetch.call_count, 1)

    def test_invalid_receipt_or_future_publication_is_rejected(self):
        for clock in (NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=1))), None):
            with self.subTest(clock=clock), self.assertRaises(ContentError):
                parse_content("ons_cpi", ons(), clock)
        with self.assertRaises(ContentError):
            parse_content("fed_statement", fed(), datetime(2026, 10, 1, tzinfo=timezone.utc))

    def test_payload_and_output_bounds_fail_without_truncation(self):
        for value in (b"", b"x" * (MAX_CONTENT_BYTES + 1), "not bytes", bytearray(ons())):
            with self.subTest(kind=type(value)), self.assertRaises(ContentError):
                parse_content("ons_cpi", value, NOW)
        for value in (ons().replace(b"Synthetic month", b"x" * 501),
                      ons().replace(b"rose.", b"x" * 12001)):
            with self.assertRaises(ContentError):
                parse_content("ons_cpi", value, NOW)

    def test_exact_byte_and_text_limits_are_accepted(self):
        raw = ons()
        padded = raw + b" " * (MAX_CONTENT_BYTES - len(raw))
        original, = parse_content("ons_cpi", raw, NOW)
        bounded, = parse_content("ons_cpi", padded, NOW)
        self.assertEqual(len(padded), MAX_CONTENT_BYTES)
        self.assertEqual(original.evidence_id, bounded.evidence_id)
        prefix = "Real gross domestic product "
        doc, = parse_content("bea_gdp", gdp(prefix + "x" * (12000 - len(prefix))), NOW)
        self.assertEqual(len(doc.text), 12000)
        title_prefix = "Consumer price inflation, UK: "
        title = title_prefix + "x" * (500 - len(title_prefix))
        doc, = parse_content("ons_cpi", raw.replace(
            b"Consumer price inflation, UK: Synthetic month", title.encode()), NOW)
        self.assertEqual(len(doc.title), 500)

    def test_partial_pages_selected_blocks_and_wrong_pages_fail_closed(self):
        for name, fixture in FIXTURES.items():
            with self.subTest(name=name), self.assertRaises(ContentError):
                parse_content(name, fixture()[:-9], NOW)
        cases = [
            ("ons_cpi", ons().replace(b"</li>", b"", 1)),
            ("ons_cpi", ons().replace(b"</section>", b"", 1)),
            ("ons_cpi", ons().replace(b"(CPIH)", b"unrelated")),
            ("boe_policy", boe().replace(b"</p>", b"", 1)),
            ("fed_statement", fed().replace(b"For media inquiries,", b"Unrecognized footer,")),
            ("bea_pce", pce().replace(b"From the same month one year ago,", b"Changed shape,")),
            ("ons_cpi", page("<h1>Access Denied</h1>")),
            ("bea_gdp", gdp().replace(b"gross domestic product", b"unrelated topic")),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value[:50]), self.assertRaises(ContentError):
                parse_content(name, value, NOW)

    def test_ambiguous_dates_and_duplicate_selected_fields_rejected(self):
        cases = [
            ("ons_cpi", ons().replace(b"18 November 2026", b"next Wednesday")),
            ("boe_policy", boe().replace(b"05 November 2026", b"November 2026")),
            ("fed_statement", fed().replace(b"2:00 p.m. EDT", b"2:00 p.m. ET")),
            ("bea_pce", pce().replace(b"Thursday, October 29", b"Friday, October 29")),
            ("bea_gdp", gdp(date="Thu, 29 Oct 2026 08:30:00")),
            ("bea_gdp", gdp(date="Fri, 29 Oct 2026 08:30:00 EDT")),
            ("bea_gdp", gdp(extra="<pubDate>Thu, 29 Oct 2026 08:30:00 EDT</pubDate>")),
        ]
        for name, value in cases:
            with self.subTest(name=name), self.assertRaises(ContentError):
                parse_content(name, value, NOW)

    def test_injected_markup_entities_duplicates_and_unbounded_trees_rejected(self):
        cases = [
            ("ons_cpi", ons().replace(b"rose.", b"<script>bad()</script>rose.")),
            ("ons_cpi", ons().replace(b'<div id="main-points">', b'<div id="main-points" id="other">')),
            ("ons_cpi", ons().replace(b"<body>", b"<body>" + b"<div>" * 130)),
            ("bea_gdp", b'<!DOCTYPE rss [<!ENTITY x SYSTEM "file:///private">]>' + gdp()),
            ("bea_gdp", ('<!DOCTYPE rss [<!ENTITY x "expanded">]>' +
                         gdp().decode()).encode("utf-16")),
            ("bea_gdp", gdp('<iframe src="http://localhost/private"></iframe>')),
            ("bea_gdp", gdp().replace(b"https://www.bea.gov/news/2026/synthetic-gdp",
                                    b"https://www.bea.gov.evil.test/news/2026/synthetic-gdp")),
        ]
        for name, value in cases:
            with self.subTest(name=name), self.assertRaises(ContentError):
                parse_content(name, value, NOW)


if __name__ == "__main__":
    unittest.main()
