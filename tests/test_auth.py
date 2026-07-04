"""
tests/test_auth.py

Testes unitários do MLAuthService.

Coberturas:
  1. build_authorization_url: gera URL correta com CLIENT_ID
  2. get_valid_token: retorna token diretamente quando ainda válido
  3. get_valid_token: dispara refresh quando token está próximo de expirar
  4. get_valid_token: lança LookupError quando user_id não existe
  5. exchange_code_for_token: cria conta no banco ao receber um code válido
  6. disconnect: remove conta existente; retorna False para inexistente
"""

import datetime
from datetime import timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.auth import MLAuthService, TOKEN_REFRESH_THRESHOLD_SECONDS
from models.ml_account import MLAccount


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #

def _make_account(
    expires_in_seconds: int = 21600,  # 6 horas — token saudável por padrão
    user_id: int = 123456789,
    nickname: str = "vendedor_teste",
) -> MLAccount:
    """Cria um MLAccount falso com o tempo de expiração configurável."""
    acc = MagicMock(spec=MLAccount)
    acc.user_id = user_id
    acc.nickname = nickname
    acc.country = "MLB"
    acc.access_token = "valid-access-token"
    acc.refresh_token = "valid-refresh-token"
    # Use naive UTC datetime to match the service's utcnow() usage
    acc.expires_at = datetime.datetime.utcnow() + datetime.timedelta(
        seconds=expires_in_seconds
    )
    acc.updated_at = datetime.datetime.utcnow()
    return acc


def _make_service(mock_repo=None) -> tuple[MLAuthService, MagicMock]:
    """Cria um MLAuthService com sessão e repositório mockados."""
    session = AsyncMock()
    service = MLAuthService(session)
    if mock_repo:
        service._repo = mock_repo
    return service, session


# --------------------------------------------------------------------------- #
# 1. build_authorization_url                                                  #
# --------------------------------------------------------------------------- #

def test_build_authorization_url_contains_client_id() -> None:
    """URL de autorização deve conter o CLIENT_ID configurado."""
    service, _ = _make_service()

    with patch("services.auth.settings") as mock_settings:
        mock_settings.ML_CLIENT_ID = "test_client_id_42"
        mock_settings.ML_REDIRECT_URI = "http://localhost:8000/mercadolivre/callback"

        url = service.build_authorization_url()

    assert "test_client_id_42" in url
    assert "response_type=code" in url
    assert "redirect_uri=" in url


def test_build_authorization_url_raises_when_client_id_missing() -> None:
    """Deve levantar ValueError quando ML_CLIENT_ID não está configurado."""
    service, _ = _make_service()

    with patch("services.auth.settings") as mock_settings:
        mock_settings.ML_CLIENT_ID = ""
        mock_settings.ML_REDIRECT_URI = "http://localhost:8000/callback"

        with pytest.raises(ValueError, match="ML_CLIENT_ID"):
            service.build_authorization_url()


# --------------------------------------------------------------------------- #
# 2. get_valid_token — token saudável, sem refresh                            #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_get_valid_token_returns_directly_when_not_expiring() -> None:
    """
    Quando o token ainda é válido (expira em mais de 5 min),
    deve retornar o token sem chamar refresh.
    """
    mock_repo = AsyncMock()
    account = _make_account(expires_in_seconds=3600)  # expira em 1h
    mock_repo.get_by_user_id.return_value = account

    service, _ = _make_service(mock_repo)

    token = await service.get_valid_token(user_id=account.user_id)

    assert token == "valid-access-token"
    # Não deve ter chamado _refresh_account_token — repo só foi lido, não atualizado
    mock_repo.get_by_user_id.assert_called_once_with(account.user_id)


