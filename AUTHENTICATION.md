q# Guia de Autenticação: OAuth 2.0 com o Mercado Livre

Este guia explica como registrar uma aplicação no Mercado Livre, configurar as credenciais no sistema e usar os endpoints de autenticação OAuth 2.0.

---

## 1. Registrar sua Aplicação no Mercado Livre

### Passo 1 — Acesse o portal de desenvolvedores

Acesse: **https://developers.mercadolivre.com.br/pt_br/api-docs-en**

Clique em **"Criar aplicação"** (ou acesse diretamente o painel de apps: https://developers.mercadolivre.com.br/pt_br/gestao-de-aplicacoes).

### Passo 2 — Preencha os dados da aplicação

| Campo | Valor Recomendado |
|-------|------------------|
| **Nome** | `datalake-ml` |
| **Descrição** | Sistema de monitoramento de e-commerce |
| **Domínio** | `http://localhost:8000` (desenvolvimento) |
| **Redirect URI** | `https://brasilices.tech/selldata/api/auth/callback` |
| **Scopes** | `read`, `write`, `offline_access` |

> [!IMPORTANT]
> O escopo `offline_access` é **obrigatório** para receber o `refresh_token`. Sem ele, a sessão expirará após 6 horas e precisará ser refeita manualmente.

### Passo 3 — Copie as credenciais

Após criar a aplicação, o Mercado Livre exibirá:

- **App ID** → isso é o `ML_CLIENT_ID`
- **Secret key** → isso é o `ML_CLIENT_SECRET`

---

## 2. Configurar as Credenciais no Sistema

Edite o arquivo `.env` (copie do `.env.example` se necessário):

```bash
cp .env.example .env
```

Preencha as três variáveis obrigatórias:

```dotenv
# Mercado Livre App OAuth Credentials
ML_CLIENT_ID=1234567890          # App ID do seu app no ML
ML_CLIENT_SECRET=AbCdEfGhIjKlMn  # Secret key do seu app no ML
ML_REDIRECT_URI=https://brasilices.tech/selldata/api/auth/callback  # URL registrada no painel ML
```

> [!CAUTION]
> **Nunca** commite o arquivo `.env` no Git. Ele já está no `.gitignore`.
> Em produção, injete essas variáveis via Docker secrets, variáveis de ambiente do servidor, ou um cofre como AWS Secrets Manager / HashiCorp Vault.

### Para produção — URL de Redirect

Substitua `http://localhost:8000` pelo domínio real da sua aplicação:

```dotenv
ML_REDIRECT_URI=https://brasilices.tech/selldata/api/auth/callback
```

Esta URL já está configurada como padrão.

---

## 3. Fluxo OAuth 2.0 Completo

```
Usuário                    Sistema (API)           Mercado Livre
   |                           |                         |
   |-- GET /mercadolivre/connect -->                     |
   |                           |                         |
   |<-- 302 Redirect para ML --+-- Authorization URL --> |
   |                           |                         |
   |--------- Login no Mercado Livre -----------------> |
   |                           |                         |
   |<-- Redirect com ?code=XYZ ----------------------- |
   |                           |                         |
   |-- GET /mercadolivre/callback?code=XYZ -->          |
   |         (automático)      |                         |
   |                           |-- POST /oauth/token --> |
   |                           |<-- access_token --------+
   |                           |    refresh_token        |
   |                           |    expires_in           |
   |                           |                         |
   |                           |-- GET /users/me ------> |
   |                           |<-- nickname, site_id ---+
   |                           |                         |
   |                           |-- Salva em ml_accounts  |
   |                           |                         |
   |<-- {"message": "Conta autenticada"} -------------- |
```

---

## 4. Endpoints da API

### `GET /mercadolivre/connect`

Redireciona o navegador para a página de login do Mercado Livre.

**Uso:** Abra no navegador ou faça uma requisição que suporte redirecionamento.

```bash
# Abra no navegador:
http://localhost:8000/mercadolivre/connect
```

---

### `GET /mercadolivre/callback?code=<CODE>`

Recebido automaticamente pelo sistema após o login. Troca o `code` pelos tokens e armazena no banco.

**Resposta de sucesso:**
```json
{
  "message": "Conta autenticada com sucesso.",
  "user_id": 123456789,
  "nickname": "minha_loja_ml",
  "country": "MLB",
  "expires_at": "2026-07-03T05:00:00"
}
```

---

### `GET /mercadolivre/status`

Lista todas as contas conectadas e o estado de validade do token.

```bash
curl http://localhost:8000/mercadolivre/status
```

**Resposta:**
```json
[
  {
    "user_id": 123456789,
    "nickname": "minha_loja_ml",
    "country": "MLB",
    "expires_at": "2026-07-03T05:00:00",
    "token_valid": true,
    "seconds_until_expiry": 18432
  }
]
```

---

### `DELETE /mercadolivre/disconnect/{user_id}`

Remove as credenciais de uma conta. Após a remoção, será necessário refazer a autenticação.

```bash
curl -X DELETE http://localhost:8000/mercadolivre/disconnect/123456789
```

**Resposta:**
```json
{
  "message": "Conta user_id=123456789 desconectada com sucesso."
}
```

---

## 5. Renovação Automática de Tokens

O `access_token` do Mercado Livre expira após **6 horas** (`expires_in: 21600`).

O sistema renova automaticamente quando:

- Qualquer módulo (coletor, worker, scheduler) solicita o token ao `MLAuthService`
- O token restante for **menor que 5 minutos** (`TOKEN_REFRESH_THRESHOLD_SECONDS = 300`)

A renovação usa o **Refresh Grant**:

```
POST https://api.mercadolibre.com/oauth/token
grant_type=refresh_token
client_id=...
client_secret=...
refresh_token=<REFRESH_TOKEN_ARMAZENADO>
```

> [!TIP]
> O `refresh_token` do Mercado Livre também é renovado a cada uso do Refresh Grant.
> O sistema persiste automaticamente o novo `refresh_token` retornado.
> Se o `refresh_token` expirar (após período de inatividade > 6 meses), o usuário precisará refazer o login.

---

## 6. Uso nos Coletores (para desenvolvedores)

Qualquer módulo que precise de um token autenticado deve usar o `MLAuthService`:

```python
from database.connection import AsyncSessionLocal
from services.auth import MLAuthService
from collector.api_client import MercadoLivreAPICollector

async def meu_job(user_id: int):
    async with AsyncSessionLocal() as session:
        # 1. Solicita o token ao serviço de autenticação
        auth_service = MLAuthService(session)

        # 2. Passa o serviço para o coletor
        collector = MercadoLivreAPICollector(
            auth_service=auth_service,
            user_id=user_id,
        )

        # 3. Usa endpoints autenticados normalmente
        orders = await collector.fetch_my_orders()
        items  = await collector.fetch_my_items()
```

> [!IMPORTANT]
> **Nunca** acesse o banco diretamente para ler tokens.
> **Sempre** solicite ao `MLAuthService.get_valid_token()`.
> O serviço garante que o token retornado é sempre válido.

---

## 7. Iniciar o Sistema com Docker

```bash
# 1. Configure as credenciais
cp .env.example .env
# Edite .env com ML_CLIENT_ID, ML_CLIENT_SECRET, ML_REDIRECT_URI

# 2. Suba todos os serviços
docker-compose up --build -d

# 3. Verifique os logs da API
docker logs datalake-api -f

# 4. Conecte sua conta ML no navegador
# Abra: http://localhost:8000/mercadolivre/connect

# 5. Após o login, verifique o status
curl http://localhost:8000/mercadolivre/status
```

---

## 8. Países Suportados

O endpoint de autorização padrão é para o Brasil:

```
https://auth.mercadolivre.com.br/authorization
```

Para outros países, altere a constante `ML_AUTH_BASE` em `services/auth.py`:

| País | Domínio |
|------|---------|
| 🇧🇷 Brasil | `https://auth.mercadolivre.com.br` |
| 🇦🇷 Argentina | `https://auth.mercadolibre.com.ar` |
| 🇲🇽 México | `https://auth.mercadolibre.com.mx` |
| 🇨🇱 Chile | `https://auth.mercadolibre.cl` |
| 🇨🇴 Colômbia | `https://auth.mercadolibre.com.co` |

O `site_id` retornado pela API (campo `country` na tabela) reflete o país:
`MLB` = Brasil, `MLA` = Argentina, `MLM` = México, etc.
