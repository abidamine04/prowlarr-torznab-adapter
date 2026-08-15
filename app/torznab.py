from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.etree import ElementTree as ET

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("torznab", TORZNAB_NS)
ET.register_namespace("atom", ATOM_NS)


def _text(parent: ET.Element, tag: str, value: object | None, **attrs: str) -> None:
    if value is None or value == "":
        return
    node = ET.SubElement(parent, tag, attrs)
    node.text = str(value)


def _attr(item: ET.Element, name: str, value: object | None) -> None:
    if value is not None and value != "":
        ET.SubElement(item, f"{{{TORZNAB_NS}}}attr", {"name": name, "value": str(value)})


def _date(value: object | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return format_datetime(parsed)
    except ValueError:
        return str(value)


def _category_ids(result: dict) -> list[str]:
    output: list[str] = []
    for category in result.get("categories") or []:
        value = category.get("id") if isinstance(category, dict) else category
        if value is not None:
            output.append(str(value))
    return output


def _identity(result: dict) -> str:
    return str(
        result.get("infoHash")
        or result.get("magnetUrl")
        or result.get("guid")
        or f'{result.get("title", "")}:{result.get("size", "")}'
    ).lower()


def normalize_results(results: list[dict], maximum: int) -> list[dict]:
    unique: dict[str, dict] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        key = _identity(result)
        current = unique.get(key)
        if current is None or int(result.get("seeders") or 0) > int(current.get("seeders") or 0):
            unique[key] = result
    return sorted(unique.values(), key=lambda x: int(x.get("seeders") or 0), reverse=True)[:maximum]


def caps_xml() -> bytes:
    root = ET.Element("caps")
    ET.SubElement(root, "server", {"title": "Prowlarr Aggregate Adapter"})
    ET.SubElement(root, "limits", {"default": "100", "max": "500"})
    searching = ET.SubElement(root, "searching")
    ET.SubElement(searching, "search", {"available": "yes", "supportedParams": "q"})
    categories = ET.SubElement(root, "categories")
    for category_id, name in ((1000, "Console"), (2000, "Movies"), (3000, "Audio"),
                              (4000, "PC"), (5000, "TV"), (6000, "XXX"),
                              (7000, "Books"), (8000, "Other")):
        ET.SubElement(categories, "category", {"id": str(category_id), "name": name})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def error_xml(code: int, description: str) -> bytes:
    return ET.tostring(ET.Element("error", {"code": str(code), "description": description}),
                       encoding="utf-8", xml_declaration=True)


def results_xml(results: list[dict], maximum: int) -> bytes:
    rss = ET.Element("rss", {"version": "1.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, f"{{{ATOM_NS}}}link", {"rel": "self", "type": "application/rss+xml"})
    _text(channel, "title", "Prowlarr Aggregate")
    for result in normalize_results(results, maximum):
        item = ET.SubElement(channel, "item")
        title = result.get("title") or "Untitled"
        identity = result.get("guid") or result.get("infoHash") or hashlib.sha256(_identity(result).encode()).hexdigest()
        magnet = result.get("magnetUrl") or ""
        info_url = result.get("infoUrl") or ""
        _text(item, "title", title)
        _text(item, "guid", identity, isPermaLink="false")
        if str(info_url).startswith(("http://", "https://")):
            _text(item, "link", info_url)
            _text(item, "comments", info_url)
        elif str(magnet).startswith("magnet:"):
            _text(item, "link", magnet)
        _text(item, "pubDate", _date(result.get("publishDate")))
        _text(item, "size", result.get("size"))
        for category_id in _category_ids(result):
            _text(item, "category", category_id)
            _attr(item, "category", category_id)
        seeders = result.get("seeders")
        leechers = result.get("leechers")
        _attr(item, "seeders", seeders)
        _attr(item, "leechers", leechers)
        if seeders is not None or leechers is not None:
            _attr(item, "peers", int(seeders or 0) + int(leechers or 0))
        _attr(item, "magneturl", magnet if str(magnet).startswith("magnet:") else None)
        _attr(item, "infohash", result.get("infoHash"))
        _attr(item, "tracker", result.get("indexer"))
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)
