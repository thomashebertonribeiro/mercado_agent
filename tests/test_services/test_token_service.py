"""
tests/test_services/test_token_service.py

Testes unitarios do TokenService.

Coberturas:
  1. get_valid_token: retorna token quando valido
  2. get_valid_token: faz refresh quando token esta expirando
  3. get_valid_token: raises LookupError quando conta nao existe
  4. exchange_code: troca code por access_token
  5. exchange_code: raises ValueError para marketplace nao suportado
  6. list_accounts: retorna contas formatadas
  7. disconnect: remove conta existente
  8. build_authorization_url: gera URL correta
  9. build_authorization_url: inclui code_challenge quando fornecido
"""

import datetime
from datetime import timezone, timedelta
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.token_service import TokenService, MARKETPLACE_CONFIGS, TOKEN_REFRESH_THRESHOLD_SECONDS


def _make_account(
    expires_in_seconds: int = 21600,
    marketplace: str = "mercadolivre",
    user_id: int = 123456789,
    nickname: str = "vendedor_teste",
):
    acc = MagicMock()
    acc.marketplace = marketplace
    acc.user_id = user_id
    acc.nickname = nickname
    acc.country = "MLB"
    acc.access_token = "valid-access-token"
    acc.refresh_token = "valid-refresh-token"
    acc.scope = "read write"
    acc.expires_at = datetime.datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    acc.updated_at = datetime.datetime.now(timezone.utc)
    return acc


def _make_service(mock_repo=None):
    session = AsyncMock()
    service = TokenService(session)
    if mock_repo:
        service._repo = mock_repo
    return service, session


class TestGetValidToken:
    @pytest.mark.asyncio
    async def test_returns_token_when_not_expiring(self):
        mock_repo = AsyncMock()
        account = _make_account(expires_in_seconds=3600)
        mock_repo.get_by_marketplace_and_user.return_value = account

        service, _ = _make_service(mock_repo)
        token = await service.get_valid_token("mercadolivre", 123456789)

        assert token == "valid-access-token"
        mock_repo.get_by_marketplace_and_user.assert_called_once_with("mercadolivre", 123456789)

    @pytest.mark.asyncio
    async def test_refreshes_when_expiring_soon(self):
        mock_repo = AsyncMock()
        account = _make_account(expires_in_seconds=60)
        mock_repo.get_by_marketplace_and_user.return_value = account

        service, session = _make_service(mock_repo)

        refreshed_data = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 21600,
        }
        with patch.object(service, "_request_token", AsyncMock(return_value=refreshed_data)):
            with patch("services.token_service.settings") as mock_settings:
                mock_settings.ML_CLIENT_ID = "client_id"
                mock_settings.ML_CLIENT_SECRET = "client_secret"

                token = await service.get_valid_token("mercadolivre", 123456789)

        assert token == "new-access-token"
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_when_account_not_found(self):
        mock_repo = AsyncMock()
        mock_repo.get_by_marketplace_and_user.return_value = None

        service, _ = _make_service(mock_repo)

        with pytest.raises(LookupError, match="nao encontrada"):
            await service.get_valid_token("mercadolivre", 999999)


class TestExchangeCode:
    @pytest.mark.asyncio
    async def test_exchanges_code_and_creates_account(self):
        mock_repo = AsyncMock()
        mock_repo.upsert.return_value = _make_account()

        service, session = _make_service(mock_repo)

        token_response = {
            "access_token": "fresh-access-token",
            "refresh_token": "fresh-refresh-token",
            "expires_in": 21600,
            "user_id": 987654321,
        }
        user_profile = {"nickname": "novo_vendedor", "site_id": "MLB"}

        with patch.object(service, "_request_token", AsyncMock(return_value=token_response)):
            with patch.object(service, "_fetch_user_profile", AsyncMock(return_value=user_profile)):
                with patch("services.token_service.settings") as mock_settings:
                    mock_settings.ML_CLIENT_ID = "client_id"
                    mock_settings.ML_CLIENT_SECRET = "client_secret"
                    mock_settings.ML_REDIRECT_URI = "http://localhost/callback"

                    account = await service.exchange_code("mercadolivre", "auth-code-abc")

        mock_repo.upsert.assert_called_once()
        call_kwargs = mock_repo.upsert.call_args[1]
        assert call_kwargs["access_token"] == "fresh-access-token"
        assert call_kwargs["user_id"] == 987654321
        assert call_kwargs["nickname"] == "novo_vendedor"

    @pytest.mark.asyncio
    async def test_raises_for_unsupported_marketplace(self):
        service, _ = _make_service()

        with pytest.raises(ValueError, match="nao suportado"):
            await service.exchange_code("amazon", "auth-code")


class TestListAccounts:
    @pytest.mark.asyncio
    async def test_returns_formatted_accounts(self):
        mock_repo = AsyncMock()
        account = _make_account(expires_in_seconds=3600)
        mock_repo.get_all.return_value = [account]

        service, _ = _make_service(mock_repo)
        result = await service.list_accounts()

        assert len(result) == 1
        assert result[0]["marketplace"] == "mercadolivre"
        assert result[0]["user_id"] == 123456789
        assert result[0]["nickname"] == "vendedor_teste"
        assert result[0]["token_valid"] is True


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_deletes_existing_account(self):
        mock_repo = AsyncMock()
        mock_repo.delete.return_value = True

        service, _ = _make_service(mock_repo)
        result = await service.disconnect("mercadolivre", 123456789)

        assert result is True
        mock_repo.delete.assert_called_once_with("mercadolivre", 123456789)

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        mock_repo = AsyncMock()
        mock_repo.delete.return_value = False

        service, _ = _make_service(mock_repo)
        result = await service.disconnect("mercadolivre", 999999)

        assert result is False


class TestBuildAuthorizationUrl:
    def test_contains_client_id(self):
        with patch("services.token_service.settings") as mock_settings:
            mock_settings.ML_CLIENT_ID = "test_client_42"
            mock_settings.ML_REDIRECT_URI = "http://localhost/callback"

            url = TokenService.build_authorization_url("mercadolivre")

        assert "test_client_42" in url
        assert "response_type=code" in url
        assert "redirect_uri=" in url

    def test_includes_code_challenge(self):
        with patch("services.token_service.settings") as mock_settings:
            mock_settings.ML_CLIENT_ID = "test_client_42"
            mock_settings.ML_REDIRECT_URI = "http://localhost/callback"

            url = TokenService.build_authorization_url("mercadolivre", code_challenge="abc123")

        assert "code_challenge=abc123" in url
        assert "code_challenge_method=S256" in url

    def test_raises_for_unsupported_marketplace(self):
        with pytest.raises(ValueError, match="nao suportado"):
            TokenService.build_authorization_url("amazon")
