"""
collector/metrics.py

Coleta métricas de execução do IntelligentCollector para um único job.

Rastreia contadores (requests, events, erros, etc.), duração total,
duração por página (para modos paginados como by_category), e uso de memória
no início e fim do job via psutil.

Uso:
    metrics = MetricsCollector()
    metrics.start()
    # ... executa coleta ...
    metrics.finish()
    job_repo.set_done(job, metrics)

Feature: intelligent-collector
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional


def _sample_rss_mb() -> Optional[float]:
    """Retorna RSS do processo atual em MB, ou None se psutil não disponível."""
    try:
        import psutil
        return round(psutil.Process().memory_info().rss / 1_048_576, 2)
    except Exception:
        return None


@dataclass
class MetricsCollector:
    """
    Contadores de execução para um CollectionJob.

    Campos públicos (incrementados pelo IntelligentCollector):
        requests        : total de requisições HTTP feitas à API
        products        : total de produtos processados com sucesso
        errors          : total de produtos que falharam
        changes         : total de produtos onde mudanças foram detectadas
        events_generated: total de linhas inseridas nas tabelas de eventos
        skipped         : total de produtos sem mudança (hash match)
        duration_seconds: calculado por finish()

    Campos de memória (opcionais — requerem psutil):
        memory_rss_start_mb: RSS em MB no início do job
        memory_rss_end_mb  : RSS em MB ao final do job
    """

    requests: int = 0
    products: int = 0
    errors: int = 0
    changes: int = 0
    events_generated: int = 0
    skipped: int = 0
    duration_seconds: int = 0

    memory_rss_start_mb: Optional[float] = field(default=None, repr=False)
    memory_rss_end_mb: Optional[float] = field(default=None, repr=False)

    _start_time: float = field(default=0.0, init=False, repr=False)
    _page_durations: List[float] = field(default_factory=list, init=False, repr=False)

    def start(self) -> None:
        """Registra o início do job e amostra a memória inicial."""
        self._start_time = time.monotonic()
        self.memory_rss_start_mb = _sample_rss_mb()

    def finish(self) -> None:
        """Calcula duration_seconds e amostra a memória final."""
        self.duration_seconds = int(time.monotonic() - self._start_time)
        self.memory_rss_end_mb = _sample_rss_mb()

    def record_page_duration(self, seconds: float) -> None:
        """Registra a duração de uma página de resultados (modo by_category)."""
        self._page_durations.append(seconds)

    @property
    def avg_seconds_per_page(self) -> Optional[float]:
        """
        Média de duração por página de resultados.
        Retorna None se nenhuma página foi registrada.
        """
        if not self._page_durations:
            return None
        return sum(self._page_durations) / len(self._page_durations)

    def to_extra_metrics(self) -> dict:
        """
        Serializa métricas extras para JSONB em CollectionJob.extra_metrics.
        Sempre retorna os três campos — None é serializado como null em JSON.
        """
        return {
            "avg_seconds_per_page": self.avg_seconds_per_page,
            "memory_rss_start_mb": self.memory_rss_start_mb,
            "memory_rss_end_mb": self.memory_rss_end_mb,
        }