# --------------------------------------------------------------------------- #
# 3. get_valid_token — token expirando, deve fazer refresh                    #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_get_valid_token_refreshes_when_expiring_soon() -> None:
    """
    Quando o token expira em menos de TOKEN_REFRESH_THRESHOLD_SECONDS,
    deve chamar o Refresh Grant e retornar o novo token.
    """
    mock_repo = AsyncMock()
    # Token com apenas 60 segundos restantes (abaixo do threshold de 300s)
    account = _make_account(expires_in_seconds=60)
    mock_repo.get_by_user_id.return_value = account

    service, session = _make_service(mock_repo)

    # Mock da chamada HTTP ao ML para o refresh
    refreshed_data = {
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "expires_in": 21600,
    }
    with patch.object(service, "_request_token", AsyncMock(return_value=refreshed_data)):
        with patch("services.auth.settings") as mock_settings:
            mock_settings.ML_CLIENT_ID = "client_id"
            mock_settings.ML_CLIENT_SECRET = "client_secret"
            mock_settings.ML_REDIRECT_URI = "http://localhost:8000/callback"

            token = await service.get_valid_token(user_id=account.user_id)

    assert token == "new-access-token"
    session.commit.assert_called_once()


# --------------------------------------------------------------------------- #
# 4. get_valid_token — user_id não cadastrado                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_get_valid_token_raises_when_account_not_found() -> None:
    """Deve levantar LookupError quando o user_id não está no banco."""
    mock_repo = AsyncMock()
    mock_repo.get_by_user_id.return_value = None

    service, _ = _make_service(mock_repo)

    with pytest.raises(LookupError, match="não encontrada"):
        await service.get_valid_token(user_id=999999)


# --------------------------------------------------------------------------- #
# 5. exchange_code_for_token — fluxo completo de callback                     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_exchange_code_creates_account_in_db() -> None:
    """
    Após receber um authorization code válido, deve criar a conta no banco
    com as credenciais corretas.
    """
    mock_repo = AsyncMock()
    mock_repo.get_by_user_id.return_value = None  # conta ainda não existe

    service, session = _make_service(mock_repo)

    token_response = {
        "access_token": "fresh-access-token",
        "refresh_token": "fresh-refresh-token",
        "expires_in": 21600,
        "user_id": 987654321,
    }
    user_profile_response = {
        "nickname": "novo_vendedor",
        "site_id": "MLB",
    }

    with patch.object(service, "_request_token", AsyncMock(return_value=token_response)):
        with patch.object(
            service, "_fetch_user_profile", AsyncMock(return_value=user_profile_response)
        ):
            with patch("services.auth.settings") as mock_settings:
                mock_settings.ML_CLIENT_ID = "client_id"
                mock_settings.ML_CLIENT_SECRET = "client_secret"
                mock_settings.ML_REDIRECT_URI = "http://localhost:8000/callback"

            with patch("repositories.marketplace_account.MarketplaceAccountRepository") as MockMPRepo:
                MockMPRepo.return_value.upsert = AsyncMock()
                account = await service.exchange_code_for_token("authorization-code-abc")

    # Conta deve ter sido adicionada à sessão
    session.add.assert_called_once()
    session.commit.assert_called()

    # Verifica que os dados foram atribuídos corretamente à conta
    added_account: MLAccount = session.add.call_args[0][0]
    assert added_account.access_token == "fresh-access-token"
    assert added_account.refresh_token == "fresh-refresh-token"
    assert added_account.user_id == 987654321
    assert added_account.nickname == "novo_vendedor"
    assert added_account.country == "MLB"


# --------------------------------------------------------------------------- #
# 6. disconnect                                                                #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_disconnect_removes_existing_account() -> None:
    """Deve retornar True e remover conta quando user_id existe."""
    mock_repo = AsyncMock()
    mock_repo.delete_by_user_id.return_value = True

    service, _ = _make_service(mock_repo)

    result = await service.disconnect(user_id=123456789)

    assert result is True
    mock_repo.delete_by_user_id.assert_called_once_with(123456789)


@pytest.mark.asyncio
async def test_disconnect_returns_false_when_not_found() -> None:
    """Deve retornar False quando o user_id não existe no banco."""
    mock_repo = AsyncMock()
    mock_repo.delete_by_user_id.return_value = False

    service, _ = _make_service(mock_repo)

    result = await service.disconnect(user_id=999999)

    assert result is False
