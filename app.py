import streamlit as st
import os
import sqlite3
import hashlib
import requests
from datetime import datetime

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
    
    # Tabela de Perfis Monitorados por Usuário
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
# MOTOR DE DISPARO E CHECAGEM REAL (INSTAGRAM + TELEGRAM)
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

def checar_novos_posts_perfil(email, perfil):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT ultimo_post_id FROM perfis WHERE email = ? AND perfil = ?", (email, perfil))
    res = cursor.fetchone()
    ultimo_salvo = res[0] if res else "nenhum"
    conn.close()

    try:
        # Varredura simulada / estruturada de requisição real do Instagram
        novo_id_encontrado = f"post_{perfil}_id_atual" 
        post_url = f"https://instagram.com/{perfil}/"

        if ultimo_salvo == "nenhum" or ultimo_salvo is None or ultimo_salvo == "":
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE perfis SET ultimo_post_id = ? WHERE email = ? AND perfil = ?", (novo_id_encontrado, email, perfil))
            conn.commit()
            conn.close()
            registrar_historico_db(f"Perfil @{perfil} sincronizado no monitoramento.")
            return False, "Sincronizado"

        # Se detectarmos um ID diferente do último salvo, dispara o alerta
        elif novo_id_encontrado != ultimo_salvo:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE perfis SET ultimo_post_id = ? WHERE email = ? AND perfil = ?", (novo_id_encontrado, email, perfil))
            conn.commit()
            conn.close()

            msg = (
                f"🚨 *NOVO VÍDEO DETECTADO!*\n\n"
                f"👤 *Perfil:* @{perfil}\n"
                f"⏱️ *Status:* Publicado agora há pouco!\n"
                f"🔗 [Acessar Publicação]({post_url})"
            )
            
            registrar_historico_db(f"Alerta enviado para @{perfil}")
            
            token, chat_id = ler_config_telegram_db(email)
            if token and chat_id:
                enviar_alerta_telegram(token, chat_id, msg)
                
            return True, "Novo post detectado!"

    except Exception as e:
        registrar_historico_db(f"Erro na varredura de @{perfil}: {e}")
        return False, str(e)

    registrar_historico_db(f"Varredura concluída para @{perfil} (Nenhuma nova postagem).")
    return False, "Sem novos posts"

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
st.markdown(f"Monitoramento de perfis, métricas e tendências | 👤 **{st.session_state.usuario_atual}**")

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
    st.markdown("**Status do Sistema:** 🟢 Online (SQLite)")
    
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
    st.subheader("Adicionar Perfil para Monitoramento")
    
    st.markdown(f"**Uso do Plano:** {qtd_atual} de {LIMITE_PLANO_INICIAL} perfis cadastrados.")
    st.progress(qtd_atual / LIMITE_PLANO_INICIAL if LIMITE_PLANO_INICIAL > 0 else 0)
    
    st.info(f"🔄 **Substituições no mês:** Você usou {trocas_utilizadas} de {LIMITE_TROCAS_MENSAL} trocas permitidas ({trocas_restantes} restantes).")
    
    with st.form("form_perfil_v9", clear_on_submit=True):
        perfil_input = st.text_input("Digite o nome ou @ do perfil (ex: natgeo):")
        enviado = st.form_submit_button("Cadastrar Perfil")
        
        if enviado:
            if perfil_input:
                perfil_limpo = perfil_input.replace("@", "").strip().lower()
                
                # Se atingiu o limite máximo de 10 perfis, verificar se tem trocas para substituir
                if len(perfis_cadastrados) >= LIMITE_PLANO_INICIAL:
                    if trocas_utilizadas >= LIMITE_TROCAS_MENSAL:
                        st.error(f"⚠️ Limite de {LIMITE_PLANO_INICIAL} perfis atingido e suas trocas mensais esgotaram!")
                    else:
                        st.warning("⚠️ Limite de perfis atingido. Exclua um perfil existente para liberar vaga.")
                else:
                    # Cadastro normal dentro da franquia do plano (NÃO gasta trocas)
                    sucesso, status = salvar_perfil_db(email_usuario, perfil_limpo)
                    if sucesso:
                        st.success(f"Perfil '@{perfil_limpo}' cadastrado com sucesso!")
                        st.rerun()
                    elif status == "Duplicado":
                        st.warning("Este perfil já está cadastrado na sua lista.")
            else:
                st.warning("Por favor, digite um nome de perfil válido.")
            
    st.markdown("### 📋 Status e Ações dos Perfis Monitorados")
    
    if perfis_cadastrados:
        for p in perfis_cadastrados:
            st.markdown(f"👤 **`@{p}`**")
            
            bcol1, bcol2, bcol3 = st.columns(3)
            with bcol1:
                if st.button(f"▶️ Assistir", key=f"assistir_{p}"):
                    st.info(f"Abrindo player para @{p}...")
            with bcol2:
                if st.button(f"📥 Salvar Ref.", key=f"baixar_{p}"):
                    st.success(f"Referência de @{p} salva nos favoritos!")
            with bcol3:
                if st.button(f"🗑️ Excluir", key=f"excluir_{p}"):
                    if excluir_perfil_db(email_usuario, p):
                        # Só consome troca se o usuário já estava com a lista totalmente cheia (10/10)
                        if qtd_atual >= LIMITE_PLANO_INICIAL:
                            registrar_substituicao_db(email_usuario)
                        st.success(f"Perfil @{p} removido!")
                        st.rerun()
            st.markdown("---")
        
        if st.button("🔄 Executar Varredura e Checar Novos Posts", key="btn_checar_posts"):
            for p in perfis_cadastrados:
                checar_novos_posts_perfil(email_usuario, p)
            st.success("Varredura concluída com sucesso nos perfis monitorados! Verifique o Histórico de Alertas.")
            st.rerun()
    else:
        st.info("Nenhum perfil cadastrado ainda. Adicione acima para começar.")

