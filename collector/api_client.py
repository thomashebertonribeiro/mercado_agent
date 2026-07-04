"""
collector/api_client.py

Cliente HTTP resiliente para a API oficial do Mercado Livre.

Suporta dois modos:
  - Público     : endpoints abertos (catálogo, items, sellers)
  - Autenticado : Bearer token via MLAuthService (pedidos, perguntas, etc.)

Retry manual com exponential back-off — sem Tenacity.
O loop de retry respeita RateLimiter para pausas globais de 429.

Funções utilitárias puras (testáveis sem instâncias):
  compute_backoff_wait(attempt, base, max_wait) -> float
  parse_retry_after(header_value, now) -> float

Feature: intelligent-collector
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

import httpx

from config.settings import settings
from utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Pure utility functions
# ─────────────────────────────────────────────────────────────────────────────

def compute_backoff_wait(attempt: int, base: float, max_wait: float) -> float:
    """
    Calcula o tempo de espera para tentativa `attempt` (1-indexed).

    Fórmula: min(base ** attempt, max_wait)
    Sem jitter — comportamento determinístico para testes.

    Property 10: Back-off formula is exact
    """
    return min(base ** attempt, max_wait)


def parse_retry_after(
    header_value: str,
    now: Optional[datetime] = None,
) -> float:
    """
    Interpreta o cabeçalho Retry-After do HTTP.

    Formatos suportados:
      - Inteiro em string (segundos): "30" → 30.0
      - HTTP-date RFC 7231:           "Wed, 21 Oct 2015 07:28:00 GMT" → delta em segundos

    Datas no passado retornam 0.0.
    `now` permite injeção de tempo para testes (padrão: datetime.now(UTC)).

    Property 11/12: Retry-After integer and HTTP-date parsing
    """
    if now is None:
        now = datetime.now(timezone.utc)

    header_value = header_value.strip()

    # Tenta parsing como inteiro primeiro
    try:
        return float(int(header_value))
    except ValueError:
        pass

    # Tenta parsing como HTTP-date
    try:
        future = parsedate_to_datetime(header_value)
        if future.tzinfo is None:
            future = future.replace(tzinfo=timezone.utc)
        delta = (future - now).total_seconds()
        return max(0.0, delta)
    except Exception:
        # Formato desconhecido — fallback seguro
        return 5.0


# ─────────────────────────────────────────────────────────────────────────────
# MercadoLivreAPICollector
# ─────────────────────────────────────────────────────────────────────────────

class MercadoLivreAPICollector:
    """
    Cliente HTTP resiliente para a API oficial do Mercado Livre.

    Modo público (sem auth):
        collector = MercadoLivreAPICollector()
        product = await collector.fetch_product("MLB123")

    Modo autenticado (com OAuth 2.0):
        collector = MercadoLivreAPICollector(auth_service=auth, user_id=123)
        orders = await collector.fetch_my_orders()

    Com RateLimiter (IntelligentCollector):
        result = await collector._make_request("GET", path, rate_limiter=limiter)
    """

    BASE_URL = "https://api.mercadolibre.com"

    def __init__(
        self,
        auth_service=None,          # MLAuthService | None
        user_id: Optional[int] = None,
    ) -> None:
        self._auth_service = auth_service
        self._user_id = user_id

        base_headers = {
            "User-Agent": settings.CRAWLER_USER_AGENT,
            "Accept": "application/json",
        }
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        self._client_kwargs: Dict[str, Any] = {
            "headers": base_headers,
            "timeout": httpx.Timeout(10.0),
            "limits": limits,
            "follow_redirects": True,
        }
        if settings.PROXY_URL:
            self._client_kwargs["proxy"] = settings.PROXY_URL

    # ------------------------------------------------------------------ #
    # Método base de requisição (retry manual)                           #
    # ------------------------------------------------------------------ #

    async def _make_request(
        self,
        method: str,
        path: str,
        authenticated: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
        rate_limiter=None,          # RateLimiter | None
        max_retries: int = 5,
        backoff_base: float = 2.0,
        max_wait: float = 60.0,
    ) -> Dict[str, Any]:
        """
        Executa a requisição HTTP com loop de retry manual e exponential back-off.

        Estratégia de retry:
          - httpx.RequestError (erros de rede)          → retry com back-off
          - HTTP 429 Too Many Requests                  → parse Retry-After, pause_for ou sleep
          - HTTP 5xx Server Error                       → retry com back-off
          - HTTP 404 Not Found                          → raise imediato (sem retry)
          - Outros HTTP 4xx                             → raise imediato

        Após esgotar max_retries, re-levanta a última exceção.
        """
        headers = dict(self._client_kwargs.get("headers", {}))

        if authenticated:
            token = await self._get_auth_token()
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers:
            headers.update(extra_headers)

        client_kwargs = {**self._client_kwargs, "headers": headers}
        url = f"{self.BASE_URL}{path}"
        last_exc: Exception = RuntimeError("max_retries=0 configurado")

        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    response = await client.request(method, url)

                # HTTP 404 — não há lógica de retry para "não encontrado"
                if response.status_code == 404:
                    response.raise_for_status()

                # HTTP 429 — respeita Retry-After e pausa o RateLimiter global
                if response.status_code == 429:
                    header_val = response.headers.get("Retry-After", "5")
                    wait = parse_retry_after(header_val)
                    logger.warning(
                        "HTTP 429 recebido",
                        attempt=attempt,
                        wait_seconds=wait,
                        path=path,
                    )
                    if rate_limiter is not None:
                        await rate_limiter.pause_for(wait)
                    else:
                        await asyncio.sleep(wait)
                    last_exc = httpx.HTTPStatusError(
                        f"429 Too Many Requests",
                        request=response.request,
                        response=response,
                    )
                    continue

                # HTTP 5xx — retry com back-off
                if response.status_code >= 500:
                    response.raise_for_status()

                # Sucesso
                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as exc:
                # 404 → re-raise imediato sem retry
                if exc.response.status_code == 404:
                    raise
                last_exc = exc
                if attempt == max_retries:
                    raise

            except httpx.RequestError as exc:
                last_exc = exc
                if attempt == max_retries:
                    raise

            # Calcula espera antes do próximo retry
            wait = compute_backoff_wait(attempt, backoff_base, max_wait)
            logger.warning(
                "Retry após erro",
                attempt=attempt,
                max_retries=max_retries,
                wait_seconds=wait,
                path=path,
            )
            if rate_limiter is not None:
                await rate_limiter.pause_for(wait)
            else:
                await asyncio.sleep(wait)

        raise last_exc

    async def _get_auth_token(self) -> str:
        """Obtém Bearer token via MLAuthService — nunca hardcoded."""
        if not self._auth_service or not self._user_id:
            raise RuntimeError(
                "Endpoint autenticado requer auth_service e user_id no construtor."
            )
        return await self._auth_service.get_valid_token(self._user_id)

    # ------------------------------------------------------------------ #
    # Endpoints Públicos                                                  #
    # ------------------------------------------------------------------ #

    async def fetch_product(
        self,
        product_id: str,
        rate_limiter=None,
        max_retries: int = 5,
        backoff_base: float = 2.0,
        max_wait: float = 60.0,
    ) -> Optional[Dict[str, Any]]:
        """Consulta detalhes de um anúncio na API pública do ML."""
        try:
            logger.info("Buscando produto", product_id=product_id)
            return await self._make_request(
                "GET",
                f"/items/{product_id}",
                rate_limiter=rate_limiter,
                max_retries=max_retries,
                backoff_base=backoff_base,
                max_wait=max_wait,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("Produto não encontrado", product_id=product_id)
                return None
            logger.error("Erro HTTP ao buscar produto", product_id=product_id, status=e.response.status_code)
            raise
        except Exception as e:
            logger.error("Erro ao buscar produto", product_id=product_id, error=str(e))
            raise

    async def fetch_seller(self, seller_id: int) -> Optional[Dict[str, Any]]:
        """Consulta detalhes de um vendedor na API pública do ML."""
        try:
            logger.info("Buscando vendedor", seller_id=seller_id)
            return await self._make_request("GET", f"/users/{seller_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("Vendedor não encontrado", seller_id=seller_id)
                return None
            logger.error("Erro HTTP ao buscar vendedor", seller_id=seller_id, status=e.response.status_code)
            raise
        except Exception as e:
            logger.error("Erro ao buscar vendedor", seller_id=seller_id, error=str(e))
            raise

    async def search_products(
        self, query: str, limit: int = 50
    ) -> Optional[Dict[str, Any]]:
        """Busca produtos no catálogo público do ML por texto."""
        try:
            return await self._make_request(
                "GET", f"/sites/MLB/search?q={query}&limit={limit}"
            )
        except Exception as e:
            logger.error("Erro ao buscar produtos", query=query, error=str(e))
            raise

    async def search_by_category(
        self,
        category_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> Optional[Dict[str, Any]]:
        """
        Busca produtos de uma categoria específica com paginação.

        Retorna None em 404 (categoria inexistente) em vez de levantar exceção.
        Usado pelo IntelligentCollector no modo by_category para paginar resultados.
        """
        try:
            return await self._make_request(
                "GET",
                f"/sites/MLB/search?category={category_id}&offset={offset}&limit={limit}",
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("Categoria não encontrada", category_id=category_id)
                return None
            raise
        except Exception as e:
            logger.error("Erro ao buscar por categoria", category_id=category_id, error=str(e))
            raise

    # ------------------------------------------------------------------ #
    # Endpoints Autenticados (OAuth 2.0)                                 #
    # ------------------------------------------------------------------ #

    async def fetch_my_orders(self, limit: int = 50) -> Optional[Dict[str, Any]]:
        """Pedidos do vendedor autenticado. Requer auth_service + user_id."""
        try:
            return await self._make_request(
                "GET",
                f"/orders/search?seller={self._user_id}&limit={limit}",
                authenticated=True,
            )
        except Exception as e:
            logger.error("Erro ao buscar pedidos", error=str(e))
            raise

    async def fetch_my_questions(self, limit: int = 50) -> Optional[Dict[str, Any]]:
        """Perguntas recebidas pelo vendedor autenticado."""
        try:
            return await self._make_request(
                "GET",
                f"/questions/search?seller_id={self._user_id}&limit={limit}",
                authenticated=True,
            )
        except Exception as e:
            logger.error("Erro ao buscar perguntas", error=str(e))
            raise

    async def fetch_my_items(self, limit: int = 50) -> Optional[Dict[str, Any]]:
        """Anúncios do vendedor autenticado."""
        try:
            return await self._make_request(
                "GET",
                f"/users/{self._user_id}/items/search?limit={limit}",
                authenticated=True,
            )
        except Exception as e:
            logger.error("Erro ao buscar anúncios", error=str(e))
            raise

    async def fetch_authenticated_user_profile(self) -> Optional[Dict[str, Any]]:
        """Perfil completo via /users/me. Requer auth_service + user_id."""
        try:
            return await self._make_request("GET", "/users/me", authenticated=True)
        except Exception as e:
            logger.error("Erro ao buscar perfil autenticado", error=str(e))
            raise
