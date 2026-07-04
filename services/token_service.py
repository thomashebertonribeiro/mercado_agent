"""
services/token_service.py

Servico centralizado de gestao de tokens OAuth para marketplaces.

Responsabilidades:
  - Verificar expiracao de tokens
  - Renovar automaticamente usando refresh_token
  - Nunca solicitar novo login enquanto o refresh_token for valido
  - Suporte a multi-marketplace (ML, Amazon, Shopee, etc.)
"""

import datetime
from datetime import timezone, timedelta
from typing import Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from repositories.marketplace_account import MarketplaceAccountRepository
from models.marketplace_account import MarketplaceAccount
from utils.logger import logger

TOKEN_REFRESH_THRESHOLD_SECONDS = 300  # 5 minutos

# Configuracoes por marketplace
MARKETPLACE_CONFIGS = {
    "mercadolivre": {
        "token_url": "https://api.mercadolibre.com/oauth/token",
        "me_url": "https://api.mercadolibre.com/users/me",
        "auth_base": "https://auth.mercadolivre.com.br",
    },
}


class TokenService:
    """
    Servico de gestao de tokens para marketplaces.

    Uso:
        async with AsyncSessionLocal() as session:
            token_svc = TokenService(session)
            token = await token_svc.get_valid_token("mercadolivre", user_id=123)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MarketplaceAccountRepository(session)

    async def get_valid_token(self, marketplace: str, user_id: int) -> str:
        """
        Retorna um access_token valido para o marketplace e user_id.

        Renova automaticamente se estiver proximo da expiracao.
        """
        account = await self._repo.get_by_marketplace_and_user(marketplace, user_id)
        if not account:
            raise LookupError(
                f"Conta {marketplace} user_id={user_id} nao encontrada. "
                "Conecte a conta primeiro."
            )

        if self._is_token_expiring(account):
            logger.info(f"Token {marketplace}/{user_id} expirando, renovando...")
            account = await self._refresh_token(account)

        return account.access_token

    async def exchange_code(
        self, marketplace: str, code: str, code_verifier: str | None = None
    ) -> MarketplaceAccount:
        """
        Troca authorization code por access_token + refresh_token.
        """
        config = MARKETPLACE_CONFIGS.get(marketplace)
        if not config:
            raise ValueError(f"Marketplace '{marketplace}' nao suportado.")

        payload = {
            "grant_type": "authorization_code",
            "client_id": settings.ML_CLIENT_ID,
            "client_secret": settings.ML_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.ML_REDIRECT_URI,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier

        token_data = await self._request_token(config["token_url"], payload)
        user_profile = await self._fetch_user_profile(
            config["me_url"], token_data["access_token"]
        )

        expires_at = datetime.datetime.now(timezone.utc) + timedelta(
            seconds=token_data["expires_in"]
        )

        account = await self._repo.upsert(
            marketplace=marketplace,
            user_id=token_data["user_id"],
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            expires_at=expires_at,
            nickname=user_profile.get("nickname"),
            country=user_profile.get("site_id"),
            scope=token_data.get("scope"),
        )

        logger.info(
            f"Conta {marketplace}/{account.user_id} ({account.nickname}) autenticada."
        )
        return account

    async def list_accounts(self, marketplace: str | None = None) -> list[dict]:
        accounts = await self._repo.get_all(marketplace)
        now = datetime.datetime.now(timezone.utc)
        return [
            {
                "marketplace": acc.marketplace,
                "user_id": acc.user_id,
                "nickname": acc.nickname,
                "country": acc.country,
                "expires_at": acc.expires_at.isoformat(),
                "token_valid": (acc.expires_at - now).total_seconds() > 0,
                "seconds_until_expiry": max(0, int((acc.expires_at - now).total_seconds())),
            }
            for acc in accounts
        ]

    async def disconnect(self, marketplace: str, user_id: int) -> bool:
        return await self._repo.delete(marketplace, user_id)

    # ── Internos ───────────────────────────────────────────────────────

    def _is_token_expiring(self, account: MarketplaceAccount) -> bool:
        now = datetime.datetime.now(timezone.utc)
        remaining = (account.expires_at - now).total_seconds()
        return remaining < TOKEN_REFRESH_THRESHOLD_SECONDS

    async def _refresh_token(self, account: MarketplaceAccount) -> MarketplaceAccount:
        config = MARKETPLACE_CONFIGS.get(account.marketplace)
        if not config:
            raise ValueError(f"Marketplace '{account.marketplace}' nao suportado.")

        payload = {
            "grant_type": "refresh_token",
            "client_id": settings.ML_CLIENT_ID,
            "client_secret": settings.ML_CLIENT_SECRET,
            "refresh_token": account.refresh_token,
        }

        token_data = await self._request_token(config["token_url"], payload)

        account.access_token = token_data["access_token"]
        account.refresh_token = token_data.get("refresh_token", account.refresh_token)
        account.expires_at = datetime.datetime.now(timezone.utc) + timedelta(
            seconds=token_data["expires_in"]
        )
        account.updated_at = datetime.datetime.now(timezone.utc)

        await self._session.commit()
        logger.debug(f"Token renovado para {account.marketplace}/{account.user_id}")
        return account

    async def _request_token(self, url: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, data=payload)
            if response.status_code != 200:
                logger.error(f"Falha ao obter token: {response.status_code} {response.text[:200]}")
            response.raise_for_status()
            return response.json()

    async def _fetch_user_profile(self, url: str, access_token: str) -> dict:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def build_authorization_url(marketplace: str, code_challenge: str | None = None) -> str:
        config = MARKETPLACE_CONFIGS.get(marketplace)
        if not config:
            raise ValueError(f"Marketplace '{marketplace}' nao suportado.")

        url = (
            f"{config['auth_base']}/authorization"
            f"?response_type=code"
            f"&client_id={settings.ML_CLIENT_ID}"
            f"&redirect_uri={settings.ML_REDIRECT_URI}"
        )
        if code_challenge:
            url += f"&code_challenge={code_challenge}&code_challenge_method=S256"
        return url
