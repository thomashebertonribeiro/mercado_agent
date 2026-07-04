"""
tests/test_scanner/test_scanner.py

Testes para o MarketScanner — lógica de varredura, frequência e árvore.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scanner.models import CategoryScanFrequency, CategoryScanState, Discovery, ScanResult


class TestCategoryScanState:
    def test_defaults(self):
        s = CategoryScanState(
            category_id="MLB1234",
            category_name="Teste",
            parent_id=None,
            path=[],
            total_items=0,
            frequency=CategoryScanFrequency.ONCE,
        )
        assert s.last_scanned_at is None
        assert s.next_scan_at is None
        assert s.scan_count == 0
        assert s.is_leaf is True
        assert s.children == []


class TestDiscovery:
    def test_defaults(self):
        d = Discovery(
            product_id="MLB1",
            title="Produto",
            category_id="MLB1234",
            seller_id=123,
            price=99.9,
        )
        assert d.discovered_at is not None
        assert d.is_new is True


class TestScanResult:
    def test_defaults(self):
        r = ScanResult()
        assert r.categories_scanned == 0
        assert r.products_found == 0
        assert r.new_products == 0
        assert r.removed_products == 0
        assert r.new_sellers == set()
        assert r.errors == 0

    def test_new_sellers_accumulates(self):
        r = ScanResult()
        r.new_sellers.add(1)
        r.new_sellers.add(2)
        assert len(r.new_sellers) == 2

    def test_duration_round_trip(self):
        r = ScanResult()
        r.started_at = datetime.now(timezone.utc)
        r.finished_at = r.started_at + timedelta(seconds=5)
        r.duration_seconds = (r.finished_at - r.started_at).total_seconds()
        assert r.duration_seconds == 5.0


class TestCategoryScanFrequency:
    def test_enum_values(self):
        assert CategoryScanFrequency.HIGH.value == "high"
        assert CategoryScanFrequency.MEDIUM.value == "medium"
        assert CategoryScanFrequency.LOW.value == "low"
        assert CategoryScanFrequency.ONCE.value == "once"

    def test_frequency_ordering(self):
        freqs = [CategoryScanFrequency.LOW, CategoryScanFrequency.MEDIUM, CategoryScanFrequency.HIGH]
        assert sorted(freqs, key=lambda f: {"high": 3, "medium": 2, "low": 1}[f.value]) == [
            CategoryScanFrequency.LOW,
            CategoryScanFrequency.MEDIUM,
            CategoryScanFrequency.HIGH,
        ]


class TestScannerHelpers:
    """Testa métodos auxiliares do scanner com instância mínima."""

    def _make_scanner(self):
        """Cria scanner sem provider (apenas para testar helpers puros)."""
        from scanner.scanner import MarketScanner
        s = MarketScanner.__new__(MarketScanner)
        s._categories = []
        s._known_products = set()
        s._semaphore = None
        return s

    def test_compute_frequency_high(self):
        s = self._make_scanner()
        assert s._compute_frequency(10000) == CategoryScanFrequency.HIGH
        assert s._compute_frequency(999999) == CategoryScanFrequency.HIGH

    def test_compute_frequency_medium(self):
        s = self._make_scanner()
        assert s._compute_frequency(1000) == CategoryScanFrequency.MEDIUM
        assert s._compute_frequency(9999) == CategoryScanFrequency.MEDIUM

    def test_compute_frequency_low(self):
        s = self._make_scanner()
        assert s._compute_frequency(1) == CategoryScanFrequency.LOW
        assert s._compute_frequency(999) == CategoryScanFrequency.LOW

    def test_compute_frequency_once(self):
        s = self._make_scanner()
        assert s._compute_frequency(0) == CategoryScanFrequency.ONCE

    def test_compute_next_scan_high(self):
        s = self._make_scanner()
        now = datetime.now(timezone.utc)
        cat = CategoryScanState(
            category_id="x", category_name="x", parent_id=None, path=[],
            total_items=10000, frequency=CategoryScanFrequency.HIGH,
        )
        nxt = s._compute_next_scan(cat)
        assert nxt > now + timedelta(hours=5)
        assert nxt < now + timedelta(hours=7)

    def test_compute_next_scan_low(self):
        s = self._make_scanner()
        now = datetime.now(timezone.utc)
        cat = CategoryScanState(
            category_id="x", category_name="x", parent_id=None, path=[],
            total_items=100, frequency=CategoryScanFrequency.LOW,
        )
        nxt = s._compute_next_scan(cat)
        assert nxt > now + timedelta(hours=71)
        assert nxt < now + timedelta(hours=73)

    def test_count_leaves_flat(self):
        s = self._make_scanner()
        cats = [
            CategoryScanState("a", "A", None, [], 0, CategoryScanFrequency.LOW, is_leaf=True),
            CategoryScanState("b", "B", None, [], 0, CategoryScanFrequency.LOW, is_leaf=True),
        ]
        assert s._count_leaves(cats) == 2

    def test_count_leaves_nested(self):
        s = self._make_scanner()
        child1 = CategoryScanState("c1", "C1", "p", [], 0, CategoryScanFrequency.LOW, is_leaf=True)
        child2 = CategoryScanState("c2", "C2", "p", [], 0, CategoryScanFrequency.LOW, is_leaf=True)
        parent = CategoryScanState("p", "P", None, [], 0, CategoryScanFrequency.LOW, is_leaf=False, children=[child1, child2])
        assert s._count_leaves([parent]) == 2

    def test_get_due_leaves_all_due(self):
        s = self._make_scanner()
        now = datetime.now(timezone.utc)
        cats = [
            CategoryScanState("a", "A", None, [], 100, CategoryScanFrequency.HIGH,
                              is_leaf=True, next_scan_at=now - timedelta(hours=1)),
            CategoryScanState("b", "B", None, [], 100, CategoryScanFrequency.LOW,
                              is_leaf=True, next_scan_at=now - timedelta(hours=1)),
        ]
        due = s._get_due_leaves(cats)
        assert len(due) == 2

    def test_get_due_leaves_not_due(self):
        s = self._make_scanner()
        now = datetime.now(timezone.utc)
        cats = [
            CategoryScanState("a", "A", None, [], 100, CategoryScanFrequency.HIGH,
                              is_leaf=True, next_scan_at=now + timedelta(hours=1)),
        ]
        due = s._get_due_leaves(cats)
        assert len(due) == 0

    def test_get_due_leaves_skips_once(self):
        s = self._make_scanner()
        now = datetime.now(timezone.utc)
        cats = [
            CategoryScanState("a", "A", None, [], 0, CategoryScanFrequency.ONCE,
                              is_leaf=True, next_scan_at=now - timedelta(hours=1)),
        ]
        due = s._get_due_leaves(cats)
        assert len(due) == 0

    def test_get_due_leaves_nested(self):
        s = self._make_scanner()
        now = datetime.now(timezone.utc)
        child = CategoryScanState("c", "C", "p", [], 100, CategoryScanFrequency.LOW,
                                  is_leaf=True, next_scan_at=now - timedelta(hours=1))
        parent = CategoryScanState("p", "P", None, [], 200, CategoryScanFrequency.MEDIUM,
                                   is_leaf=False, children=[child])
        due = s._get_due_leaves([parent])
        assert len(due) == 1
        assert due[0].category_id == "c"

    def test_find_category_in_flat_list(self):
        s = self._make_scanner()
        cats = [
            CategoryScanState("a", "A", None, [], 0, CategoryScanFrequency.LOW, is_leaf=True),
            CategoryScanState("b", "B", None, [], 0, CategoryScanFrequency.LOW, is_leaf=True),
        ]
        found = s._find_category("b", cats)
        assert found is not None
        assert found.category_id == "b"

    def test_find_category_in_nested(self):
        s = self._make_scanner()
        child = CategoryScanState("c", "C", "p", [], 0, CategoryScanFrequency.LOW, is_leaf=True)
        parent = CategoryScanState("p", "P", None, [], 0, CategoryScanFrequency.LOW, is_leaf=False, children=[child])
        found = s._find_category("c", [parent])
        assert found is not None
        assert found.category_id == "c"

    def test_find_category_missing(self):
        s = self._make_scanner()
        cats = [
            CategoryScanState("a", "A", None, [], 0, CategoryScanFrequency.LOW, is_leaf=True),
        ]
        found = s._find_category("z", cats)
        assert found is None

    def test_scan_result_accumulates(self):
        r = ScanResult()
        r.categories_scanned = 5
        r.products_found = 100
        r.new_products = 10
        r.errors = 2
        r.new_sellers.add(1)
        r.new_sellers.add(2)
        assert r.categories_scanned == 5
        assert r.products_found == 100
        assert r.new_products == 10
        assert r.errors == 2
        assert len(r.new_sellers) == 2
