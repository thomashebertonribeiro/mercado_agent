"""
api/routers/mercadolivre.py

Endpoints OAuth 2.0 do Mercado Livre.

  GET  /mercadolivre/connect              → Redireciona ao login do ML
  GET  /mercadolivre/callback             → Recebe o code e troca pelo token
  GET  /mercadolivre/status               → Lista contas conectadas e validade
  DELETE /mercadolivre/disconnect/{uid}   → Remove credenciais de uma conta
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from services.auth import MLAuthService
from utils.logger import logger

router = APIRouter(prefix="/mercadolivre", tags=["Mercado Livre Auth"])


# --------------------------------------------------------------------------- #
# GET /mercadolivre/connect                                                    #
# --------------------------------------------------------------------------- #

@router.get(
    "/connect",
    summary="Iniciar autenticação OAuth 2.0 com o Mercado Livre",
    response_description="Redireciona para a página de login do Mercado Livre",
)
async def connect(
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """
    Gera a URL de autorização do Mercado Livre e redireciona o usuário para ela.

    Após o login, o Mercado Livre redirecionará de volta para `/mercadolivre/callback`
    com um parâmetro `code` que será trocado pelo access_token.
    """
    try:
        auth_service = MLAuthService(db)
        url = auth_service.build_authorization_url()
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# --------------------------------------------------------------------------- #
# GET /mercadolivre/callback                                                   #
# --------------------------------------------------------------------------- #

@router.get(
    "/callback",
    summary="Callback OAuth 2.0 — recebe o code e armazena o token",
)
async def callback(
    code: str = Query(..., description="Authorization code recebido do Mercado Livre"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Endpoint de callback OAuth 2.0.

    O Mercado Livre redireciona aqui após o login com um parâmetro `code`.
    Este endpoint troca o code pelo access_token + refresh_token e armazena
    as credenciais de forma segura na tabela `ml_accounts`.

    **Não é necessário chamar este endpoint manualmente** — ele é invocado
    automaticamente pelo Mercado Livre após o fluxo de login.
    """
    try:
        auth_service = MLAuthService(db)
        account = await auth_service.exchange_code_for_token(code)
        return {
            "message": "Conta autenticada com sucesso.",
            "user_id": account.user_id,
            "nickname": account.nickname,
            "country": account.country,
            "expires_at": account.expires_at.isoformat(),
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Erro no callback OAuth: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Falha ao trocar o código de autorização com o Mercado Livre. "
                   "Verifique as credenciais e tente novamente.",
        )


# --------------------------------------------------------------------------- #
# GET /mercadolivre/status                                                     #
# --------------------------------------------------------------------------- #

@router.get(
    "/status",
    summary="Listar contas conectadas e estado dos tokens",
)
async def status_accounts(
    db: AsyncSession = Depends(get_db_session),
) -> list:
    """
    Retorna todas as contas do Mercado Livre conectadas ao sistema.

    Para cada conta, informa:
    - `user_id` e `nickname`
    - `country` (site_id do ML, ex: "MLB" para Brasil)
    - `expires_at` (quando o access_token expira)
    - `token_valid` (True se ainda válido)
    - `seconds_until_expiry` (segundos restantes até expirar)
    """
    auth_service = MLAuthService(db)
    return await auth_service.list_accounts()


# --------------------------------------------------------------------------- #
# DELETE /mercadolivre/disconnect/{user_id}                                    #
# --------------------------------------------------------------------------- #

@router.delete(
    "/disconnect/{user_id}",
    summary="Desconectar uma conta do Mercado Livre",
    status_code=status.HTTP_200_OK,
)
async def disconnect(
    user_id: int,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Remove as credenciais OAuth de uma conta do Mercado Livre do banco de dados.

    Após a desconexão, a conta precisará ser autenticada novamente via `/mercadolivre/connect`
    para que os coletores possam usar endpoints autenticados.
    """
    auth_service = MLAuthService(db)
    deleted = await auth_service.disconnect(user_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conta com user_id={user_id} não encontrada.",
        )

    return {"message": f"Conta user_id={user_id} desconectada com sucesso."}
