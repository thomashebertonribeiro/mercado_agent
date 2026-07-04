"""
api/routers/auth.py

Endpoint de callback OAuth 2.0 para o Mercado Livre.

O ML redireciona para https://brasilices.tech/selldata/api/auth/callback
com os parâmetros code e state.
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from services.auth import MLAuthService
from utils.logger import logger

router = APIRouter(prefix="/api/auth", tags=["Auth Callback"])


@router.get("/callback")
async def auth_callback(
    code: str = Query(..., description="Authorization code do ML"),
    state: str = Query("", description="State parameter para PKCE"),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """
    Callback OAuth 2.0 — recebe o code do ML e troca pelo token.
    Retorna página HTML de sucesso/erro para o usuário.
    """
    try:
        auth_service = MLAuthService(db)
        account = await auth_service.exchange_code_for_token(code, state=state)

        html = f"""
        <html>
        <head><title>SellData - Autenticado</title></head>
        <body style="font-family: Arial; text-align: center; padding: 50px;">
            <h1>Conta conectada com sucesso!</h1>
            <p><strong>{account.nickname}</strong> (user_id: {account.user_id})</p>
            <p>Pais: {account.country}</p>
            <p>Token valido ate: {account.expires_at.strftime('%d/%m/%Y %H:%M')}</p>
            <hr>
            <p>Voltar ao <a href="/docs">painel da API</a></p>
            <script>setTimeout(() => window.close(), 5000);</script>
        </body>
        </html>
        """
        return HTMLResponse(content=html, status_code=200)

    except Exception as e:
        logger.error(f"Erro no callback OAuth: {e}")
        html = f"""
        <html>
        <head><title>SellData - Erro</title></head>
        <body style="font-family: Arial; text-align: center; padding: 50px;">
            <h1>Erro na autenticacao</h1>
            <p>{str(e)}</p>
            <p>Tente novamente via <a href="/mercadolivre/connect">conectar conta</a></p>
        </body>
        </html>
        """
        return HTMLResponse(content=html, status_code=400)
