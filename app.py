import streamlit as st
import os
import psycopg2
from psycopg2 import IntegrityError
import hashlib
import requests
import json
import io
import re
from decimal import Decimal
from datetime import date, timedelta
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

# ViralRadar 3.3.1 — Banco persistente Supabase PostgreSQL e horários de Brasília
FUSO_BRASIL = ZoneInfo("America/Sao_Paulo")

def horario_brasilia(valor):
    """Converte ISO 8601 ou datetime em UTC para horário de São Paulo.
    Datetimes sem fuso são interpretados como UTC (padrão dos registros existentes).
    """
    if not valor:
        return None
    try:
        dt = valor if isinstance(valor, datetime) else datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(FUSO_BRASIL)
    except (ValueError, TypeError, OverflowError):
        return None

def exibir_horario_br(valor):
    dt = horario_brasilia(valor)
    return dt.strftime("%d/%m/%Y às %H:%M") if dt else "Não informado"

LIMITE_PLANO_INICIAL = 10
LIMITE_TROCAS_MENSAL = 5


def conectar_banco():
    """Abre conexão de curta duração usando somente Streamlit Secrets."""
    cfg = st.secrets["connections"]["supabase"]
    return psycopg2.connect(
        host=cfg["host"], port=int(cfg["port"]), dbname=cfg["database"],
        user=cfg["user"], password=cfg["password"],
        sslmode=cfg.get("sslmode", "require"), connect_timeout=12,
    )


# Permissões e assinaturas (fonte de verdade: Supabase PostgreSQL)
PLANOS_VIRALRADAR = {
    "inicial": {"nome": "Inicial", "canais": 10, "trocas": 5, "mensal": "R$ 19,99", "anual": "R$ 179,88"},
    "criador": {"nome": "Criador", "canais": 20, "trocas": 10, "mensal": "R$ 29,99", "anual": "R$ 299,88"},
    "agencia": {"nome": "Agência", "canais": 50, "trocas": 20, "mensal": "R$ 69,99", "anual": "R$ 779,88"},
}


def eh_administrador(email):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS(SELECT 1 FROM public.administradores WHERE email=%s)", (email,))
            return bool(cur.fetchone()[0])


def consultar_assinatura(email):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT plano, modalidade, status, inicio_em, vencimento_em,
                                  limite_canais, limite_trocas
                           FROM public.assinaturas WHERE email=%s""", (email,))
            row = cur.fetchone()
    if row is None:
        return None
    return dict(zip(("plano", "modalidade", "status", "inicio_em", "vencimento_em",
                     "limite_canais", "limite_trocas"), row))


def assinatura_valida(assinatura):
    if not assinatura or assinatura["status"] != "ativa":
        return False
    inicio, fim = assinatura["inicio_em"], assinatura["vencimento_em"]
    agora = datetime.now(timezone.utc)
    return bool(inicio and fim and inicio <= agora < fim)


def listar_clientes_admin(busca="", filtro_status="Todos", filtro_plano="Todos", filtro_modalidade="Todas", pagina=1, por_pagina=25):
    """Pesquisa paginada no PostgreSQL; não carrega a base inteira na interface."""
    condicoes = []
    parametros = []
    if busca.strip():
        condicoes.append("u.email ILIKE %s")
        parametros.append("%" + busca.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if filtro_status != "Todos":
        condicoes.append("COALESCE(a.status, 'sem assinatura') = %s")
        parametros.append(filtro_status)
    if filtro_plano != "Todos":
        condicoes.append("COALESCE(a.plano, 'sem plano') = %s")
        parametros.append(filtro_plano)
    if filtro_modalidade != "Todas":
        condicoes.append("COALESCE(a.modalidade, '-') = %s")
        parametros.append(filtro_modalidade)
    where = " WHERE " + " AND ".join(condicoes) if condicoes else ""
    origem = " FROM public.usuarios u LEFT JOIN public.assinaturas a ON a.email=u.email"
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*)" + origem + where, tuple(parametros))
            total = cur.fetchone()[0]
            paginas = max(1, (total + por_pagina - 1) // por_pagina)
            pagina = min(max(1, pagina), paginas)
            cur.execute("""SELECT u.email, COALESCE(a.plano, 'sem plano'),
                                  COALESCE(a.modalidade, '-'), COALESCE(a.status, 'sem assinatura'),
                                  a.vencimento_em, a.limite_canais, a.limite_trocas"""
                        + origem + where + " ORDER BY u.email LIMIT %s OFFSET %s",
                        tuple(parametros + [por_pagina, (pagina - 1) * por_pagina]))
            return cur.fetchall(), total, pagina, paginas


def localizar_conta_admin(email):
    """Consulta exata para operações administrativas, sem depender da página atual."""
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT email FROM public.usuarios WHERE LOWER(email)=LOWER(%s)", (email.strip(),))
            registro = cur.fetchone()
            return registro[0] if registro else None


def atualizar_assinatura_admin(email, plano, modalidade, status, dias):
    limites = PLANOS_VIRALRADAR[plano]
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM public.usuarios WHERE email=%s", (email,))
            if not cur.fetchone():
                raise ValueError("Usuário não encontrado")
            # Bloqueio e reativação mantêm o vencimento existente.
            if status == "ativa":
                cur.execute("""INSERT INTO public.assinaturas
                    (email, plano, modalidade, status, limite_canais, limite_trocas, inicio_em, vencimento_em)
                    VALUES (%s,%s,%s,'ativa',%s,%s,NOW(),NOW()+(%s * INTERVAL '1 day'))
                    ON CONFLICT (email) DO UPDATE SET
                    plano=EXCLUDED.plano, modalidade=EXCLUDED.modalidade,
                    status='ativa', limite_canais=EXCLUDED.limite_canais,
                    limite_trocas=EXCLUDED.limite_trocas, inicio_em=EXCLUDED.inicio_em,
                    vencimento_em=EXCLUDED.vencimento_em, atualizado_em=NOW()""",
                    (email, plano, modalidade, limites["canais"], limites["trocas"], dias))
            else:
                cur.execute("""UPDATE public.assinaturas SET status=%s, atualizado_em=NOW()
                               WHERE email=%s""", (status, email))
                if cur.rowcount == 0:
                    raise ValueError("Não existe assinatura para suspender")


def testar_conexao_asaas_sandbox():
    """Teste somente leitura no ambiente de homologação do Asaas."""
    chave = st.secrets.get("ASAAS_SANDBOX_API_KEY") or st.secrets.get("ASAAS_API_KEY")
    if not chave:
        return False, ("Chave não encontrada nos Secrets. Use o nome "
                       "ASAAS_SANDBOX_API_KEY (ou ASAAS_API_KEY).")
    try:
        resposta = requests.get(
            "https://api-sandbox.asaas.com/v3/myAccount",
            headers={"access_token": str(chave).strip(),
                     "accept": "application/json",
                     "User-Agent": "ViralRadar/3.5 (integracao sandbox)"},
            timeout=15,
        )
        if resposta.status_code == 200:
            return True, "Conexão autenticada com o Asaas Sandbox! Nenhuma cobrança foi criada."
        if resposta.status_code in (401, 403):
            return False, (f"Asaas retornou HTTP {resposta.status_code}: "
                           "verifique se a chave é do Sandbox e está completa.")
        return False, f"Asaas retornou HTTP {resposta.status_code}. Nenhuma cobrança foi criada."
    except requests.Timeout:
        return False, "Tempo esgotado ao conectar ao Asaas Sandbox. Tente novamente."
    except requests.RequestException:
        return False, "Não foi possível estabelecer conexão com o Asaas Sandbox."



# Cobranças de homologação: a ativação depende do webhook e das regras do processador no Supabase.
PRECOS_ASAAS = {
    "inicial": {"mensal": Decimal("19.99"), "anual": Decimal("179.88")},
    "criador": {"mensal": Decimal("29.99"), "anual": Decimal("299.88")},
    "agencia": {"mensal": Decimal("69.99"), "anual": Decimal("779.88")},
}


def asaas_sandbox_requisicao(metodo, rota, payload=None):
    chave = st.secrets.get("ASAAS_SANDBOX_API_KEY") or st.secrets.get("ASAAS_API_KEY")
    ambiente = str(st.secrets.get("ASAAS_ENV", "sandbox")).lower().strip()
    if ambiente != "sandbox" or not chave:
        raise ValueError("Checkout de teste indisponível: confira ASAAS_ENV=sandbox e a chave Asaas.")
    try:
        resposta = requests.request(
            metodo, "https://api-sandbox.asaas.com/v3/" + rota.lstrip("/"),
            headers={"access_token": str(chave).strip(), "accept": "application/json",
                     "Content-Type": "application/json", "User-Agent": "ViralRadar/3.5"},
            json=payload, timeout=25,
        )
        if not resposta.ok:
            # Não mostrar resposta integral, pois pode conter dados do cliente.
            raise ValueError(f"O Asaas Sandbox não aceitou a operação (HTTP {resposta.status_code}).")
        return resposta.json()
    except requests.RequestException as exc:
        raise ValueError("Falha de comunicação com o Asaas Sandbox. Tente novamente.") from exc


def localizar_cliente_asaas(email):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asaas_customer_id FROM public.asaas_clientes WHERE email=%s", (email,))
            registro = cur.fetchone()
            return registro[0] if registro else None


def obter_ou_criar_cliente_asaas(email, nome, cpf_cnpj):
    existente = localizar_cliente_asaas(email)
    if existente:
        return existente
    cliente = asaas_sandbox_requisicao("POST", "customers", {
        "name": nome, "email": email, "cpfCnpj": cpf_cnpj,
        "externalReference": email,
    })
    identificador = cliente.get("id")
    if not identificador:
        raise ValueError("Asaas não retornou o identificador do cliente.")
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO public.asaas_clientes (email, asaas_customer_id)
                           VALUES (%s,%s) ON CONFLICT (email) DO NOTHING""", (email, identificador))
            cur.execute("SELECT asaas_customer_id FROM public.asaas_clientes WHERE email=%s", (email,))
            return cur.fetchone()[0]


