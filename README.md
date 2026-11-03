# Backend PDV

Backend do sistema PDV, construído com Django. O banco SQLite é usado por padrão no desenvolvimento local.

## Requisitos

- Python 3.12 ou superior
- Windows, macOS ou Linux

## Configuração no Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Copie o valor gerado e substitua `DJANGO_SECRET_KEY` no arquivo `.env`.

## Configuração no macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Copie o valor gerado e substitua `DJANGO_SECRET_KEY` no arquivo `.env`.

## Executar localmente

Com o ambiente virtual ativado e o `.env` configurado:

```bash
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

A aplicação estará disponível em `http://127.0.0.1:8000/`.
O servidor fica vinculado ao loopback e não fica acessível pela rede local por padrão.

## Teste da loja no Windows

Na pasta `frontend`, gere a interface integrada ao Django:

```powershell
npm.cmd run build:store
```

Isso grava o build em `backend/static/frontend`. Depois, inicie o `Iniciar Miss Presentes.bat`
na pasta `backend`. O iniciador prepara o ambiente Python 3.12 e as dependências na primeira
execução, cria um `.env` local se necessário, aplica as migrações, inicia o servidor local
e abre o navegador quando `/api/v1/health/` responder. A primeira execução precisa de
Python 3.12 instalado e conexão com a internet para baixar as dependências. A janela
minimizada do servidor permanece aberta enquanto o PDV estiver em uso; feche essa janela
para encerrar o servidor.

Este pacote é destinado a teste local, não é ainda um instalador de produção. Ele usa o
`runserver` do Django e mantém o banco `db.sqlite3` e as imagens na pasta do backend.
Faça cópias manuais dos dados importantes; backup automático e restauração ainda não estão
implementados.

## API v1

Todas as rotas JSON ficam sob `/api/v1/`. Listagens usam o formato `data` e paginação
(`page`, `page_size`, máximo de 100 registros por página). Erros usam `error` e,
quando aplicável, `details`.

| Método | Endpoint | Descrição |
| --- | --- | --- |
| `GET` | `/api/v1/health/` | Verifica se a API está disponível |
| `GET` | `/api/v1/csrf/` | Obtém token CSRF para requisições que alteram dados |
| `GET` | `/api/v1/categories/` | Lista as tags/categorias cadastradas |
| `GET`, `POST` | `/api/v1/products/` | Lista e cadastra produtos |
| `GET`, `PATCH` | `/api/v1/products/{id}/` | Consulta e atualiza dados do produto |
| `GET`, `POST` | `/api/v1/stock-movements/` | Consulta e registra ajustes de estoque |
| `GET`, `POST` | `/api/v1/sales/` | Consulta o histórico e registra vendas |

### Exemplos

Listar produtos, com busca/filtro/paginação opcionais:

```text
GET /api/v1/products/?q=colar&category=Colares&page=1&page_size=20
```

Criar produto (JSON; envie `multipart/form-data` quando incluir uma imagem):

```json
{
  "name": "Colar de pérolas",
  "sku": "COL-001",
  "price": "49.90",
  "stock": 5,
  "categories": ["Colares", "Novidades"]
}
```

Editar produto aceita `name`, `sku`, `price`, `categories` e `is_active`.
O estoque não pode ser alterado nessa rota.

Ajustar estoque usa delta positivo ou negativo; a data/hora, saldo anterior e saldo
resultante são calculados e gravados pelo servidor:

```json
{
  "product_id": "UUID-DO-PRODUTO",
  "quantity_delta": -1,
  "note": "Contagem física"
}
```

Registrar venda envia apenas identificadores e quantidades de produto, e não preços
confiáveis do navegador. O servidor valida o estoque, calcula valores e registra venda,
itens e baixas de estoque atomicamente:

```json
{
  "payment_method": "credit",
  "installments": 3,
  "discount_type": "percentage",
  "discount_value": "10",
  "interest_type": "percentage",
  "interest_value": "5",
  "items": [
    {"product_id": "UUID-DO-PRODUTO", "quantity": 2}
  ]
}
```

Para evitar duplicar uma venda em uma repetição de rede, envie um `Idempotency-Key`
único por tentativa. Se a mesma chave for reutilizada com um corpo diferente, a API
responde `409 Conflict`. No pagamento em dinheiro, `amount_received` é opcional; se
informado, a API valida o valor e calcula `change_given`.

### Front-end durante desenvolvimento

O caminho recomendado é manter o navegador falando com a mesma origem do Vite e
encaminhar `/api` ao Django por proxy. Isso evita CORS e mantém as URLs de API iguais
na integração posterior. O proxy abaixo ja esta configurado em `../frontend/vite.config.ts`. Durante o desenvolvimento, mantenha o Django em `127.0.0.1:8000` e inicie o Vite na pasta `frontend` com `npm run dev`:

```ts
server: {
  proxy: {
    '/api': {
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
    },
  },
},
```

O `.env` do Django deve confiar na origem do Vite durante desenvolvimento:
`DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000`.
Antes de qualquer `POST`/`PATCH`, o front-end deve buscar `/api/v1/csrf/` e enviar o
token retornado em `X-CSRFToken`, mantendo os cookies (`credentials: 'include'`).

Para o uso simplificado futuro, a arquitetura recomendada é empacotar o build estático
do front-end junto ao Django, servi-lo na mesma origem da API e iniciar o servidor
vinculado a `127.0.0.1`. Para distribuição no Windows, use um servidor WSGI apropriado
(por exemplo, Waitress) e WhiteNoise para os arquivos estáticos, em vez de `runserver`.
Um lançador local pode iniciar o processo, aguardar `/api/v1/health/` responder e só
então abrir o navegador. Não exponha esta API sem autenticação ou proteção adequada a
outros dispositivos da rede.

## Versionamento

O repositório Git do backend é independente do repositório do frontend. Arquivos de ambiente, banco local, mídia e ambientes virtuais não devem ser versionados.
