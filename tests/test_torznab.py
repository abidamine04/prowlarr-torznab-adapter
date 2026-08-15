import unittest
from xml.etree import ElementTree as ET

from app.torznab import caps_xml, normalize_results, results_xml


class TorznabTests(unittest.TestCase):
    def test_caps_is_xml(self):
        self.assertEqual(ET.fromstring(caps_xml()).tag, "caps")

    def test_result_conversion_and_escaping(self):
        body = results_xml([{
            "title": "Ubuntu & Linux <ISO>", "guid": "safe-guid", "size": 42,
            "publishDate": "2026-01-01T10:00:00Z", "seeders": 8, "leechers": 2,
            "magnetUrl": "magnet:?xt=urn:btih:ABC", "infoHash": "ABC",
            "indexer": "Example", "categories": [{"id": 4000}],
        }], 10)
        root = ET.fromstring(body)
        self.assertEqual(root.findtext("./channel/item/title"), "Ubuntu & Linux <ISO>")
        text = body.decode()
        self.assertIn('name="seeders" value="8"', text)
        self.assertIn('name="magneturl"', text)

    def test_deduplicates_and_keeps_best_seed_count(self):
        data = [{"guid": "same", "seeders": 1}, {"guid": "same", "seeders": 9}]
        self.assertEqual(normalize_results(data, 10)[0]["seeders"], 9)
        self.assertEqual(len(normalize_results(data, 10)), 1)

    def test_does_not_embed_unneeded_download_url(self):
        body = results_xml([{"title": "x", "guid": "g", "downloadUrl": "http://x/?apikey=SECRET"}], 10)
        self.assertNotIn(b"SECRET", body)


if __name__ == "__main__":
    unittest.main()