def cobranca_pendente_asaas(email, plano, modalidade, forma):
    # Evita gerar várias cobranças ao repetir o clique na mesma opção.
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT asaas_payment_id FROM public.asaas_cobrancas
                WHERE email=%s AND plano=%s AND modalidade=%s AND status_asaas IN ('PENDING','AWAITING_RISK_ANALYSIS')
                AND vencimento>=CURRENT_DATE ORDER BY criado_em DESC LIMIT 1""",
                (email, plano, modalidade))
            row = cur.fetchone()
    if not row:
        return None
    pagamento = asaas_sandbox_requisicao("GET", f"payments/{row[0]}")
    if pagamento.get("billingType") == forma and pagamento.get("status") in ("PENDING", "AWAITING_RISK_ANALYSIS"):
        return pagamento
    return None


def criar_cobranca_asaas(email, nome, cpf_cnpj, plano, modalidade, forma):
    if plano not in PRECOS_ASAAS or modalidade not in ("mensal", "anual"):
        raise ValueError("Plano ou período inválido.")
    if forma not in ("PIX", "CREDIT_CARD"):
        raise ValueError("Forma de pagamento inválida.")
    nome = nome.strip()
    documento = re.sub(r"\D", "", cpf_cnpj)
    if len(nome) < 3 or len(documento) not in (11, 14):
        raise ValueError("Informe seu nome completo e CPF/CNPJ válido em formato.")
    pendente = cobranca_pendente_asaas(email, plano, modalidade, forma)
    if pendente:
        link = pendente.get("invoiceUrl")
        if link and link.startswith("https://"):
            return link, pendente["id"], True
    cliente_id = obter_ou_criar_cliente_asaas(email, nome, documento)
    valor = PRECOS_ASAAS[plano][modalidade]
    vencimento = date.today() + timedelta(days=3)
    referencia = f"viralradar:{email}:{plano}:{modalidade}"
    pagamento = asaas_sandbox_requisicao("POST", "payments", {
        "customer": cliente_id, "billingType": forma, "value": float(valor),
        "dueDate": vencimento.isoformat(),
        "description": f"ViralRadar - plano {plano} - {modalidade}",
        "externalReference": referencia,
    })
    payment_id, link = pagamento.get("id"), pagamento.get("invoiceUrl")
    if not payment_id:
        raise ValueError("Asaas não retornou o identificador da cobrança.")
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO public.asaas_cobrancas
                (asaas_payment_id,email,asaas_customer_id,plano,modalidade,valor_centavos,status_asaas,vencimento)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (asaas_payment_id) DO NOTHING""",
                (payment_id,email,cliente_id,plano,modalidade,int(valor*100),
                 pagamento.get("status", "PENDING"),vencimento))
    if not link or not link.startswith("https://"):
        raise ValueError("Cobrança registrada, mas Asaas não retornou um link de pagamento seguro.")
    return link, payment_id, False


def criar_hash_senha(senha):
    """PBKDF2 com salt aleatório; também aceitamos hashes legados no login."""
    import secrets
    salt = secrets.token_bytes(16)
    chave = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${chave.hex()}"


def conferir_senha(senha, armazenada):
    import hmac
    if armazenada.startswith("pbkdf2_sha256$"):
        try:
            _, ciclos, salt, chave = armazenada.split("$", 3)
            calculada = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(salt), int(ciclos))
            return hmac.compare_digest(calculada, bytes.fromhex(chave))
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(hashlib.sha256(senha.encode()).hexdigest(), armazenada)


def cadastrar_usuario_db(email, senha):
    email = email.strip().lower()
    try:
        with conectar_banco() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO usuarios (email, senha) VALUES (%s, %s)",
                            (email, criar_hash_senha(senha)))
                cur.execute("INSERT INTO metricas (email, trocas) VALUES (%s, 0)", (email,))
        return True, "Cadastro realizado com sucesso!"
    except IntegrityError:
        return False, "E-mail já cadastrado!"


def verificar_login_db(email, senha):
    email = email.strip().lower()
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT senha FROM usuarios WHERE email=%s", (email,))
            linha = cur.fetchone()
            if not linha or not conferir_senha(senha, linha[0]):
                return False
            if not linha[0].startswith("pbkdf2_sha256$"):
                cur.execute("UPDATE usuarios SET senha=%s WHERE email=%s", (criar_hash_senha(senha), email))
            return True


def ler_perfis_db(email):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT perfil FROM perfis WHERE email=%s ORDER BY perfil", (email,))
            return [linha[0] for linha in cur.fetchall()]


def salvar_perfil_db(email, novo_perfil):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM perfis WHERE email=%s", (email,))
            quantidade = cur.fetchone()[0]
            cur.execute("""SELECT limite_canais FROM public.assinaturas
                           WHERE email=%s AND status='ativa'
                           AND inicio_em<=NOW() AND vencimento_em>NOW()""", (email,))
            limite = cur.fetchone()
            if not limite:
                return False, "Assinatura inativa"
            if quantidade >= limite[0]:
                return False, "Limite atingido"
            cur.execute("INSERT INTO perfis (email, perfil, ultimo_post_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING perfil",
                        (email, novo_perfil, "nenhum"))
            return (True, "Sucesso") if cur.fetchone() else (False, "Duplicado")


def excluir_perfil_db(email, perfil_alvo):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM snapshots_perfil WHERE email=%s AND perfil=%s", (email, perfil_alvo))
            cur.execute("DELETE FROM perfis WHERE email=%s AND perfil=%s", (email, perfil_alvo))
    return True


def ler_trocas_db(email):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO metricas (email,trocas) VALUES (%s,0) ON CONFLICT (email) DO NOTHING", (email,))
            cur.execute("SELECT trocas FROM metricas WHERE email=%s", (email,))
            return cur.fetchone()[0]


