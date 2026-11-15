from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import Mock

from market_intelligence.news import FED_NEWS_URL, ProviderError, parse_fed_news
from market_intelligence.contracts import NewsEvidence
from market_intelligence.providers import CalendarTransport
from market_intelligence.store import construct, encode
from test_intelligence_providers import FakeResponse


def feed(description="Policy announcement.", published="Fri, 13 Nov 2026 14:00:00 GMT", link="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261113a.htm", extra=""):
    return ("<rss version='2.0'><channel><title>Federal Reserve</title><item><title>Policy release</title><link>" + link +
            "</link><description><![CDATA[" + description + "]]></description>" +
            ("<pubDate>" + published + "</pubDate>" if published is not None else "") +
            extra + "</item></channel></rss>").encode("utf-8")


@unittest.skipIf(importlib.util.find_spec("defusedxml") is None, "Install requirements-intelligence.txt for RSS tests")
class IntelligenceNewsParserTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)

    def test_official_evidence_has_receipt_source_family_and_hash(self):
        batch = parse_fed_news(feed(), self.now)
        self.assertEqual(len(batch.items), 1)
        item = batch.items[0]
        self.assertEqual(item.source, "fed_monetary")
        self.assertEqual(item.publisher_family, "federal-reserve")
        self.assertEqual(item.received_at, self.now)
        self.assertEqual(item.published_at, datetime(2026, 11, 13, 14, tzinfo=timezone.utc))
        self.assertIsNone(item.supersedes)
        self.assertEqual(len(batch.input_hash), 64)

    def test_duplicate_items_collapse_without_claiming_independent_confirmation(self):
        item = feed().split(b"<item>", 1)[1].split(b"</item>", 1)[0]
        duplicated = feed().replace(b"</channel>", b"<item>" + item + b"</item></channel>")
        self.assertEqual(len(parse_fed_news(duplicated, self.now).items), 1)

    def test_html_script_style_and_embedded_links_are_not_executed_or_followed(self):
        item = parse_fed_news(feed(description="<p>Policy <b>release</b>.</p><script>fetch('secret')</script><style>hidden</style><a href='http://127.0.0.1'>Source</a>"), self.now).items[0]
        self.assertNotIn("secret", item.snippet)
        self.assertNotIn("hidden", item.snippet)
        self.assertNotIn("127.0.0.1", item.snippet)
        self.assertIn("Policy", item.snippet)

    def test_missing_or_floating_publication_remains_unknown(self):
        for published in (None, "Fri, 13 Nov 2026 14:00:00 -0000"):
            self.assertIsNone(parse_fed_news(feed(published=published), self.now).items[0].published_at)

    def test_changes_have_distinct_evidence_but_same_cluster(self):
        original = parse_fed_news(feed(), self.now).items[0]
        updated = parse_fed_news(feed(description="Updated policy announcement."), self.now).items[0]
        self.assertNotEqual(original.evidence_id, updated.evidence_id)
        self.assertEqual(original.cluster_id, updated.cluster_id)
        self.assertIsNone(updated.supersedes)

    def test_external_entity_and_doctype_are_refused(self):
        for payload in (b"<!DOCTYPE rss [<!ENTITY x SYSTEM 'file:///private'>]><rss version='2.0'><channel><title>&x;</title></channel></rss>",
                        b"<!DOCTYPE rss><rss version='2.0'><channel /></rss>"):
            with self.assertRaises(ProviderError):
                parse_fed_news(payload, self.now)

    def test_unsafe_links_future_dates_and_oversized_text_fail_explicitly(self):
        for payload in (feed(link="https://127.0.0.1/newsevents/pressreleases/x.htm"),
                        feed(link="https://www.federalreserve.gov/newsevents/pressreleases/../secret.htm"),
                        feed(published="Sun, 15 Nov 2026 14:00:00 GMT"), feed(description="x" * 2001),
                        feed(extra="<title>Duplicate title</title>"), b"x" * 1048577):
            with self.assertRaises(ProviderError):
                parse_fed_news(payload, self.now)

    def test_evidence_contract_rejects_unverified_time_and_mutation_relations(self):
        item = parse_fed_news(feed(), self.now).items[0]
        for changes in ({"received_at": self.now.replace(tzinfo=None)}, {"content_hash": "invalid"},
                        {"source_url": "https://user:secret@example.com/"}, {"supersedes": item.evidence_id}):
            with self.assertRaises(ValueError):
                replace(item, **changes)

    def test_wire_roundtrip_and_schema_preserve_news_receipt(self):
        item = parse_fed_news(feed(), self.now).items[0]
        payload = json.loads(encode(item))
        self.assertEqual(construct(NewsEvidence, payload), item)
        if importlib.util.find_spec("jsonschema") is None:
            self.skipTest("Install requirements-dashboard-dev.txt for schema validation")
        from jsonschema import Draft202012Validator, FormatChecker
        schema = json.loads((Path(__file__).resolve().parents[1] / "market_intelligence" / "schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(payload)
        payload["content_hash"] = "a" * 64 + "\n"
        self.assertFalse(validator.is_valid(payload))

    def test_self_closing_hidden_tags_do_not_hide_following_text(self):
        item = parse_fed_news(feed(description="<script/><p>Visible statement</p>"), self.now).items[0]
        self.assertEqual(item.snippet, "Visible statement")

    def test_inline_formatting_preserves_words_numbers_and_paragraphs(self):
        item = parse_fed_news(feed(description="<p>Rate un<b>changed</b> at 4.<b>25</b>%.</p><p>Next paragraph.</p>"), self.now).items[0]
        self.assertEqual(item.snippet, "Rate unchanged at 4.25%. Next paragraph.")

    def test_dot_segment_aliases_are_not_distinct_article_evidence(self):
        with self.assertRaises(ProviderError):
            parse_fed_news(feed(link="https://www.federalreserve.gov/newsevents/pressreleases/./monetary20261113a.htm"), self.now)

    def test_feed_depth_and_item_counts_are_bounded(self):
        nested = b"<rss version='2.0'><channel>" + b"<x>" * 40 + b"</x>" * 40 + b"</channel></rss>"
        item = feed().split(b"<item>", 1)[1].split(b"</item>", 1)[0]
        oversized = b"<rss version='2.0'><channel>" + (b"<item>" + item + b"</item>") * 201 + b"</channel></rss>"
        for payload in (nested, oversized):
            with self.assertRaises(ProviderError):
                parse_fed_news(payload, self.now)


class IntelligenceNewsTransportTests(unittest.TestCase):
    def test_fixed_fed_feed_routes_host_path_and_xml_type(self):
        response = FakeResponse(body=b"<rss/>", headers={"Content-Type": "application/rss+xml"})
        connection = Mock(sock=None)
        connection.getresponse.return_value = response
        resolver = Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
        factory = Mock(return_value=connection)
        transport = CalendarTransport(resolver, factory, monotonic=lambda: 0.0)
        self.assertEqual(transport.fetch(FED_NEWS_URL), b"<rss/>")
        resolver.assert_called_once_with("www.federalreserve.gov", 443, type=socket.SOCK_STREAM)
        factory.assert_called_once_with("www.federalreserve.gov", "93.184.216.34", 3.0)
        self.assertEqual(connection.request.call_args.args[:2], ("GET", "/feeds/press_monetary.xml"))
        self.assertIn("application/rss+xml", connection.request.call_args.kwargs["headers"]["Accept"])

    def test_calendar_content_or_article_urls_are_not_feed_requests(self):
        connection = Mock(sock=None)
        connection.getresponse.return_value = FakeResponse(headers={"Content-Type": "text/calendar"})
        resolver = Mock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
        transport = CalendarTransport(resolver, Mock(return_value=connection), monotonic=lambda: 0.0)
        with self.assertRaises(ProviderError):
            transport.fetch(FED_NEWS_URL)
        before = resolver.call_count
        with self.assertRaises(ProviderError):
            transport.fetch("https://www.federalreserve.gov/newsevents/pressreleases/monetary20261113a.htm")
        self.assertEqual(resolver.call_count, before)


if __name__ == "__main__":
    unittest.main()