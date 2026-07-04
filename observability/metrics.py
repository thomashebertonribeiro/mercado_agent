"""
observability/metrics.py

Métricas globais da plataforma (singleton thread-safe).

Expoe counters, histograms e gauges para monitoramento do sistema.
Consumido por endpoints REST e futuramente por exportador Prometheus.

Uso:
    from observability.metrics import platform_metrics
    platform_metrics.collections_total.inc()
    platform_metrics.record_category_duration("MLB1234", 12.5)
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Histogram:
    """Histograma simples com média, p99, p50, min, max e contagem."""

    values: List[float] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, value: float) -> None:
        with self._lock:
            self.values.append(value)

    def snapshot(self) -> dict:
        with self._lock:
            n = len(self.values)
            if n == 0:
                return {"count": 0, "min": None, "max": None, "avg": None, "p50": None, "p99": None}
            sorted_vals = sorted(self.values)
            return {
                "count": n,
                "min": sorted_vals[0],
                "max": sorted_vals[-1],
                "avg": sum(sorted_vals) / n,
                "p50": sorted_vals[n // 2],
                "p99": sorted_vals[int(n * 0.99)],
            }

    def reset(self) -> None:
        with self._lock:
            self.values.clear()


@dataclass
class Counter:
    """Contador monotônico thread-safe."""

    _value: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, amount: int = 1) -> None:
        with self._lock:
            self._value += amount

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = 0


@dataclass
class Gauge:
    """Medidor de valor atual thread-safe."""

    _value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value -= amount

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def reset(self) -> None:
        with self._lock:
            self._value = 0.0

    @property
    def value(self) -> float:
        with self._lock:
            return self._value


class PlatformMetrics:
    """Métricas globais da plataforma.

    Atributos principais:
        collections_total          — total de jobs de coleta executados
        collections_active         — gauge: jobs em execução agora
        collection_errors_total    — total de jobs que falharam
        products_collected_total   — total de produtos processados
        events_generated_total     — total de eventos gerados
        requests_total             — total de requisições HTTP à API externa
        scans_total                — total de varreduras do scanner
        recommendations_total      — total de recomendações geradas
        signals_total              — total de sinais gerados
        reports_total              — total de relatórios do AI Analyst
        category_duration_seconds  — histograma: duração por categoria
        category_product_count     — gauge: produtos conhecidos por categoria
        db_pool_size               — gauge: tamanho do pool de conexões
        requests_in_flight         — gauge: requisições HTTP concorrentes
        uptime_seconds             — tempo desde o início do processo
    """

    def __init__(self) -> None:
        # Counters
        self.collections_total = Counter()
        self.collection_errors_total = Counter()
        self.products_collected_total = Counter()
        self.events_generated_total = Counter()
        self.requests_total = Counter()
        self.scans_total = Counter()
        self.recommendations_total = Counter()
        self.signals_total = Counter()
        self.reports_total = Counter()

        # Gauges
        self.collections_active = Gauge()
        self.requests_in_flight = Gauge()
        self.db_pool_size = Gauge()
        self.category_product_count: Dict[str, Gauge] = defaultdict(Gauge)

        # Histograms
        self.category_duration_seconds: Dict[str, Histogram] = defaultdict(Histogram)

        # Uptime
        self._start_time = time.monotonic()

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start_time

    def record_category_duration(self, category_id: str, seconds: float) -> None:
        self.category_duration_seconds[category_id].record(seconds)

    def set_category_product_count(self, category_id: str, count: int) -> None:
        self.category_product_count[category_id].set(float(count))

    def snapshot(self) -> dict:
        """Retorna todas as métricas como dicionário aninhado."""
        category_histograms = {
            cid: h.snapshot()
            for cid, h in self.category_duration_seconds.items()
        }
        category_counts = {
            cid: g.value
            for cid, g in self.category_product_count.items()
        }

        return {
            "uptime_seconds": round(self.uptime_seconds, 1),
            "counters": {
                "collections_total": self.collections_total.value,
                "collection_errors_total": self.collection_errors_total.value,
                "products_collected_total": self.products_collected_total.value,
                "events_generated_total": self.events_generated_total.value,
                "requests_total": self.requests_total.value,
                "scans_total": self.scans_total.value,
                "recommendations_total": self.recommendations_total.value,
                "signals_total": self.signals_total.value,
                "reports_total": self.reports_total.value,
            },
            "gauges": {
                "collections_active": self.collections_active.value,
                "requests_in_flight": self.requests_in_flight.value,
                "db_pool_size": self.db_pool_size.value,
            },
            "categories": {
                "product_count": category_counts,
                "duration_seconds": category_histograms,
            },
        }

    def reset_all(self) -> None:
        self.collections_total.reset()
        self.collection_errors_total.reset()
        self.products_collected_total.reset()
        self.events_generated_total.reset()
        self.requests_total.reset()
        self.scans_total.reset()
        self.recommendations_total.reset()
        self.signals_total.reset()
        self.reports_total.reset()
        self.collections_active.reset()
        self.requests_in_flight.reset()
        self.db_pool_size.reset()
        for g in self.category_product_count.values():
            g.reset()
        for h in self.category_duration_seconds.values():
            h.reset()
        self._start_time = time.monotonic()


# Singleton
platform_metrics = PlatformMetrics()