def salvar_troca_db(email, qtd):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO metricas (email,trocas) VALUES (%s,%s) ON CONFLICT (email) DO UPDATE SET trocas=EXCLUDED.trocas", (email, qtd))


def registrar_substituicao_db(email):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE metricas SET trocas=trocas+1
                           WHERE email=%s AND trocas < (
                               SELECT limite_trocas FROM public.assinaturas
                               WHERE email=%s AND status='ativa'
                               AND inicio_em<=NOW() AND vencimento_em>NOW()) RETURNING trocas""",
                        (email, email))
            return cur.fetchone() is not None


def registrar_historico_db(mensagem, email=None):
    if not email:
        return
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO historico (email,mensagem,data) VALUES (%s,%s,%s)",
                        (email, mensagem, datetime.now(FUSO_BRASIL).strftime("%d/%m/%Y %H:%M:%S")))


def ler_historico_db(email):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT data,mensagem FROM historico WHERE email=%s ORDER BY id DESC LIMIT 200", (email,))
            return [f"{data} — {mensagem}" for data, mensagem in cur.fetchall()]


def salvar_config_telegram_db(email, chat_id):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO configuracoes (email,telegram_token,telegram_chat_id)
                VALUES (%s,%s,%s) ON CONFLICT (email) DO UPDATE SET telegram_chat_id=EXCLUDED.telegram_chat_id""",
                (email, "", chat_id))


def ler_config_telegram_db(email):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_token,telegram_chat_id FROM configuracoes WHERE email=%s", (email,))
            return cur.fetchone() or ("", "")


def credenciais_telegram_usuario(email):
    """Token protegido no Streamlit; Chat ID individual no cadastro do usuário."""
    token = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
    _, chat_id = ler_config_telegram_db(email)
    return token, chat_id

# ==========================================
# MOTOR DE DISPARO E CHECAGEM (YOUTUBE + TELEGRAM)
# ==========================================
def enviar_alerta_telegram(token, chat_id, mensagem):
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensagem,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            # Nunca registrar a URL com o token nos logs.
            print(f"Telegram: falha HTTP {response.status_code} no envio.")
        return response.status_code == 200
    except requests.RequestException:
        print("Telegram: falha de conexão no envio.")
        return False

def consultar_youtube(endpoint, parametros):
    """Consulta a API oficial sem expor a chave nos logs."""
    chave = st.secrets["YOUTUBE_API_KEY"]
    resposta = requests.get(
        f"https://www.googleapis.com/youtube/v3/{endpoint}",
        params={**parametros, "key": chave}, timeout=15
    )
    resposta.raise_for_status()
    return resposta.json()


def identificar_canal_youtube(entrada):
    """Aceita @handle, URL /@handle, URL /channel/UC... ou ID UC..."""
    from urllib.parse import urlparse
    entrada = entrada.strip()
    if entrada.startswith(("https://", "http://")):
        url = urlparse(entrada)
        if url.hostname not in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            raise ValueError("Informe um link do YouTube válido.")
        partes = url.path.strip("/").split("/")
        if partes[0] == "channel" and len(partes) > 1:
            entrada = partes[1]
        elif partes[0].startswith("@"):
            entrada = partes[0]
        else:
            raise ValueError("Use o link /@handle ou /channel/ID do canal.")
    if entrada.startswith("UC") and len(entrada) == 24:
        parametros = {"part": "snippet,contentDetails", "id": entrada}
    else:
        parametros = {"part": "snippet,contentDetails", "forHandle": entrada.lstrip("@")}
    dados = consultar_youtube("channels", parametros)
    itens = dados.get("items", [])
    if not itens:
        raise ValueError("Canal não encontrado. Confira o @handle ou ID.")
    item = itens[0]
    return item["id"], item["snippet"]["title"], item["contentDetails"]["relatedPlaylists"]["uploads"]


def checar_novos_posts_perfil(email, canal):
    """Retorna (houve_novidade, mensagem, dados_painel)."""
    try:
        canal_id, nome, uploads = identificar_canal_youtube(canal)
        pagina = consultar_youtube("playlistItems", {
            "part": "snippet,contentDetails", "playlistId": uploads, "maxResults": 10
        })
        itens = pagina.get("items", [])
        ids = [i.get("contentDetails", {}).get("videoId") for i in itens]
        ids = [v for v in ids if v]
        if not ids:
            return False, f"{nome}: nenhum vídeo público encontrado.", {"nome": nome, "videos": []}

        # videos.list fornece estatísticas reais dos vídeos disponíveis.
        detalhes = consultar_youtube("videos", {
            "part": "snippet,statistics", "id": ",".join(ids[:5]), "maxResults": 5
        })
        por_id = {v["id"]: v for v in detalhes.get("items", [])}
        videos = []
        for video_id in ids[:5]:
            dado = por_id.get(video_id)
            if not dado:
                continue
            snippet = dado.get("snippet", {})
            stats = dado.get("statistics", {})
            videos.append({
                "id": video_id,
                "titulo": snippet.get("title", "Sem título"),
                "publicado": snippet.get("publishedAt", ""),
                "imagem": snippet.get("thumbnails", {}).get("medium", {}).get("url"),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "visualizacoes": int(stats["viewCount"]) if "viewCount" in stats else None,
                "curtidas": int(stats["likeCount"]) if "likeCount" in stats else None,
                "comentarios": int(stats["commentCount"]) if "commentCount" in stats else None,
            })

        coletado_em = datetime.now(timezone.utc).isoformat()
        with conectar_banco() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ultimo_post_id FROM perfis WHERE email=%s AND perfil=%s", (email, canal))
                linha = cur.fetchone()
                if linha is None:
                    return False, "Canal não cadastrado.", None
                anterior = linha[0]
                for video in videos:
                    cur.execute("""SELECT visualizacoes FROM video_metricas
                        WHERE email=%s AND canal_id=%s AND video_id=%s
                        ORDER BY coletado_em DESC LIMIT 1""", (email, canal_id, video["id"]))
                    antiga = cur.fetchone()
                    video["delta_views"] = (video["visualizacoes"] - antiga[0]
                        if antiga and antiga[0] is not None and video["visualizacoes"] is not None else None)
                    cur.execute("""INSERT INTO video_metricas
                        (email, canal_id, video_id, visualizacoes, curtidas, comentarios, coletado_em)
                        VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (email, canal_id, video["id"], video["visualizacoes"],
                         video["curtidas"], video["comentarios"], coletado_em))
                mais_recente = ids[0]
                primeira = not anterior or anterior == "nenhum" or anterior.startswith("yt_video_")
                novos = [] if primeira or anterior == mais_recente or anterior not in ids else ids[:ids.index(anterior)]
                if primeira or anterior not in ids:
                    cur.execute("UPDATE perfis SET ultimo_post_id=%s WHERE email=%s AND perfil=%s",
                                (mais_recente, email, canal))
                    mensagem = f"{nome}: referência sincronizada; exibindo os últimos 5 vídeos."
                elif novos:
                    token, chat_id = credenciais_telegram_usuario(email)
                    falha = False
                    for video_id in reversed(novos):
                        aviso = f"🚨 NOVO VÍDEO NO YOUTUBE!\nCanal: {nome}\nhttps://www.youtube.com/watch?v={video_id}"
                        if token and chat_id and not enviar_alerta_telegram(token, chat_id, aviso):
                            falha = True
                            break
                    if not falha:
                        cur.execute("UPDATE perfis SET ultimo_post_id=%s WHERE email=%s AND perfil=%s",
                                    (mais_recente, email, canal))
                    mensagem = (f"{nome}: {len(novos)} novo(s) vídeo(s) detectado(s)." if not falha
                                else f"{nome}: falha ao enviar Telegram; tentativa será repetida.")
                else:
                    mensagem = f"{nome}: sem vídeos novos; métricas atualizadas."
        return bool(novos), mensagem, {"nome": nome, "videos": videos, "coletado_em": coletado_em}
    except (requests.RequestException, ValueError, KeyError, psycopg2.Error) as erro:
        return False, f"Erro ao consultar {canal}: {erro}", None


def mostrar_resultados_youtube(painel):
    if not painel:
        return
    st.markdown(f"#### {painel['nome']} — últimos 5 vídeos")
    st.caption(f"Consulta (Brasília): {exibir_horario_br(painel.get('coletado_em'))}")
    for video in painel["videos"]:
        with st.container(border=True):
            col_img, col_info = st.columns([1, 3])
            with col_img:
                if video["imagem"]:
                    st.image(video["imagem"], use_container_width=True)
            with col_info:
                st.markdown(f"**{video['titulo']}**")
                st.caption(f"Publicado: {video['publicado'][:10] or 'Não informado'}")
                st.link_button("▶ Assistir", video["url"])
            def fmt(valor):
                return f"{valor:,}".replace(",", ".") if valor is not None else "Indisponível"
            c1, c2, c3 = st.columns(3)
            c1.metric("Visualizações", fmt(video["visualizacoes"]),
                      delta=fmt(video["delta_views"]) if video["delta_views"] is not None else None)
            c2.metric("Curtidas", fmt(video["curtidas"]))
            c3.metric("Comentários", fmt(video["comentarios"]))


def ler_execucoes_automaticas(email, limite=10):
    """Consulta apenas o histórico do usuário autenticado; não chama a API YouTube."""
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT iniciado_em, finalizado_em, status,
                       canais_consultados, videos_detectados, erros
                       FROM monitoramento_execucoes
                       WHERE email=%s ORDER BY iniciado_em DESC LIMIT %s""",
                        (email, limite))
            return cur.fetchall()


