import streamlit as st
import os
import sqlite3
import hashlib
import requests
import json
import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from datetime import datetime, timezone
from urllib.parse import urlparse

# Nome do Banco de Dados SQLite
DB_NAME = "viralradar.db"
LIMITE_PLANO_INICIAL = 10
LIMITE_TROCAS_MENSAL = 5

# ==========================================
# CONFIGURAÇÃO DO BANCO DE DADOS (SQLite)
# ==========================================
def inicializar_banco():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Tabela de Usuários (com senha em hash)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            email TEXT PRIMARY KEY,
            senha TEXT NOT NULL
        )
    ''')
    
    # Tabela de Canais / Perfis Monitorados por Usuário
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS perfis (
            email TEXT,
            perfil TEXT,
            ultimo_post_id TEXT,
            PRIMARY KEY (email, perfil)
        )
    ''')
    
    # Garante que a coluna 'ultimo_post_id' exista em bancos criados anteriormente
    cursor.execute("PRAGMA table_info(perfis)")
    colunas = [coluna[1] for coluna in cursor.fetchall()]
    if 'ultimo_post_id' not in colunas:
        cursor.execute("ALTER TABLE perfis ADD COLUMN ultimo_post_id TEXT")
    
    # Tabela de Métricas / Substituições por Usuário (Garante 0 inicial)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS metricas (
            email TEXT PRIMARY KEY,
            trocas INTEGER DEFAULT 0
        )
    ''')
    
    # Tabela de Histórico de Alertas
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mensagem TEXT,
            data TEXT
        )
    ''')
    
    # Tabela de Configurações do Telegram por Usuário
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS configuracoes (
            email TEXT PRIMARY KEY,
            telegram_token TEXT,
            telegram_chat_id TEXT
        )
    ''')
    
    cursor.execute("""CREATE TABLE IF NOT EXISTS video_metricas (
        email TEXT NOT NULL, canal_id TEXT NOT NULL, video_id TEXT NOT NULL,
        visualizacoes INTEGER, curtidas INTEGER, comentarios INTEGER,
        coletado_em TEXT NOT NULL,
        PRIMARY KEY(email, canal_id, video_id, coletado_em)
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS snapshots_perfil (
        email TEXT NOT NULL, perfil TEXT NOT NULL, painel_json TEXT NOT NULL,
        atualizado_em TEXT NOT NULL, novo INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(email, perfil)
    )""")
    conn.commit()
    conn.close()

# Inicializa o banco ao iniciar o app
inicializar_banco()

# Função para gerar Hash SHA-256 da senha
def criar_hash_senha(senha):
    return hashlib.sha256(senha.encode('utf-8')).hexdigest()

# ==========================================
# FUNÇÕES DE MANIPULAÇÃO DE DADOS
# ==========================================
def cadastrar_usuario_db(email, senha):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        senha_hash = criar_hash_senha(senha)
        cursor.execute("INSERT INTO usuarios (email, senha) VALUES (?, ?)", (email, senha_hash))
        cursor.execute("INSERT OR REPLACE INTO metricas (email, trocas) VALUES (?, 0)", (email,))
        conn.commit()
        conn.close()
        return True, "Cadastro realizado com sucesso!"
    except sqlite3.IntegrityError:
        conn.close()
        return False, "E-mail já cadastrado!"

def verificar_login_db(email, senha):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    senha_hash = criar_hash_senha(senha)
    cursor.execute("SELECT * FROM usuarios WHERE email = ? AND senha = ?", (email, senha_hash))
    usuario = cursor.fetchone()
    conn.close()
    return usuario is not None

def ler_perfis_db(email):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT perfil FROM perfis WHERE email = ?", (email,))
    resultados = [row[0] for row in cursor.fetchall()]
    conn.close()
    return resultados

def salvar_perfil_db(email, novo_perfil):
    perfis = ler_perfis_db(email)
    if len(perfis) >= LIMITE_PLANO_INICIAL:
        return False, "Limite atingido"
    
    if novo_perfil not in perfis:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO perfis (email, perfil, ultimo_post_id) VALUES (?, ?, ?)", (email, novo_perfil, "nenhum"))
        conn.commit()
        conn.close()
        return True, "Sucesso"
    return False, "Duplicado"

def excluir_perfil_db(email, perfil_alvo):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM perfis WHERE email = ? AND perfil = ?", (email, perfil_alvo))
    conn.execute("DELETE FROM snapshots_perfil WHERE email=? AND perfil=?", (email, perfil_alvo))
    conn.commit()
    conn.close()
    return True

def ler_trocas_db(email):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT trocas FROM metricas WHERE email = ?", (email,))
    res = cursor.fetchone()
    if res is None:
        cursor.execute("INSERT OR IGNORE INTO metricas (email, trocas) VALUES (?, 0)", (email,))
        conn.commit()
        conn.close()
        return 0
    conn.close()
    return res[0]

def salvar_troca_db(email, qtd):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO metricas (email, trocas) VALUES (?, ?)", (email, qtd))
    conn.commit()
    conn.close()

def registrar_substituicao_db(email):
    trocas_feitas = ler_trocas_db(email)
    if trocas_feitas < LIMITE_TROCAS_MENSAL:
        salvar_troca_db(email, trocas_feitas + 1)
        return True
    return False

def registrar_historico_db(mensagem):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    data_atual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    cursor.execute("INSERT INTO historico (mensagem, data) VALUES (?, ?)", (mensagem, data_atual))
    conn.commit()
    conn.close()

def ler_historico_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT data, mensagem FROM historico ORDER BY id DESC")
    resultados = [f"{row[0]} — {row[1]}" for row in cursor.fetchall()]
    conn.close()
    return resultados

def salvar_config_telegram_db(email, chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO configuracoes (email, telegram_token, telegram_chat_id) VALUES (?, ?, ?)", (email, "", chat_id))
    conn.commit()
    conn.close()

def ler_config_telegram_db(email):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_token, telegram_chat_id FROM configuracoes WHERE email = ?", (email,))
    res = cursor.fetchone()
    conn.close()
    return res if res else ("", "")


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
        with sqlite3.connect(DB_NAME) as conn:
            linha = conn.execute(
                "SELECT ultimo_post_id FROM perfis WHERE email=? AND perfil=?",
                (email, canal)
            ).fetchone()
            if linha is None:
                return False, "Canal não cadastrado.", None
            anterior = linha[0]
            # Guarda a medição anterior antes de registrar a nova.
            for video in videos:
                antiga = conn.execute("""SELECT visualizacoes FROM video_metricas
                    WHERE email=? AND canal_id=? AND video_id=?
                    ORDER BY coletado_em DESC LIMIT 1""",
                    (email, canal_id, video["id"])).fetchone()
                video["delta_views"] = (video["visualizacoes"] - antiga[0]
                    if antiga and antiga[0] is not None and video["visualizacoes"] is not None else None)
                conn.execute("""INSERT INTO video_metricas
                    (email, canal_id, video_id, visualizacoes, curtidas, comentarios, coletado_em)
                    VALUES (?,?,?,?,?,?,?)""", (email, canal_id, video["id"],
                    video["visualizacoes"], video["curtidas"], video["comentarios"], coletado_em))

            mais_recente = ids[0]
            primeira = not anterior or anterior == "nenhum" or anterior.startswith("yt_video_")
            novos = [] if primeira or anterior == mais_recente or anterior not in ids else ids[:ids.index(anterior)]
            if primeira or anterior not in ids:
                conn.execute("UPDATE perfis SET ultimo_post_id=? WHERE email=? AND perfil=?",
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
                    conn.execute("UPDATE perfis SET ultimo_post_id=? WHERE email=? AND perfil=?",
                                 (mais_recente, email, canal))
                mensagem = (f"{nome}: {len(novos)} novo(s) vídeo(s) detectado(s)." if not falha
                            else f"{nome}: falha ao enviar Telegram; tentativa será repetida.")
            else:
                mensagem = f"{nome}: sem vídeos novos; métricas atualizadas."
        return bool(novos), mensagem, {"nome": nome, "videos": videos, "coletado_em": coletado_em}
    except (requests.RequestException, ValueError, KeyError, sqlite3.Error) as erro:
        return False, f"Erro ao consultar {canal}: {erro}", None


def mostrar_resultados_youtube(painel):
    if not painel:
        return
    st.markdown(f"#### {painel['nome']} — últimos 5 vídeos")
    st.caption(f"Consulta em UTC: {painel['coletado_em'][:19].replace('T', ' ')}")
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


# ==========================================
# CACHE DE RESULTADOS E EXPORTAÇÃO
# ==========================================
def salvar_snapshot(email, perfil, painel, novo):
    if painel is None:
        return
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""INSERT INTO snapshots_perfil
            (email, perfil, painel_json, atualizado_em, novo)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email, perfil) DO UPDATE SET
            painel_json=excluded.painel_json,
            atualizado_em=excluded.atualizado_em,
            novo=excluded.novo""",
            (email, perfil, json.dumps(painel, ensure_ascii=False),
             painel.get("coletado_em", datetime.now(timezone.utc).isoformat()), int(novo)))


def ler_snapshots(email):
    with sqlite3.connect(DB_NAME) as conn:
        registros = conn.execute("""SELECT perfil, painel_json, atualizado_em, novo
            FROM snapshots_perfil WHERE email=?""", (email,)).fetchall()
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
                "Publicado em (UTC)": video.get("publicado", ""),
                "Visualizações": video.get("visualizacoes"),
                "Variação de visualizações": video.get("delta_views"),
                "Curtidas": video.get("curtidas"),
                "Comentários": video.get("comentarios"),
                "Link": video.get("url", ""),
                "Consultado em (UTC)": dado["atualizado"]
            })
    return linhas


def gerar_excel(linhas):
    wb = Workbook()
    ws = wb.active
    ws.title = "Vídeos monitorados"
    colunas = ["Perfil", "Nome do canal", "Rede", "Posição", "Título",
               "Publicado em (UTC)", "Visualizações", "Variação de visualizações",
               "Curtidas", "Comentários", "Link", "Consultado em (UTC)"]
    ws.append(colunas)
    for celula in ws[1]:
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor="183153")
    for linha in linhas:
        ws.append([linha.get(coluna) for coluna in colunas])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        letra = col[0].column_letter
        ws.column_dimensions[letra].width = min(55, max(14, max(len(str(c.value or "")) for c in col) + 2))
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
                    st.session_state.usuario_atual = email_login
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
                        st.session_state.usuario_atual = email_cad
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

with st.sidebar:
    st.header("📌 Menu")
    opcao = st.radio(
        "Seção:", 
        [
            "Painel Principal", 
            "🔥 Tendências e Assuntos", 
            "Histórico de Alertas", 
            "Configurar Telegram",
            "Assinatura / Planos", 
            "Finalizar Compra / Pagamento", 
            "Suporte"
        ], 
        key="menu_principal_viralradar_v9"
    )
    st.markdown("---")
    st.markdown("**Status do Sistema:** 🟢 Online (YouTube API)")
    
    if st.button("🚪 Sair da Conta (Logout)", key="btn_logout_v9"):
        st.session_state.logado = False
        st.session_state.usuario_atual = ""
        st.rerun()
        
    if st.button("🔄 Atualizar Tela", key="btn_atualizar_geral_v9"):
        st.rerun()

email_usuario = st.session_state.usuario_atual
perfis_cadastrados = ler_perfis_db(email_usuario)
qtd_atual = len(perfis_cadastrados)
trocas_utilizadas = ler_trocas_db(email_usuario)
trocas_restantes = LIMITE_TROCAS_MENSAL - trocas_utilizadas

if opcao == "Painel Principal":
    st.subheader("📡 ViralRadar 3.0 — Central multirredes")
    st.caption("YouTube integrado • TikTok, Instagram, Facebook e X: integrações futuras, sem coleta ainda")
    st.markdown(f"**Perfis cadastrados:** {qtd_atual} de {LIMITE_PLANO_INICIAL} | **Trocas usadas:** {trocas_utilizadas} de {LIMITE_TROCAS_MENSAL}")

    with st.form("form_perfil_v30", clear_on_submit=True):
        perfil_input = st.text_input("Canal YouTube (@handle, link ou ID):")
        enviado = st.form_submit_button("➕ Cadastrar perfil")
        if enviado:
            if not perfil_input.strip():
                st.warning("Informe um canal válido.")
            elif qtd_atual >= LIMITE_PLANO_INICIAL:
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
            if ultimo:
                simbolo = "🟢" if dado["novo"] else "⚪"
                colunas[1].write(f"{simbolo} {formato_views(ultimo.get('visualizacoes'))}")
            else:
                colunas[1].write("⏳ Pendente")
            for coluna in colunas[2:]:
                coluna.caption("—")
            with st.expander(f"Detalhes · {nome}"):
                if dado:
                    st.caption(f"Última consulta (UTC): {dado['atualizado']} | 🟢 Novidade na última consulta · ⚪ Sem novidade")
                    mostrar_resultados_youtube(painel)
                else:
                    st.info("Ainda não consultado. Clique em atualizar todos.")
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
                    if qtd_atual >= LIMITE_PLANO_INICIAL:
                        registrar_substituicao_db(email_usuario)
                    st.rerun()

        st.divider()
        st.subheader("📥 Exportar relatórios")
        linhas = linhas_relatorio(perfis_cadastrados, snapshots)
        if linhas:
            x, y = st.columns(2)
            x.download_button("📊 Baixar Excel (.xlsx)", gerar_excel(linhas),
                              file_name="viralradar_relatorio.xlsx",
                              mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            y.download_button("📄 Baixar TXT", gerar_txt(linhas).encode("utf-8-sig"),
                              file_name="viralradar_relatorio.txt", mime="text/plain")
            st.caption(f"{len(linhas)} vídeos no relatório, com métricas da última consulta registrada.")
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
    historico = ler_historico_db()
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
    st.info("Nesta versão, o Chat ID é salvo no SQLite do Streamlit Cloud. "
            "Ele pode desaparecer após reinicializações. Antes de vender assinaturas, "
            "vamos migrar os cadastros para um banco persistente.")

elif opcao == "Assinatura / Planos":
    st.subheader("💎 Planos e Assinaturas (ViralRadar)")
    st.markdown("Escolha o plano ideal para o seu volume de monitoramento:")
    
    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        st.markdown("### 🥉 Inicial")
        st.markdown("**R$ 29,90 / mês**")
        st.markdown("• 5 Canais/Perfis\n• Alertas via Telegram\n• Histórico básico")
        if st.button("💳 Assinar Inicial", key="plan_inicial"):
            st.session_state.plano_checkout = "Plano Inicial — R$ 29,90/mês"
            st.success("Plano selecionado! Vá até a aba de Pagamento.")
    with col_p2:
        st.markdown("### 🥈 Criador")
        st.markdown("**R$ 59,90 / mês**")
        st.markdown("• 15 Canais/Perfis\n• Alertas prioritários\n• 5 Trocas mensais")
        if st.button("💳 Assinar Criador", key="plan_criador"):
            st.session_state.plano_checkout = "Plano Criador — R$ 59,90/mês"
            st.success("Plano selecionado! Vá até a aba de Pagamento.")
    with col_p3:
        st.markdown("### 🥇 Agência")
        st.markdown("**R$ 119,90 / mês**")
        st.markdown("• 40 Canais/Perfis\n• Monitoramento em tempo real\n• Suporte dedicado")
        if st.button("💳 Assinar Agência", key="plan_agencia"):
            st.session_state.plano_checkout = "Plano Agência — R$ 119,90/mês"
            st.success("Plano selecionado! Vá até a aba de Pagamento.")

elif opcao == "Finalizar Compra / Pagamento":
    st.subheader("🛒 Finalizar Assinatura — Checkout Seguro")
    if st.session_state.plano_checkout:
        st.markdown(f"📦 **Cliente Logado:** `{st.session_state.usuario_atual}`")
        st.info(f"**Plano Escolhido:** {st.session_state.plano_checkout}")
        if st.button("🚀 Confirmar e Pagar Agora via Pix", key="btn_concluir_pagamento_v9"):
            st.success("🎉 Pagamento simulado com sucesso! Acesso liberado no sistema!")
    else:
        st.warning("⚠️ Nenhum plano selecionado ainda! Visite a aba 'Assinatura / Planos' primeiro.")

elif opcao == "Suporte":
    st.subheader("💬 Central de Ajuda")
    st.markdown("Fale conosco pelo canal oficial de suporte do ViralRadar.")

