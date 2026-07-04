"""
services/auth.py

Serviço central de autenticação OAuth 2.0 do Mercado Livre.

Responsabilidades:
  - Gerar URL de autorização (Connect)
  - Trocar authorization code por access_token + refresh_token (Callback)
  - Verificar expiração e renovar o token automaticamente (Refresh)
  - Retornar token válido para qualquer módulo do sistema
  - Revogar / remover credenciais (Disconnect)

Nenhum módulo deve acessar o banco diretamente para tokens.
Sempre solicite ao MLAuthService.
"""

import datetime
from datetime import timezone
from typing import Optional
import hashlib
import base64
import secrets
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from models.ml_account import MLAccount
from repositories.ml_account import MLAccountRepository
from utils.logger import logger

# Mercado Livre OAuth 2.0 base URL
ML_AUTH_BASE = "https://auth.mercadolibre.com"  # Use .com (not .com.br)
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_ME_URL    = "https://api.mercadolibre.com/users/me"

# Renovar token se restarem menos de 5 minutos para expirar
TOKEN_REFRESH_THRESHOLD_SECONDS = 300

# PKCE state storage (in-memory, keyed by state parameter)
_pkce_state: dict[str, str] = {}  # state -> code_verifier


class MLAuthService:
    """
    Serviço de autenticação OAuth 2.0 do Mercado Livre.

    Uso correto:
        async with AsyncSessionLocal() as session:
            auth = MLAuthService(session)
            token = await auth.get_valid_token(user_id=123456789)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MLAccountRepository(session)

    @staticmethod
    def _generate_code_verifier() -> str:
        """Generate a random code verifier for PKCE (43-128 chars)."""
        return secrets.token_urlsafe(64)[:128]

    @staticmethod
    def _generate_code_challenge(verifier: str) -> str:
        """Generate S256 code challenge from verifier."""
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    # ------------------------------------------------------------------ #
    # 1. URL de Autorização                                               #
    # ------------------------------------------------------------------ #

    def build_authorization_url(self) -> str:
        """
        Gera a URL de login do Mercado Livre para iniciar o fluxo OAuth 2.0.

        Redirecione o usuário para esta URL.
        Após o login, o ML redirecionará para ML_REDIRECT_URI com um 'code'.
        """
        if not settings.ML_CLIENT_ID:
            raise ValueError(
                "ML_CLIENT_ID não está configurado. "
                "Defina a variável de ambiente ML_CLIENT_ID."
            )
        if not settings.ML_REDIRECT_URI:
            raise ValueError(
                "ML_REDIRECT_URI não está configurado. "
                "Defina a variável de ambiente ML_REDIRECT_URI."
            )

        url = (
            f"{ML_AUTH_BASE}/authorization"
            f"?response_type=code"
            f"&client_id={settings.ML_CLIENT_ID}"
            f"&redirect_uri={settings.ML_REDIRECT_URI}"
        )

        # Add PKCE (required by ML) with state for persistence
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        state = secrets.token_urlsafe(32)
        _pkce_state[state] = code_verifier
        url += f"&code_challenge={code_challenge}&code_challenge_method=S256&state={state}"

        logger.info(f"Authorization URL gerada: {url}")
        return url

    # ------------------------------------------------------------------ #
    # 2. Callback: Troca o code pelo token                                #
    # ------------------------------------------------------------------ #

    async def exchange_code_for_token(self, code: str, state: str = "") -> MLAccount:
        """
        Recebe o authorization code do callback do Mercado Livre,
        solicita o access_token + refresh_token e armazena no banco.

        Raises:
            ValueError: Se as credenciais não estiverem configuradas.
            httpx.HTTPStatusError: Se a requisição ao ML falhar.
        """
        self._validate_credentials()

        payload = {
            "grant_type": "authorization_code",
            "client_id": settings.ML_CLIENT_ID,
            "client_secret": settings.ML_CLIENT_SECRET,
            "code": code,
            "redirect_uri": settings.ML_REDIRECT_URI,
        }

        # Retrieve PKCE code_verifier from state storage
        code_verifier = _pkce_state.pop(state, None)
        if code_verifier:
            payload["code_verifier"] = code_verifier
            logger.debug(f"PKCE code_verifier recuperado para state={state[:8]}...")

        token_data = await self._request_token(payload)
        account = await self._upsert_account(token_data)
        logger.info(
            f"Conta '{account.nickname}' (user_id={account.user_id}) "
            f"autenticada com sucesso."
        )
        return account

    # ------------------------------------------------------------------ #
    # 3. Token válido (com auto-refresh)                                  #
    # ------------------------------------------------------------------ #

    async def get_valid_token(self, user_id: int) -> str:
        """
        Retorna um access_token válido para o user_id informado.

        - Se o token ainda for válido (expira em mais de 5 min), retorna diretamente.
        - Se estiver próximo de expirar ou expirado, executa o refresh grant e
          salva os novos tokens no banco antes de retornar.

        Raises:
            LookupError: Se o user_id não estiver cadastrado no banco.
            httpx.HTTPStatusError: Se o refresh falhar no Mercado Livre.
        """
        account = await self._repo.get_by_user_id(user_id)
        if not account:
            raise LookupError(
                f"Conta ML com user_id={user_id} não encontrada. "
                "Conecte a conta em /mercadolivre/connect primeiro."
            )

        if self._is_token_expiring(account):
            logger.info(
                f"Token de '{account.nickname}' expira em breve. "
                "Iniciando renovação automática..."
            )
            account = await self._refresh_account_token(account)

        return account.access_token

    # ------------------------------------------------------------------ #
    # 4. Refresh manual                                                   #
    # ------------------------------------------------------------------ #

    async def refresh_token(self, user_id: int) -> MLAccount:
        """
        Força a renovação do token independente da expiração.
        Útil para chamadas manuais via admin ou diagnóstico.
        """
        account = await self._repo.get_by_user_id(user_id)
        if not account:
            raise LookupError(f"Conta ML com user_id={user_id} não encontrada.")

        account = await self._refresh_account_token(account)
        logger.info(f"Token de '{account.nickname}' renovado manualmente.")
        return account

    # ------------------------------------------------------------------ #
    # 5. Status de contas                                                 #
    # ------------------------------------------------------------------ #

    async def list_accounts(self) -> list[dict]:
        """
        Lista todas as contas conectadas com informações de validade do token.
        """
        accounts = await self._repo.get_all_accounts()
        now = datetime.datetime.utcnow()
        result = []
        for acc in accounts:
            seconds_remaining = (acc.expires_at - now).total_seconds()
            result.append({
                "user_id": acc.user_id,
                "nickname": acc.nickname,
                "country": acc.country,
                "expires_at": acc.expires_at.isoformat(),
                "token_valid": seconds_remaining > 0,
                "seconds_until_expiry": max(0, int(seconds_remaining)),
            })
        return result

    # ------------------------------------------------------------------ #
    # 6. Disconnect                                                       #
    # ------------------------------------------------------------------ #

    async def disconnect(self, user_id: int) -> bool:
        """
        Remove as credenciais de um conta do banco de dados.

        Returns:
            True se removido com sucesso, False se a conta não existia.
        """
        deleted = await self._repo.delete_by_user_id(user_id)
        if deleted:
            logger.info(f"Conta ML user_id={user_id} desconectada com sucesso.")
        else:
            logger.warning(f"Tentativa de desconectar user_id={user_id} que não existe.")
        return deleted

    # ------------------------------------------------------------------ #
    # Métodos internos                                                    #
    # ------------------------------------------------------------------ #

    def _validate_credentials(self) -> None:
        """Garante que as variáveis de ambiente obrigatórias estão configuradas."""
        if not settings.ML_CLIENT_ID:
            raise ValueError("ML_CLIENT_ID não está configurado.")
        if not settings.ML_CLIENT_SECRET:
            raise ValueError("ML_CLIENT_SECRET não está configurado.")
        if not settings.ML_REDIRECT_URI:
            raise ValueError("ML_REDIRECT_URI não está configurado.")

    def _is_token_expiring(self, account: MLAccount) -> bool:
        """Retorna True se o token expira em menos de TOKEN_REFRESH_THRESHOLD_SECONDS."""
        now = datetime.datetime.utcnow()
        remaining = (account.expires_at - now).total_seconds()
        return remaining < TOKEN_REFRESH_THRESHOLD_SECONDS

    async def _refresh_account_token(self, account: MLAccount) -> MLAccount:
        """Executa o Refresh Grant e atualiza a conta no banco."""
        self._validate_credentials()

        payload = {
            "grant_type": "refresh_token",
            "client_id": settings.ML_CLIENT_ID,
            "client_secret": settings.ML_CLIENT_SECRET,
            "refresh_token": account.refresh_token,
        }

        token_data = await self._request_token(payload)

        # Atualiza os campos da conta existente em vez de criar uma nova linha
        account.access_token = token_data["access_token"]
        account.refresh_token = token_data.get("refresh_token", account.refresh_token)
        account.expires_at = self._compute_expires_at(token_data["expires_in"])
        account.updated_at = datetime.datetime.now(timezone.utc).replace(tzinfo=None)

        await self._session.commit()
        await self._session.refresh(account)
        logger.debug(
            f"Token renovado para '{account.nickname}'. "
            f"Novo expires_at: {account.expires_at}"
        )
        return account

    async def _request_token(self, payload: dict) -> dict:
        """
        Requisição HTTP ao endpoint de token do Mercado Livre.
        Raises httpx.HTTPStatusError em caso de falha.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(ML_TOKEN_URL, data=payload)
            if response.status_code != 200:
                logger.error(
                    f"Falha ao obter token ML. "
                    f"Status: {response.status_code} | Body: {response.text}"
                )
            response.raise_for_status()
            return response.json()

    async def _upsert_account(self, token_data: dict) -> MLAccount:
        """
        Cria ou atualiza a conta ML no banco a partir da resposta do token endpoint.
        Busca os dados do usuário na API /users/me para obter nickname e country.
        """
        access_token = token_data["access_token"]
        user_id = token_data["user_id"]

        # Busca dados do perfil do usuário na API do ML
        user_profile = await self._fetch_user_profile(access_token)
        nickname = user_profile.get("nickname", f"user_{user_id}")
        country = user_profile.get("site_id", "")

        expires_at = self._compute_expires_at(token_data["expires_in"])
        now = datetime.datetime.now(timezone.utc).replace(tzinfo=None)

        # Tenta encontrar conta existente (upsert)
        account = await self._repo.get_by_user_id(user_id)

        if account:
            account.access_token = access_token
            account.refresh_token = token_data["refresh_token"]
            account.expires_at = expires_at
            account.nickname = nickname
            account.country = country
            account.updated_at = now
        else:
            account = MLAccount(
                user_id=user_id,
                nickname=nickname,
                country=country,
                access_token=access_token,
                refresh_token=token_data["refresh_token"],
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
            )
            self._session.add(account)

        await self._session.commit()
        await self._session.refresh(account)

        # Sync to marketplace_accounts for SyncService/TokenService
        from repositories.marketplace_account import MarketplaceAccountRepository
        mp_repo = MarketplaceAccountRepository(self._session)
        await mp_repo.upsert(
            marketplace="mercadolivre",
            user_id=user_id,
            access_token=access_token,
            refresh_token=token_data["refresh_token"],
            expires_at=expires_at,
            nickname=nickname,
            country=country,
        )

        return account

    async def _fetch_user_profile(self, access_token: str) -> dict:
        """Busca o perfil do usuário autenticado via /users/me."""
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(ML_ME_URL, headers=headers)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _compute_expires_at(expires_in: int) -> datetime.datetime:
        """Converte expires_in (segundos) para um timestamp UTC absoluto (naive para PostgreSQL)."""
        return (datetime.datetime.now(timezone.utc) + datetime.timedelta(seconds=expires_in)).replace(tzinfo=None)