def mostrar_monitoramento_automatico(email):
    st.markdown("### 🤖 Monitoramento automático")
    try:
        execucoes = ler_execucoes_automaticas(email)
    except psycopg2.Error:
        st.warning("Não foi possível consultar o histórico automático agora. Tente atualizar a tela.")
        return

    if not execucoes:
        st.info("Ainda não há verificações automáticas registradas para esta conta.")
        return

    inicio, fim, status, canais, videos, erros = execucoes[0]
    status_legenda = {"sucesso": "✅ Concluído com sucesso",
                      "falha": "🔴 Concluído com falha",
                      "em_andamento": "🟡 Em andamento"}.get(status, "⚪ Não identificado")
    inicio_br = horario_brasilia(inicio)
    if inicio_br and (datetime.now(FUSO_BRASIL) - inicio_br).total_seconds() > 45 * 60:
        st.warning("A última verificação registrada tem mais de 45 minutos. Confira o agendamento no GitHub Actions.")

    st.caption("Última verificação iniciada: " + exibir_horario_br(inicio) +
               " (Brasília) | Finalizada: " + exibir_horario_br(fim))
    st.write("**Resultado da última execução:** " + status_legenda)
    c1, c2, c3 = st.columns(3)
    c1.metric("Canais consultados", canais)
    c2.metric("Vídeos detectados", videos)
    c3.metric("Erros", erros)
    st.caption("Informações do Supabase, atualizadas ao recarregar a página. "
               "A programação de 15 minutos depende do GitHub Actions e pode sofrer atrasos.")

    with st.expander("📋 Histórico das últimas verificações"):
        linhas = []
        for ini, terminado, situacao, qtd_canais, qtd_videos, qtd_erros in execucoes:
            linhas.append({
                "Início (Brasília)": exibir_horario_br(ini),
                "Fim (Brasília)": exibir_horario_br(terminado),
                "Status": situacao,
                "Canais": qtd_canais,
                "Vídeos detectados": qtd_videos,
                "Erros": qtd_erros,
            })
        st.dataframe(linhas, hide_index=True, use_container_width=True)


# ==========================================
# CACHE DE RESULTADOS E EXPORTAÇÃO
# ==========================================
def salvar_snapshot(email, perfil, painel, novo):
    if painel is None:
        return
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO snapshots_perfil
                (email, perfil, painel_json, atualizado_em, novo)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(email, perfil) DO UPDATE SET
                painel_json=EXCLUDED.painel_json,
                atualizado_em=EXCLUDED.atualizado_em,
                novo=EXCLUDED.novo""",
                (email, perfil, json.dumps(painel, ensure_ascii=False),
                 painel.get("coletado_em", datetime.now(timezone.utc).isoformat()), int(novo)))


def ler_snapshots(email):
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT perfil, painel_json, atualizado_em, novo
                FROM snapshots_perfil WHERE email=%s""", (email,))
            registros = cur.fetchall()
    return {p: {"painel": json.loads(j), "atualizado": t, "novo": bool(n)}
            for p, j, t, n in registros}


def linhas_relatorio(perfis, snapshots):
    linhas = []
    for perfil in perfis:
        dado = snapshots.get(perfil)
        if not dado:
            continue
        painel = dado["painel"]
        for pos, video in enumerate(painel.get("videos", []), 1):
            linhas.append({
                "Perfil": perfil, "Nome do canal": painel.get("nome", perfil),
                "Rede": "YouTube", "Posição": pos,
                "Título": video.get("titulo", ""),
                "Publicado em (Brasília)": video.get("publicado", ""),
                "Visualizações": video.get("visualizacoes"),
                "Variação de visualizações": video.get("delta_views"),
                "Curtidas": video.get("curtidas"),
                "Comentários": video.get("comentarios"),
                "Link": video.get("url", ""),
                "Consultado em (Brasília)": dado["atualizado"]
            })
    return linhas


def data_excel(valor):
    """Converte datas UTC para horário de Brasília, sem fuso (exigência do Excel)."""
    dt = horario_brasilia(valor)
    return dt.replace(tzinfo=None) if dt else None


