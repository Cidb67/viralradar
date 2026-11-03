"""ViralRadar 3.4 — monitoramento agendado, sem dependência do Streamlit.
Executar via GitHub Actions. Segredos exclusivamente em variáveis de ambiente.
"""
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import psycopg2
import requests

API = 'https://www.googleapis.com/youtube/v3/'


def db():
    return psycopg2.connect(
        host=os.environ['SUPABASE_HOST'], port=int(os.environ['SUPABASE_PORT']),
        dbname=os.environ['SUPABASE_DATABASE'], user=os.environ['SUPABASE_USER'],
        password=os.environ['SUPABASE_PASSWORD'], sslmode='require', connect_timeout=12,
    )


def youtube(endpoint, **params):
    r = requests.get(API + endpoint, params={**params, 'key': os.environ['YOUTUBE_API_KEY']}, timeout=20)
    r.raise_for_status()
    return r.json()


def resolve(entrada):
    entrada = entrada.strip()
    if entrada.startswith(('https://', 'http://')):
        url = urlparse(entrada)
        if url.hostname not in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            raise ValueError('URL do canal inválida')
        partes = url.path.strip('/').split('/')
        if partes[0] == 'channel' and len(partes) > 1:
            entrada = partes[1]
        elif partes[0].startswith('@'):
            entrada = partes[0]
        else:
            raise ValueError('Formato de canal não suportado')
    params = {'part': 'snippet,contentDetails'}
    if entrada.startswith('UC') and len(entrada) == 24:
        params['id'] = entrada
    else:
        params['forHandle'] = entrada.lstrip('@')
    itens = youtube('channels', **params).get('items', [])
    if not itens:
        raise ValueError('Canal não encontrado')
    c = itens[0]
    return c['id'], c['snippet']['title'], c['contentDetails']['relatedPlaylists']['uploads']


def enviar(token, chat_id, nome, video_id):
    if not token or not chat_id:
        return False
    r = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': str(chat_id), 'text': f'🚨 NOVO VÍDEO NO YOUTUBE!\nCanal: {nome}\nhttps://www.youtube.com/watch?v={video_id}'},
        timeout=20,
    )
    # Nunca incluir URL/token em logs.
    if r.status_code != 200:
        print(f'Falha no Telegram: HTTP {r.status_code}')
        return False
    return True


def monitorar(email, perfil):
    canal_id, nome, uploads = resolve(perfil)
    itens = youtube('playlistItems', part='contentDetails', playlistId=uploads, maxResults=10).get('items', [])
    ids = [x.get('contentDetails', {}).get('videoId') for x in itens]
    ids = [x for x in ids if x]
    if not ids:
        print(f'{nome}: nenhum vídeo encontrado')
        return
    detalhes = youtube('videos', part='snippet,statistics', id=','.join(ids[:5])).get('items', [])
    por_id = {v['id']: v for v in detalhes}
    videos = []
    coleta = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        with conn.cursor() as cur:
            # Evita duas execuções simultâneas para o mesmo canal.
            cur.execute('SELECT ultimo_post_id FROM perfis WHERE email=%s AND perfil=%s FOR UPDATE', (email, perfil))
            registro = cur.fetchone()
            if not registro:
                return
            anterior = registro[0]
            cur.execute('SELECT telegram_chat_id FROM configuracoes WHERE email=%s', (email,))
            chat = cur.fetchone()
            chat_id = chat[0] if chat else None
            for vid in ids[:5]:
                item = por_id.get(vid)
                if not item:
                    continue
                sn, stats = item.get('snippet', {}), item.get('statistics', {})
                views = int(stats['viewCount']) if 'viewCount' in stats else None
                likes = int(stats['likeCount']) if 'likeCount' in stats else None
                comments = int(stats['commentCount']) if 'commentCount' in stats else None
                cur.execute('''SELECT visualizacoes FROM video_metricas
                    WHERE email=%s AND canal_id=%s AND video_id=%s
                    ORDER BY coletado_em DESC LIMIT 1''', (email, canal_id, vid))
                prev = cur.fetchone()
                delta = views - prev[0] if prev and prev[0] is not None and views is not None else None
                videos.append({
                    'id': vid, 'titulo': sn.get('title', 'Sem título'),
                    'publicado': sn.get('publishedAt', ''),
                    'imagem': sn.get('thumbnails', {}).get('medium', {}).get('url'),
                    'url': f'https://www.youtube.com/watch?v={vid}',
                    'visualizacoes': views, 'curtidas': likes,
                    'comentarios': comments, 'delta_views': delta,
                })
                cur.execute('''INSERT INTO video_metricas
                    (email, canal_id, video_id, visualizacoes, curtidas, comentarios, coletado_em)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (email, canal_id, vid, views, likes, comments, coleta))
            mais_recente = ids[0]
            primeira = not anterior or anterior == 'nenhum' or anterior.startswith('yt_video_')
            if primeira or anterior not in ids:
                # Primeira leitura é referência, não dispara alertas antigos.
                cur.execute('UPDATE perfis SET ultimo_post_id=%s WHERE email=%s AND perfil=%s',
                            (mais_recente, email, perfil))
                novos = []
                print(f'{nome}: referência sincronizada')
            else:
                novos = ids[:ids.index(anterior)]
                if novos:
                    if not chat_id:
                        print(f'{nome}: sem Chat ID; não avançou referência para evitar perder alerta')
                    else:
                        # Atualiza a referência por vídeo enviado para reduzir duplicações em falhas parciais.
                        for vid in reversed(novos):
                            if not enviar(os.environ['TELEGRAM_BOT_TOKEN'], chat_id, nome, vid):
                                raise RuntimeError(f'{nome}: envio Telegram falhou')
                            cur.execute('UPDATE perfis SET ultimo_post_id=%s WHERE email=%s AND perfil=%s',
                                        (vid, email, perfil))
                        print(f'{nome}: {len(novos)} novo(s) vídeo(s) notificado(s)')
                else:
                    print(f'{nome}: sem vídeos novos')
            painel = {'nome': nome, 'videos': videos, 'coletado_em': coleta}
            cur.execute('''INSERT INTO snapshots_perfil (email, perfil, painel_json, atualizado_em, novo)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(email,perfil) DO UPDATE SET
                painel_json=EXCLUDED.painel_json, atualizado_em=EXCLUDED.atualizado_em,
                novo=EXCLUDED.novo''',
                (email, perfil, json.dumps(painel, ensure_ascii=False), coleta, int(bool(novos))))


def main():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT email, perfil FROM perfis ORDER BY email, perfil')
            perfis = cur.fetchall()
    print(f'ViralRadar: {len(perfis)} canais para consultar')
    erros = 0
    for email, perfil in perfis:
        try:
            monitorar(email, perfil)
        except (requests.RequestException, psycopg2.Error, ValueError, KeyError, RuntimeError) as exc:
            # Evita imprimir exceções HTTP com URLs contendo chaves de API.
            print(f'Erro ao consultar canal {perfil}: {type(exc).__name__}')
            erros += 1
    if erros:
        print(f'Execução encerrada com {erros} erro(s)')
        sys.exit(1)
    print('Execução concluída')


if __name__ == '__main__':
    main()
