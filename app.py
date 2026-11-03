import streamlit as st
import os
import sqlite3
import hashlib
import requests
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

def salvar_config_telegram_db(email, token, chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO configuracoes (email, telegram_token, telegram_chat_id) VALUES (?, ?, ?)", (email, token, chat_id))
    conn.commit()
    conn.close()

def ler_config_telegram_db(email):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_token, telegram_chat_id FROM configuracoes WHERE email = ?", (email,))
    res = cursor.fetchone()
    conn.close()
    return res if res else ("", "")

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
        return response.status_code == 200
    except Exception as e:
        print(f"Erro ao disparar Telegram: {e}")
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
                token, chat_id = ler_config_telegram_db(email)
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
# CONFIGURAÇÃO DA PÁGINA STREAMLIT
# ==========================================
st.set_page_config(
    page_title="ViralRadar - Monitor de Tendências",
    page_icon="🚀",
    layout="centered"
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
    st.subheader("Adicionar Canal do YouTube para Monitoramento")
    
    st.markdown(f"**Uso do Plano:** {qtd_atual} de {LIMITE_PLANO_INICIAL} canais cadastrados.")
    st.progress(qtd_atual / LIMITE_PLANO_INICIAL if LIMITE_PLANO_INICIAL > 0 else 0)
    
    st.info(f"🔄 **Substituições no mês:** Você usou {trocas_utilizadas} de {LIMITE_TROCAS_MENSAL} trocas permitidas ({trocas_restantes} restantes).")
    
    with st.form("form_perfil_v9", clear_on_submit=True):
        perfil_input = st.text_input("Digite o nome ou ID do canal (ex: MrBeast):")
        enviado = st.form_submit_button("Cadastrar Canal")
        
        if enviado:
            if perfil_input:
                perfil_limpo = perfil_input.strip()
                
                if len(perfis_cadastrados) >= LIMITE_PLANO_INICIAL:
                    if trocas_utilizadas >= LIMITE_TROCAS_MENSAL:
                        st.error(f"⚠️ Limite de {LIMITE_PLANO_INICIAL} canais atingido e suas trocas mensais esgotaram!")
                    else:
                        st.warning("⚠️ Limite de canais atingido. Exclua um canal existente para liberar vaga.")
                else:
                    sucesso, status = salvar_perfil_db(email_usuario, perfil_limpo)
                    if sucesso:
                        st.success(f"Canal '{perfil_limpo}' cadastrado com sucesso!")
                        st.rerun()
                    elif status == "Duplicado":
                        st.warning("Este canal já está cadastrado na sua lista.")
            else:
                st.warning("Por favor, digite um nome de canal válido.")
            
    st.markdown("### 📋 Canais Monitorados")
    
    if perfis_cadastrados:
        for p in perfis_cadastrados:
            st.markdown(f"📺 **`{p}`**")
            
            bcol1, bcol2, bcol3 = st.columns(3)
            with bcol1:
                if st.button(f"▶️ Assistir", key=f"assistir_{p}"):
                    st.info(f"Abrindo canal {p}...")
            with bcol2:
                if st.button(f"📥 Salvar Ref.", key=f"baixar_{p}"):
                    st.success(f"Referência de {p} salva nos favoritos!")
            with bcol3:
                if st.button(f"🗑️ Excluir", key=f"excluir_{p}"):
                    if excluir_perfil_db(email_usuario, p):
                        if qtd_atual >= LIMITE_PLANO_INICIAL:
                            registrar_substituicao_db(email_usuario)
                        st.success(f"Canal {p} removido!")
                        st.rerun()
            st.markdown("---")
        
        if st.button("🔄 Executar Varredura e Checar Novos Vídeos", key="btn_checar_posts"):
            for p in perfis_cadastrados:
                _, resultado, painel = checar_novos_posts_perfil(email_usuario, p)
                st.info(resultado)
                mostrar_resultados_youtube(painel)
    else:
        st.info("Nenhum canal cadastrado ainda. Adicione acima para começar.")

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
    st.subheader("🤖 Configurações do Bot do Telegram")
    st.markdown("Insira o Token do seu Bot do Telegram e o Chat ID para receber os alertas instantâneos de novos vídeos.")
    
    token_atual, chat_id_atual = ler_config_telegram_db(email_usuario)
    
    with st.form("form_config_telegram"):
        t_input = st.text_input("Telegram Bot Token:", value=token_atual, type="password")
        c_input = st.text_input("Telegram Chat ID:", value=chat_id_atual)
        btn_salvar_config = st.form_submit_button("Salvar Configurações")
        
        if btn_salvar_config:
            salvar_config_telegram_db(email_usuario, t_input, c_input)
            st.success("Configurações do Telegram salvas com sucesso!")
            
            if enviar_alerta_telegram(t_input, c_input, "🚀 *ViralRadar Conectado ao YouTube!* Alertas ativados."):
                st.info("Mensagem de teste enviada para o seu Telegram!")

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

# ==========================================
# TESTE REAL DA API DO YOUTUBE
# ==========================================

def testar_api_youtube():
    try:
        api_key = st.secrets["YOUTUBE_API_KEY"]

        resposta = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet",
                "chart": "mostPopular",
                "regionCode": "BR",
                "maxResults": 1,
                "key": api_key
            },
            timeout=15
        )

        dados = resposta.json()

        if resposta.status_code == 200:
            st.success("YouTube conectado com sucesso!")

            for video in dados.get("items", []):
                st.write(
                    "Vídeo encontrado:",
                    video["snippet"]["title"]
                )
        else:
            st.error(
                dados.get("error", {}).get(
                    "message", "Erro desconhecido"
                )
            )

    except KeyError:
        st.error("Chave API não encontrada nos Secrets.")
    except requests.RequestException as erro:
        st.error(f"Erro de conexão: {erro}")


st.divider()
st.subheader("Teste de conexão com YouTube")

if st.button("Testar API do YouTube"):
    testar_api_youtube()