def ler_evolucao_db(email, perfis, snapshots=None):
    """Histórico REAL das medições armazenadas; não inventa crescimento."""
    if not perfis:
        return []
    with conectar_banco() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT vm.canal_id, vm.video_id, vm.coletado_em,
                vm.visualizacoes, vm.curtidas, vm.comentarios
                FROM video_metricas vm WHERE vm.email=%s
                ORDER BY vm.video_id, vm.coletado_em""", (email,))
            dados = cur.fetchall()
    if snapshots is not None:
        ids = {video.get("id") for perfil in perfis
               for video in (snapshots.get(perfil, {}).get("painel") or {}).get("videos", [])}
        dados = [registro for registro in dados if registro[1] in ids]
    return dados


def gerar_excel(linhas, historico=None):
    """Relatório de 4 abas com dados reais das últimas consultas."""
    historico = historico or []
    wb = Workbook()
    resumo = wb.active
    resumo.title = "Resumo Geral"
    publicacoes = wb.create_sheet("Publicações")
    evolucao = wb.create_sheet("Evolução")
    graficos = wb.create_sheet("Gráficos")
    azul, claro, verde = "183153", "EAF0F7", "167D63"

    def titulo(ws, texto, ultima_coluna):
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ultima_coluna)
        c = ws.cell(1, 1, texto)
        c.font = Font(name="Aptos Display", size=16, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=azul)
        c.alignment = Alignment(vertical="center")
        ws.row_dimensions[1].height = 32
        ws.sheet_view.showGridLines = False

    def cabecalho(ws, row, labels):
        for col, label in enumerate(labels, 1):
            cell = ws.cell(row, col, label)
            cell.fill = PatternFill("solid", fgColor=azul)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[row].height = 29
        ws.freeze_panes = f"A{row+1}"

    def faixa(ws, row, n):
        if row % 2 == 0:
            for col in range(1, n + 1):
                ws.cell(row, col).fill = PatternFill("solid", fgColor=claro)

    titulo(resumo, "VIRALRADAR | Resumo de monitoramento", 6)
    resumo["A3"] = "Gerado em (Brasília)"
    resumo["B3"] = datetime.now(FUSO_BRASIL).replace(tzinfo=None)
    resumo["B3"].number_format = "dd/mm/yyyy hh:mm"
    resumo["A4"] = "Fonte: últimas consultas salvas no ViralRadar; somente YouTube integrado."
    resumo.merge_cells("A4:F4")
    cabecalho(resumo, 6, ["Perfil", "Rede", "Vídeos exibidos", "Views acumuladas*", "Curtidas*", "Comentários*"])
    grupos = {}
    for item in linhas:
        chave = (item.get("Nome do canal") or item.get("Perfil"), item.get("Rede"))
        grupos.setdefault(chave, []).append(item)
    for idx, ((perfil, rede), itens) in enumerate(sorted(grupos.items()), 7):
        valores = [perfil, rede, len(itens)]
        for campo in ("Visualizações", "Curtidas", "Comentários"):
            nums = [x[campo] for x in itens if isinstance(x.get(campo), (int, float))]
            valores.append(sum(nums) if nums else None)
        for col, val in enumerate(valores, 1):
            resumo.cell(idx, col, val)
        faixa(resumo, idx, 6)
        for col in (4, 5, 6):
            resumo.cell(idx, col).number_format = '#,##0'
    aviso_row = 8 + len(grupos)
    resumo.cell(aviso_row, 1, "* Totais dos vídeos exibidos; não representam todas as publicações do canal.")
    resumo.merge_cells(start_row=aviso_row, start_column=1, end_row=aviso_row, end_column=6)
    resumo.auto_filter.ref = f"A6:F{max(7, 6+len(grupos))}"
    for col, largura in {"A":32,"B":16,"C":20,"D":24,"E":20,"F":22}.items():
        resumo.column_dimensions[col].width = largura

    titulo(publicacoes, "VIRALRADAR | Publicações monitoradas", 12)
    cabecalho(publicacoes, 3, ["Perfil", "Rede", "Data da publicação (Brasília)", "Título", "Visualizações",
                               "Crescimento (views)", "Crescimento (%)", "Curtidas", "Comentários",
                               "Link do vídeo", "Última consulta (Brasília)", "ID do vídeo"])
    for row, item in enumerate(linhas, 4):
        link = item.get("Link") or ""
        atual = item.get("Visualizações")
        delta = item.get("Variação de visualizações")
        anterior_views = atual - delta if isinstance(atual, (int, float)) and isinstance(delta, (int, float)) else None
        percentual = delta / anterior_views if anterior_views is not None and anterior_views > 0 else None
        valores = [item.get("Nome do canal") or item.get("Perfil"), item.get("Rede"),
                   data_excel(item.get("Publicado em (Brasília)")), item.get("Título"),
                   atual, delta, percentual, item.get("Curtidas"), item.get("Comentários"), link,
                   data_excel(item.get("Consultado em (Brasília)")),
                   link.split("v=")[-1].split("&")[0] if "v=" in link else ""]
        for col, valor in enumerate(valores, 1):
            cel = publicacoes.cell(row, col, valor)
            if col in (3, 11):
                cel.number_format = "dd/mm/yyyy hh:mm"
            if col in (5, 6, 8, 9):
                cel.number_format = '#,##0'
            if col == 7:
                cel.number_format = '0.0%'
        if link.startswith("https://www.youtube.com/"):
            cel = publicacoes.cell(row, 10)
            cel.hyperlink = link
            cel.font = Font(color="0563C1", underline="single")
        faixa(publicacoes, row, 12)
    publicacoes.auto_filter.ref = f"A3:L{max(4,3+len(linhas))}"
    for col, largura in {"A":29,"B":15,"C":24,"D":53,"E":20,"F":23,"G":19,"H":17,"I":19,"J":48,"K":24,"L":19}.items():
        publicacoes.column_dimensions[col].width = largura

    titulo(evolucao, "VIRALRADAR | Histórico de medições", 7)
    evolucao["A2"] = "Cada linha representa uma coleta salva. A evolução só existe após duas ou mais consultas."
    evolucao.merge_cells("A2:G2")
    cabecalho(evolucao, 4, ["Canal ID", "Vídeo ID", "Consulta (Brasília)", "Visualizações", "Curtidas", "Comentários", "Variação de views"])
    anterior = {}
    for row, (canal, video, coleta, views, likes, comentarios) in enumerate(historico, 5):
        chave = (canal, video)
        previa = anterior.get(chave)
        delta = views - previa if views is not None and previa is not None else None
        anterior[chave] = views
        vals = [canal, video, data_excel(coleta), views, likes, comentarios, delta]
        for col, val in enumerate(vals, 1):
            cell = evolucao.cell(row, col, val)
            if col == 3: cell.number_format = "dd/mm/yyyy hh:mm"
            if col in (4, 5, 6, 7): cell.number_format = '#,##0'
        faixa(evolucao, row, 7)
    evolucao.auto_filter.ref = f"A4:G{max(5,4+len(historico))}"
    for col, largura in {"A":32,"B":23,"C":25,"D":22,"E":18,"F":18,"G":23}.items():
        evolucao.column_dimensions[col].width = largura

    titulo(graficos, "VIRALRADAR | Gráficos de desempenho", 6)
    graficos["A3"] = "Valores reais coletados. Cada barra identifica um vídeo; a legenda informa a métrica."
    graficos.merge_cells("A3:C3")
    cabecalho(graficos, 5, ["Vídeo (data + título)", "Visualizações", "Crescimento (views)"])
    ordenadas = sorted([x for x in linhas if isinstance(x.get("Visualizações"), (int, float))],
                       key=lambda x: x["Visualizações"], reverse=True)[:20]
    tem_delta = False
    for row, item in enumerate(ordenadas, 6):
        dt = data_excel(item.get("Publicado em (Brasília)"))
        rotulo = f"{dt.strftime('%d/%m/%Y') if dt else 's/data'} | {str(item.get('Título',''))[:38]}"
        graficos.cell(row, 1, rotulo)
        graficos.cell(row, 2, item["Visualizações"])
        delta = item.get("Variação de visualizações")
        if isinstance(delta, (int, float)):
            graficos.cell(row, 3, delta)
            tem_delta = True
        for col in (2, 3):
            graficos.cell(row, col).number_format = '#,##0'
        faixa(graficos, row, 3)
    graficos.column_dimensions["A"].width = 68
    graficos.column_dimensions["B"].width = 23
    graficos.column_dimensions["C"].width = 25
    if ordenadas:
        chart = BarChart()
        chart.type = "bar"
        chart.title = "Visualizações dos vídeos monitorados"
        chart.y_axis.title = "Vídeo (publicação e título)"
        chart.x_axis.title = "Visualizações"
        chart.height = max(9, min(18, len(ordenadas) * 0.8 + 5))
        chart.width = 25
        chart.add_data(Reference(graficos, min_col=2, min_row=5, max_row=5+len(ordenadas)), titles_from_data=True)
        chart.set_categories(Reference(graficos, min_col=1, min_row=6, max_row=5+len(ordenadas)))
        chart.legend.position = "b"
        chart.dLbls = DataLabelList()
        chart.dLbls.showVal = True
        graficos.add_chart(chart, "E5")
        if tem_delta:
            crescimento = BarChart()
            crescimento.type = "bar"
            crescimento.title = "Crescimento de visualizações desde a consulta anterior"
            crescimento.y_axis.title = "Vídeo"
            crescimento.x_axis.title = "Novas visualizações"
            crescimento.height = chart.height
            crescimento.width = 25
            crescimento.add_data(Reference(graficos, min_col=3, min_row=5, max_row=5+len(ordenadas)), titles_from_data=True)
            crescimento.set_categories(Reference(graficos, min_col=1, min_row=6, max_row=5+len(ordenadas)))
            crescimento.legend.position = "b"
            crescimento.dLbls = DataLabelList()
            crescimento.dLbls.showVal = True
            graficos.add_chart(crescimento, f"E{max(27, 8 + len(ordenadas) * 2)}")
        else:
            graficos.cell(7 + len(ordenadas), 1, "Aguardando uma segunda consulta para calcular o crescimento dos vídeos.")

    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def gerar_txt(linhas):
    if not linhas:
        return "Nenhum dado coletado até o momento."
    return "\n\n".join("\n".join(f"{chave}: {valor if valor is not None else 'Indisponível'}"
                                   for chave, valor in linha.items()) for linha in linhas)


def formato_views(valor):
    if valor is None:
        return "s/d"
    if valor >= 1_000_000:
        return f"{valor / 1_000_000:.1f} mi".replace(".", ",")
    if valor >= 1_000:
        return f"{valor / 1_000:.1f} mil".replace(".", ",")
    return str(valor)

# ==========================================
# CONFIGURAÇÃO DA PÁGINA STREAMLIT
# ==========================================
st.set_page_config(
    page_title="ViralRadar - Monitor de Tendências",
    page_icon="🚀",
    layout="wide"
)

if "logado" not in st.session_state:
    st.session_state.logado = False
if "usuario_atual" not in st.session_state:
    st.session_state.usuario_atual = ""
if "plano_checkout" not in st.session_state:
    st.session_state.plano_checkout = None

# ==========================================
# TELA DE AUTENTICAÇÃO (LOGIN / CADASTRO)
# ==========================================
if not st.session_state.logado:
    st.title("🚀 ViralRadar — Bem-vindo!")
    st.markdown("Faça login na sua conta ou crie um novo cadastro seguro para acessar o monitor de tendências.")
    
    aba_login, aba_cadastro = st.tabs(["🔑 Entrar (Login)", "📝 Criar Conta"])
    
    with aba_login:
        st.subheader("Acessar sua Conta")
        with st.form("form_login_db"):
            email_login = st.text_input("E-mail:")
            senha_login = st.text_input("Senha:", type="password")
            btn_entrar = st.form_submit_button("Entrar no Painel")
            
            if btn_entrar:
                if verificar_login_db(email_login, senha_login):
                    st.session_state.logado = True
                    st.session_state.usuario_atual = email_login.strip().lower()
                    st.success("Login efetuado com sucesso! Entrando...")
                    st.rerun()
                else:
                    st.error("⚠️ E-mail ou senha incorretos.")

    with aba_cadastro:
        st.subheader("Criar Nova Conta")
        with st.form("form_cadastro_db"):
            email_cad = st.text_input("Seu E-mail:")
            senha_cad = st.text_input("Crie uma Senha:", type="password")
            btn_cadastrar = st.form_submit_button("Cadastrar e Acessar")
            
            if btn_cadastrar:
                if email_cad and senha_cad:
                    sucesso, msg = cadastrar_usuario_db(email_cad, senha_cad)
                    if sucesso:
                        st.session_state.logado = True
                        st.session_state.usuario_atual = email_cad.strip().lower()
                        st.success(f"🎉 {msg} Seja bem-vindo!")
                        st.rerun()
                    else:
                        st.warning(msg)
                else:
                    st.warning("Preencha todos os campos para cadastrar.")
    
    st.stop()

# ==========================================
# PAINEL PRINCIPAL (APÓS O LOGIN)
# ==========================================
st.title("🚀 ViralRadar - Painel de Controle Inteligente")
st.markdown(f"Monitoramento de canais e tendências | 👤 **{st.session_state.usuario_atual}**")

email_usuario = st.session_state.usuario_atual
try:
    administrador = eh_administrador(email_usuario)
    assinatura = consultar_assinatura(email_usuario)
except psycopg2.Error:
    st.error("Não foi possível consultar as permissões. Tente novamente mais tarde.")
    st.stop()

acesso_ativo = assinatura_valida(assinatura)
limite_canais = assinatura["limite_canais"] if acesso_ativo else 0
limite_trocas = assinatura["limite_trocas"] if acesso_ativo else 0

with st.sidebar:
    st.header("📌 Menu")
    opcoes = ["Painel Principal", "🔥 Tendências e Assuntos", "Histórico de Alertas",
              "Configurar Telegram", "Assinatura / Planos", "Finalizar Compra / Pagamento", "Suporte"]
    if administrador:
        opcoes.append("🛡️ Administração")
    opcao = st.radio("Seção:", opcoes, key="menu_principal_viralradar_admin_v1")
    st.markdown("---")
    st.markdown("**Status do Sistema:** 🟢 Online (YouTube API)")
    
    if st.button("🚪 Sair da Conta (Logout)", key="btn_logout_v9"):
        st.session_state.logado = False
        st.session_state.usuario_atual = ""
        st.rerun()
        
    if st.button("🔄 Atualizar Tela", key="btn_atualizar_geral_v9"):
        st.rerun()

# Uma conta sem assinatura pode ver os planos, mas não operar o monitoramento.
if not acesso_ativo and opcao not in ("Assinatura / Planos", "Finalizar Compra / Pagamento", "Suporte", "🛡️ Administração"):
    st.warning("Sua conta não possui assinatura ativa. Escolha um plano ou entre em contato com o suporte.")
    st.stop()

perfis_cadastrados = ler_perfis_db(email_usuario)
qtd_atual = len(perfis_cadastrados)
trocas_utilizadas = ler_trocas_db(email_usuario)
trocas_restantes = max(0, limite_trocas - trocas_utilizadas)

if opcao == "Painel Principal":
    st.subheader("📡 ViralRadar 3.5 — Central multirredes")
    st.caption("YouTube integrado • TikTok, Instagram, Facebook e X: integrações futuras, sem coleta ainda")
    st.markdown(f"**Perfis cadastrados:** {qtd_atual} de {limite_canais} | **Trocas usadas:** {trocas_utilizadas} de {limite_trocas}")

    mostrar_monitoramento_automatico(email_usuario)
    st.divider()

    with st.form("form_perfil_v30", clear_on_submit=True):
        perfil_input = st.text_input("Canal YouTube (@handle, link ou ID):")
        enviado = st.form_submit_button("➕ Cadastrar perfil")
        if enviado:
            if not perfil_input.strip():
                st.warning("Informe um canal válido.")
            elif qtd_atual >= limite_canais:
                st.warning("Limite de canais atingido. Exclua um para liberar vaga.")
            else:
                sucesso, status = salvar_perfil_db(email_usuario, perfil_input.strip())
                if sucesso:
                    token, chat_id = credenciais_telegram_usuario(email_usuario)
                    if token and chat_id:
                        enviar_alerta_telegram(token, chat_id,
                            f"📡 ViralRadar: novo canal cadastrado para monitoramento: {perfil_input.strip()}")
                    st.success("Canal cadastrado. Execute a primeira consulta para carregar os dados.")
                    st.rerun()
                else:
                    st.warning(status)

    if perfis_cadastrados:
        if st.button("🔄 Consultar YouTube e atualizar todos", type="primary"):
            progresso = st.progress(0)
            with st.spinner("Consultando canais cadastrados..."):
                for idx, perfil in enumerate(perfis_cadastrados):
                    novo, mensagem, painel = checar_novos_posts_perfil(email_usuario, perfil)
                    salvar_snapshot(email_usuario, perfil, painel, novo)
                    if painel is None:
                        st.warning(mensagem)
                    progresso.progress((idx + 1) / len(perfis_cadastrados))
            st.rerun()

        st.markdown("#### 🔎 Consultar um canal específico")
        perfil_selecionado = st.selectbox("Escolha o canal para consultar:",
                                         perfis_cadastrados, key="seletor_consulta_individual_v31")
        if st.button("🔎 Consultar canal selecionado", key="consultar_selecionado_v31"):
            with st.spinner(f"Consultando {perfil_selecionado}..."):
                novo, mensagem, painel_novo = checar_novos_posts_perfil(email_usuario, perfil_selecionado)
                salvar_snapshot(email_usuario, perfil_selecionado, painel_novo, novo)
            if painel_novo is None:
                st.error(mensagem)
            else:
                st.session_state["resultado_consulta_v31"] = mensagem
                st.rerun()
        if st.session_state.get("resultado_consulta_v31"):
            st.info(st.session_state.pop("resultado_consulta_v31"))

        snapshots = ler_snapshots(email_usuario)
        st.caption("A tabela abaixo lê os últimos resultados salvos, sem consumir a API ao abrir a página.")
        cab = st.columns([3, 2, 2, 2, 2, 2])
        for coluna, titulo in zip(cab, ["Perfil", "▶ YouTube", "TikTok", "Instagram", "Facebook", "X"]):
            coluna.markdown(f"**{titulo}**")
        for perfil in perfis_cadastrados:
            dado = snapshots.get(perfil)
            painel = dado["painel"] if dado else None
            videos = painel.get("videos", []) if painel else []
            ultimo = videos[0] if videos else None
            nome = painel.get("nome", perfil) if painel else perfil
            colunas = st.columns([3, 2, 2, 2, 2, 2])
            colunas[0].write(nome)
            if dado and dado.get("atualizado"):
                data_consulta = data_excel(dado["atualizado"])
                if data_consulta:
                    colunas[0].caption(f"Última consulta: {data_consulta.strftime('%d/%m/%Y %H:%M')} (Brasília)")
            else:
                colunas[0].caption("Ainda não consultado")
            if ultimo:
                simbolo = "🟢" if dado["novo"] else "⚪"
                colunas[1].write(f"{simbolo} {formato_views(ultimo.get('visualizacoes'))}")
            else:
                colunas[1].write("⏳ Pendente")
            for coluna in colunas[2:]:
                coluna.caption("—")
            with st.expander(f"Detalhes · {nome}"):
                if dado:
                    st.caption(f"Última consulta (Brasília): {exibir_horario_br(dado['atualizado'])} | 🟢 Novidade na última consulta · ⚪ Sem novidade")
                    mostrar_resultados_youtube(painel)
                else:
                    st.info("Ainda não consultado. Clique em atualizar todos.")
                if dado and videos:
                    linhas_canal = linhas_relatorio([perfil], {perfil: dado})
                    historico_canal = ler_evolucao_db(email_usuario, [perfil], {perfil: dado})
                    st.download_button(
                        "📊 Baixar relatório Excel deste canal",
                        data=gerar_excel(linhas_canal, historico_canal),
                        file_name=f"viralradar_{''.join(c if c.isalnum() or c in '-_' else '_' for c in nome)[:45]}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"excel_individual_{perfil}")
                a, b = st.columns(2)
                if a.button("🔄 Atualizar somente este perfil", key=f"atualizar_{perfil}"):
                    novo, mensagem, painel_novo = checar_novos_posts_perfil(email_usuario, perfil)
                    salvar_snapshot(email_usuario, perfil, painel_novo, novo)
                    if painel_novo:
                        st.rerun()
                    else:
                        st.error(mensagem)
                if b.button("🗑️ Excluir perfil", key=f"excluir_{perfil}"):
                    excluir_perfil_db(email_usuario, perfil)
                    if qtd_atual >= limite_canais:
                        registrar_substituicao_db(email_usuario)
                    st.rerun()

        st.divider()
        st.subheader("📥 Relatório consolidado (todos os canais)")
        st.caption("Para baixar um canal separadamente, abra 'Detalhes' do canal e use o botão Excel individual.")
        linhas = linhas_relatorio(perfis_cadastrados, snapshots)
        if linhas:
            x, y = st.columns(2)
            x.download_button("📊 Baixar Excel (.xlsx)", gerar_excel(linhas, ler_evolucao_db(email_usuario, perfis_cadastrados, snapshots)),
                              file_name="viralradar_relatorio.xlsx",
                              mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            y.download_button("📄 Baixar TXT", gerar_txt(linhas).encode("utf-8-sig"),
                              file_name="viralradar_relatorio.txt", mime="text/plain")
            st.caption(f"{len(linhas)} vídeos no consolidado. Excel com Resumo Geral, Publicações, Evolução e Gráficos.")
        else:
            st.info("Execute a primeira consulta para habilitar os relatórios.")
    else:
        st.info("Cadastre seu primeiro canal para começar.")

elif opcao == "🔥 Tendências e Assuntos":
    st.subheader("🔥 Tracker de Assuntos e Vídeos em Alta no YouTube")
    st.markdown("""
    - 📈 **Nicho de Podcasts / Cortes:** Formato de cortes verticais com legendas dinâmicas (+300% de retenção)
    - 🎵 **Áudio em Alta:** Efeitos sonoros de transição rápida estilo cinematic
    - 🏷️ **Termos Mais Buscados:** `#shorts`, `tutorial`, `curiosidades`
    """)

elif opcao == "Histórico de Alertas":
    st.subheader("🔔 Histórico de Disparos Recentes")
    historico = ler_historico_db(email_usuario)
    if historico:
        for item in historico:
            st.text(f"📢 {item}")
    else:
        st.info("Nenhum alerta registrado até o momento.")

elif opcao == "Configurar Telegram":
    st.subheader("🤖 Configurações do Telegram")
    token, chat_id_atual = credenciais_telegram_usuario(email_usuario)
    if token:
        st.success("Token do bot encontrado nos Secrets do Streamlit.")
    else:
        st.error("TELEGRAM_BOT_TOKEN não encontrado nos Secrets do Streamlit.")
    st.caption("O Token é protegido no servidor. Cada usuário informa somente o seu Chat ID.")
    with st.form("form_config_telegram_v31"):
        chat_digitado = st.text_input("Seu Telegram Chat ID:", value=chat_id_atual,
                                     help="Abra seu bot no Telegram e envie /start antes do teste.")
        salvar = st.form_submit_button("💾 Salvar Chat ID")
        if salvar:
            chat_digitado = chat_digitado.strip()
            if not chat_digitado or not (chat_digitado.lstrip("-").isdigit()):
                st.warning("Informe um Chat ID numérico válido.")
            else:
                salvar_config_telegram_db(email_usuario, chat_digitado)
                st.success("Chat ID salvo para esta conta!")
                chat_id_atual = chat_digitado
    if st.button("📨 Enviar mensagem de teste", disabled=not bool(token and chat_id_atual)):
        if enviar_alerta_telegram(token, chat_id_atual,
                "🚀 ViralRadar conectado! O envio de alertas do Telegram está funcionando."):
            st.success("Mensagem enviada! Confira a conversa com seu bot no Telegram.")
        else:
            st.error("Não foi possível enviar. Confira o Chat ID e se você iniciou a conversa com /start.")
    st.info("O Chat ID é salvo no Supabase PostgreSQL, fora do servidor do Streamlit.")

elif opcao == "Assinatura / Planos":
    st.subheader("💎 Planos e Assinaturas (ViralRadar)")
    st.caption("Cobranças de teste disponíveis no Asaas Sandbox. A ativação depende da confirmação do webhook e das regras da assinatura.")
    cols = st.columns(3)
    for coluna, (codigo, plano) in zip(cols, PLANOS_VIRALRADAR.items()):
        with coluna:
            st.markdown(f"### {plano['nome']}")
            st.markdown(f"**{plano['mensal']} / mês**")
            st.markdown(f"**{plano['anual']} / ano** (pagamento único)")
            st.write(f"{plano['canais']} canais • {plano['trocas']} trocas mensais • Telegram")
            if st.button(f"Selecionar {plano['nome']}", key=f"plano_{codigo}"):
                st.session_state.plano_checkout = codigo
                st.session_state.pop("asaas_checkout_link", None)
                st.info("Plano selecionado. Abra a seção Finalizar Compra / Pagamento para testar no Asaas Sandbox.")

elif opcao == "Finalizar Compra / Pagamento":
    st.subheader("🛒 Finalizar Assinatura — Checkout Seguro")
    st.info("🧪 Ambiente de testes Asaas Sandbox. Não use cartão ou dinheiro real.")
    if st.session_state.plano_checkout:
        plano_escolhido = st.session_state.plano_checkout
        st.markdown(f"📦 **Cliente:** `{email_usuario}`")
        st.write(f"**Plano:** {PLANOS_VIRALRADAR[plano_escolhido]['nome']}")
        # Fora do formulário: o Streamlit atualiza o preço imediatamente ao trocar o período.
        modalidade_compra = st.radio("Período", ["mensal", "anual"], horizontal=True,
                                    format_func=lambda x: "Mensal" if x == "mensal" else "Anual (12 meses)",
                                    key="asaas_periodo_compra_v2")
        st.write(f"**Valor da assinatura:** R$ {PRECOS_ASAAS[plano_escolhido][modalidade_compra]:.2f}".replace(".", ","))
        with st.form("asaas_checkout_sandbox_v1"):
            forma_compra = st.radio("Pagamento", ["PIX", "CREDIT_CARD"], horizontal=True,
                                  format_func=lambda x: "Pix" if x == "PIX" else "Cartão de crédito")
            nome_pagador = st.text_input("Nome completo do responsável pelo pagamento")
            documento_pagador = st.text_input("CPF ou CNPJ do responsável")
            st.caption("O pagamento ocorre no ambiente seguro do Asaas. Taxas e parcelamento, quando disponíveis, são exibidos lá.")
            confirmar_compra = st.form_submit_button("Gerar cobrança de TESTE no Asaas")
        if confirmar_compra:
            try:
                with st.spinner("Criando cobrança de teste..."):
                    link, pagamento_id, reutilizada = criar_cobranca_asaas(
                        email_usuario, nome_pagador, documento_pagador,
                        plano_escolhido, modalidade_compra, forma_compra)
                st.session_state["asaas_checkout_link"] = link
                st.session_state["asaas_checkout_payment_id"] = pagamento_id
                st.success("Cobrança de teste localizada." if reutilizada else "Cobrança de teste criada e registrada no Supabase!")
            except (ValueError, psycopg2.Error) as exc:
                st.error(str(exc) if isinstance(exc, ValueError) else "Erro ao registrar cobrança no Supabase. Não tente novamente antes de conferir a tabela.")
        if st.session_state.get("asaas_checkout_link"):
            st.link_button("🔒 Abrir pagamento de TESTE no Asaas", st.session_state["asaas_checkout_link"])
            st.caption("A confirmação do pagamento será processada pelo webhook, conforme as regras de elegibilidade da assinatura.")
    else:
        st.warning("⚠️ Nenhum plano selecionado. Visite 'Assinatura / Planos' primeiro.")

elif opcao == "🛡️ Administração":
    # Verificação de autorização no servidor, independente da visibilidade do menu.
    if not eh_administrador(email_usuario):
        st.error("Acesso não autorizado.")
        st.stop()
    st.subheader("🛡️ Central Administrativa — ViralRadar")
    st.caption("Administração de contas e testes de integração com Asaas Sandbox.")
    with st.expander("🔌 Integração Asaas — testar conexão Sandbox", expanded=True):
        st.caption("Teste seguro de leitura. Não cria clientes, cobranças ou assinaturas e não exibe a chave.")
        if st.button("🧪 Testar conexão Asaas Sandbox", key="asaas_sandbox_teste_v1"):
            with st.spinner("Verificando autenticação no Asaas Sandbox..."):
                sucesso_asaas, mensagem_asaas = testar_conexao_asaas_sandbox()
            if sucesso_asaas:
                st.success(mensagem_asaas)
            else:
                st.error(mensagem_asaas)
    st.divider()
    st.subheader("🔎 Gerenciar assinaturas")
    st.caption("Pesquise diretamente no Supabase. A consulta é paginada para funcionar também com muitos assinantes.")
    busca = st.text_input("Buscar por e-mail", placeholder="Digite o e-mail completo ou parte dele", key="admin_busca_email_v2")
    col_status, col_plano, col_modalidade = st.columns(3)
    with col_status:
        filtro_status = st.selectbox("Situação", ["Todos", "ativa", "cancelada", "suspensa", "vencida", "pendente", "sem assinatura"], key="admin_filtro_status_v2")
    with col_plano:
        filtro_plano = st.selectbox("Plano", ["Todos", "inicial", "criador", "agencia", "sem plano"], key="admin_filtro_plano_v2")
    with col_modalidade:
        filtro_modalidade = st.selectbox("Modalidade", ["Todas", "cortesia", "mensal", "anual", "-"], key="admin_filtro_modalidade_v2")
    pagina_solicitada = st.number_input("Página", min_value=1, step=1, value=1, key="admin_pagina_v2")
    try:
        clientes, total, pagina_atual, paginas = listar_clientes_admin(
            busca, filtro_status, filtro_plano, filtro_modalidade, int(pagina_solicitada))
    except psycopg2.Error:
        st.error("Não foi possível consultar as assinaturas. Confira a conexão com o Supabase.")
        st.stop()
    st.metric("Contas encontradas", total)
    st.caption(f"Página {pagina_atual} de {paginas} • Até 25 contas por página")
    if clientes:
        st.dataframe([{
            "E-mail": e, "Plano": p, "Modalidade": m, "Status": status,
            "Vencimento (Brasília)": exibir_horario_br(venc) if venc else "—",
            "Canais": lc, "Trocas": lt
        } for e, p, m, status, venc, lc, lt in clientes],
            use_container_width=True, hide_index=True)
    else:
        st.info("Nenhuma conta corresponde aos filtros selecionados.")

    st.divider()
    st.subheader("🛡️ Gerenciar autorização")
    st.caption("Selecione uma conta encontrada ou informe seu e-mail completo para localizá-la diretamente.")
    contas_encontradas = [r[0] for r in clientes]
    escolha = st.selectbox("Conta encontrada", ["Digitar e-mail manualmente"] + contas_encontradas,
                           key="admin_conta_selecionada_v2")
    email_manual = st.text_input("E-mail exato para administrar", value=busca.strip() if "@" in busca and " " not in busca else "",
                                 key="admin_email_manual_v2") if escolha == "Digitar e-mail manualmente" else ""
    alvo = email_manual.strip().lower() if escolha == "Digitar e-mail manualmente" else escolha
    with st.form("admin_assinatura_form"):
        plano = st.selectbox("Plano", list(PLANOS_VIRALRADAR),
                             format_func=lambda x: PLANOS_VIRALRADAR[x]["nome"])
        modalidade = st.selectbox("Modalidade", ["cortesia", "mensal", "anual"])
        operacao = st.radio("Ação", ["Liberar por prazo", "Suspender", "Cancelar"], horizontal=True)
        dias = st.number_input("Dias de acesso a partir de hoje (para liberação)", min_value=1, max_value=366, value=30)
        confirmar = st.checkbox("Confirmo que desejo alterar a autorização desta conta")
        enviar = st.form_submit_button("Aplicar alteração")
    if enviar and not confirmar:
        st.warning("Marque a caixa de confirmação antes de aplicar a alteração.")
    elif enviar:
        if not alvo:
            st.warning("Selecione uma conta ou informe o e-mail completo.")
        else:
            try:
                conta_confirmada = localizar_conta_admin(alvo)
                if not conta_confirmada:
                    st.error("E-mail não encontrado entre os usuários cadastrados.")
                else:
                    novo_status = {"Liberar por prazo": "ativa", "Suspender": "suspensa", "Cancelar": "cancelada"}[operacao]
                    atualizar_assinatura_admin(conta_confirmada, plano, modalidade, novo_status, int(dias))
                    st.success("Autorização atualizada no Supabase.")
                    st.rerun()
            except (ValueError, psycopg2.Error):
                st.error("Não foi possível atualizar a autorização. Nenhuma confirmação de alteração foi emitida.")
    st.info("A preparação de uma cortesia para pagamento automático será adicionada após validarmos as regras do processador de pagamentos, para evitar liberações indevidas.")

elif opcao == "Suporte":
    st.subheader("💬 Central de Ajuda")
    st.markdown("Fale conosco pelo canal oficial de suporte do ViralRadar.")