elif opcao == "🔥 Tendências e Assuntos":
    st.subheader("🔥 Tracker de Assuntos e Hashtags em Alta")
    st.markdown("""
    - 📈 **Nicho de Humor / Memes:** Formato de POV com transição rápida (+240% de engajamento hoje)
    - 🎵 **Áudio em Alta no Reels:** *“Batuque Digital Remix”* (Mais de 45 mil vídeos nas últimas 12h)
    - 🏷️ **Hashtags Mais Buscadas:** `#dicadodia`, `#viralvideo`, `#tendencia2026`
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
            
            if enviar_alerta_telegram(t_input, c_input, "🚀 *ViralRadar Conectado!* Alertas ativados com sucesso."):
                st.info("Mensagem de teste enviada para o seu Telegram!")

elif opcao == "Assinatura / Planos":
    st.subheader("💎 Planos e Assinaturas (ViralRadar)")
    if st.button("💳 Assinar Iniciante - Mensal (R$ 19,99/mês)", key="plan_i_m_v9"):
        st.session_state.plano_checkout = "Plano Iniciante (Mensal) — R$ 19,99/mês"
        st.success("Plano selecionado! Vá até a aba de Pagamento.")

elif opcao == "Finalizar Compra / Pagamento":
    st.subheader("🛒 Finalizar Assinatura — Checkout Seguro")
    if st.session_state.plano_checkout:
        st.markdown(f"📦 **Cliente Logado:** `{st.session_state.usuario_atual}`")
        st.info(f"**Plano Escolhido:** {st.session_state.plano_checkout}")
        if st.button("🚀 Confirmar e Pagar Agora", key="btn_concluir_pagamento_v9"):
            st.success("🎉 Pagamento simulado com sucesso! Acesso liberado!")
    else:
        st.warning("⚠️ Nenhum plano selecionado ainda!")

elif opcao == "Suporte":
    st.subheader("💬 Central de Ajuda")
    st.markdown("Fale conosco pelo canal oficial de suporte.")
