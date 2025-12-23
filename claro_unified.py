#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
🚀 CLARO TV+ UNIFIED - Versão Completa
================================================================================
Combina as funcionalidades de:
- clarodrm.py (Interface Web + Player Shaka + DRM nativo)
- infinity_final.py (Streamlink + Pywidevine + MPEG-TS)

Desenvolvido para máxima compatibilidade e performance.
================================================================================
"""

import os
import sys
import json
import time
import random
import string
import shutil
import re
import uuid
import logging
import threading
import subprocess
import urllib.parse
from datetime import datetime
from functools import lru_cache

# Bibliotecas de terceiros
import requests
import urllib3
import dns.resolver
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Flask
from flask import Flask, render_template_string, request, Response, jsonify, stream_with_context

# Pywidevine (opcional - para modo MPEG-TS)
try:
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device
    from pywidevine.pssh import PSSH
    PYWIDEVINE_AVAILABLE = True
except ImportError:
    PYWIDEVINE_AVAILABLE = False
    print("⚠️  Pywidevine não instalado. Modo MPEG-TS desabilitado.")

# Hypercorn (para HTTP/2 + HTTPS)
try:
    from hypercorn.config import Config
    from hypercorn.asyncio import serve
    import asyncio
    HYPERCORN_AVAILABLE = True
except ImportError:
    HYPERCORN_AVAILABLE = False

# ================================================================================
# CONFIGURAÇÕES GERAIS
# ================================================================================

# Silencia logs desnecessários
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('hypercorn.access').setLevel(logging.ERROR)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'claro_unified_secret_2024')

# ================================================================================
# CONFIGURAÇÃO - EDITE AQUI
# ================================================================================

class Config:
    # Servidor
    PORTA_HTTPS = int(os.environ.get('PORT', 8443))
    CERT_FILE = "cert.pem"
    KEY_FILE = "key.pem"
    
    # Arquivos de dados
    COOKIES_FILE = "cookies_finais.txt"
    CDN_COOKIES_FILE = "cookies_cdn.json"
    DEVICE_FILE = "device.wvd"
    
    # Credenciais (para modo de login direto)
    USERNAME = os.environ.get('CLARO_USER', 'nocbrasil')
    PASSWORD = os.environ.get('CLARO_PASS', 'n0cbr4s1l')
    
    # URLs da API Claro
    API_BASE = "https://androidtv-mediation-layer.clarobrasil.mobi"
    URL_CATALOGO = f"{API_BASE}/api/v1/catalog/content_providers"
    URL_PLAYBACK = f"{API_BASE}/api/entitlement/playback"
    URL_AUTH_TOKEN = f"{API_BASE}/api/v1/auth/token"
    URL_AUTH_LOGIN = f"{API_BASE}/api/auth/login"
    
    # Youbora Analytics
    YOUBORA_HOST = "https://infinity-c35.youboranqs01.com"
    YOUBORA_ACCOUNT = "clarobrasildev"
    
    # Configurações de sessão
    ONLINE_TIMEOUT_SECONDS = 300  # 5 minutos
    
    # Localização padrão
    DEFAULT_CITY = "Sao Paulo"
    DEFAULT_STATE = "Sao Paulo"

# ================================================================================
# HEADERS TEMPLATES
# ================================================================================

HEADERS_TIZEN = {
    'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0',
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    'Origin': 'https://development.3ss.tv',
    'Referer': 'https://development.3ss.tv/',
    'x-api-key': 'b443518f-54cf-4e40-aaf6-6598bf8ac48c',
    'x-client-app-version': '1.75.0.0',
    'x-device-id': 'browser-uuid-tizen',
    'x-device-model': '2021',
    'x-device-type': 'smart_tv',
    'x-operating-system': 'tizen',
    'x-operating-system-version': '10.0.0.0',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'cross-site',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache'
}

HEADERS_WEB = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Origin': 'https://www.clarotvmais.com.br',
    'Referer': 'https://www.clarotvmais.com.br/'
}

# ================================================================================
# VARIÁVEIS GLOBAIS
# ================================================================================

VISITOR_COUNT = 0
ONLINE_USERS = {}
ACTIVE_STREAMS = {}

# Sessões
auth_session = requests.Session()
proxy_session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
proxy_session.mount('https://', HTTPAdapter(max_retries=retries))

# Detecta binários
STREAMLINK_BIN = shutil.which("streamlink") or "/root/.local/bin/streamlink"
FFMPEG_BIN = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

# ================================================================================
# FUNÇÕES AUXILIARES
# ================================================================================

def get_random_br_ip():
    """Gera IP residencial brasileiro aleatório"""
    ranges = [(177, 0), (179, 0), (191, 0), (186, 0), (187, 0), (200, 0), (201, 0)]
    base = random.choice(ranges)
    return f"{base[0]}.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}"

def get_stealth_headers(base_headers=None, spoof_ip=None):
    """Adiciona headers de stealth para evitar bloqueios"""
    h = (base_headers or HEADERS_TIZEN).copy()
    
    if spoof_ip:
        h['X-Forwarded-For'] = spoof_ip
        h['X-Real-IP'] = spoof_ip
        h['Client-IP'] = spoof_ip
    
    h['x-request-id'] = str(uuid.uuid4())
    h['x-correlation-id'] = str(uuid.uuid4())
    h['x-trace-id'] = h['x-correlation-id']
    
    return h

def get_online_count():
    """Retorna contagem de usuários online"""
    now = time.time()
    expired = [sid for sid, ts in ONLINE_USERS.items() if now - ts > Config.ONLINE_TIMEOUT_SECONDS]
    for sid in expired:
        del ONLINE_USERS[sid]
    return len(ONLINE_USERS)

def load_cookies():
    """Carrega cookies de autenticação"""
    if not os.path.exists(Config.COOKIES_FILE):
        return None
    try:
        cookies = {}
        with open(Config.COOKIES_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    parts = line.split('=', 1)
                    cookies[parts[0].strip()] = parts[1].strip()
        return cookies if cookies else None
    except:
        return None

def save_cookies(cookie_jar):
    """Salva cookies de autenticação"""
    try:
        with open(Config.COOKIES_FILE, 'w') as f:
            for cookie in cookie_jar:
                f.write(f"{cookie.name}={cookie.value}\n")
        return True
    except:
        return False

def load_cdn_cookies():
    """Carrega cookies do CDN"""
    if not os.path.exists(Config.CDN_COOKIES_FILE):
        return {}
    try:
        with open(Config.CDN_COOKIES_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_cdn_cookies(cookie_jar):
    """Salva cookies do CDN"""
    try:
        cookies_dict = requests.utils.dict_from_cookiejar(cookie_jar)
        with open(Config.CDN_COOKIES_FILE, 'w') as f:
            json.dump(cookies_dict, f)
    except:
        pass

@lru_cache(maxsize=200)
def resolve_cdn_url(url):
    """Resolve URL para CDN CloudFront (fix 403)"""
    try:
        try:
            r = proxy_session.get(url, allow_redirects=True, stream=True, verify=False, timeout=4, headers=HEADERS_WEB)
            current_url = r.url
        except:
            current_url = url
        
        parsed = urllib.parse.urlparse(current_url)
        host = parsed.netloc
        
        for _ in range(5):
            try:
                answers = dns.resolver.resolve(host, 'CNAME')
                cname = str(answers[0].target).rstrip('.')
                if 'cloudfront.net' in cname:
                    return parsed._replace(netloc=cname).geturl()
                host = cname
            except:
                break
        
        return current_url
    except:
        return url

def categorize_channel(name):
    """Categoriza canal pelo nome"""
    name = name.lower()
    if any(x in name for x in ['sport', 'espn', 'futebol', 'premiere', 'combate']):
        return 'Esportes'
    if any(x in name for x in ['telecine', 'hbo', 'megapix', 'universal', 'tnt', 'space', 'axn', 'amc']):
        return 'Filmes'
    if any(x in name for x in ['cartoon', 'nick', 'gloob', 'discovery kids', 'disney', 'tooncast']):
        return 'Infantil'
    if any(x in name for x in ['news', 'cnn', 'bandnews', 'record news', 'jovem pan']):
        return 'Notícias'
    if any(x in name for x in ['globo', 'sbt', 'record', 'band', 'redetv', 'viva']):
        return 'Aberto/Variedades'
    return 'Outros'

def format_time(epoch):
    """Formata epoch para HH:MM"""
    if epoch > 2000000000:
        epoch /= 1000
    return datetime.fromtimestamp(epoch).strftime('%H:%M')

# ================================================================================
# API CLARO - FUNÇÕES CORE
# ================================================================================

def fetch_channels():
    """Busca lista de canais"""
    cookies = load_cookies()
    if not cookies:
        return []
    
    try:
        resp = requests.get(
            Config.URL_CATALOGO,
            headers=HEADERS_TIZEN,
            params={"size": "600"},
            cookies=cookies,
            verify=False
        )
        items = resp.json().get('data', {}).get('items', [])
        
        channels = []
        for item in items:
            if item.get('live_id') and item.get('title'):
                prog = item.get('current_program', {})
                start = prog.get('start_epoch', 0)
                end = prog.get('end_epoch', 0)
                now = time.time()
                
                if start > 2000000000: start /= 1000
                if end > 2000000000: end /= 1000
                
                percent = 0
                if end > start:
                    percent = max(0, min(100, ((now - start) / (end - start)) * 100))
                
                time_str = f"{format_time(start)} - {format_time(end)}" if start > 0 else ""
                
                channels.append({
                    'id': item['live_id'],
                    'title': item['title'],
                    'logo': item.get('logo', ''),
                    'program': prog.get('title', 'Ao Vivo'),
                    'time': time_str,
                    'category': categorize_channel(item['title']),
                    'progress': int(percent)
                })
        
        return channels
    except Exception as e:
        print(f"❌ Erro ao buscar canais: {e}")
        return []

def get_stream_data(channel_id, use_cookies=True, spoof_ip=None):
    """
    Obtém dados do stream (manifest, license, token)
    
    Modo 1 (use_cookies=True): Usa cookies salvos
    Modo 2 (use_cookies=False): Faz login direto
    """
    session = requests.Session()
    spoof_ip = spoof_ip or get_random_br_ip()
    session.headers.update(get_stealth_headers(spoof_ip=spoof_ip))
    
    if use_cookies:
        cookies = load_cookies()
        if not cookies:
            return None, "Cookies não encontrados"
        session.cookies.update(cookies)
    else:
        # Login direto
        try:
            time.sleep(random.uniform(0.1, 0.3))
            resp = session.post(
                Config.URL_AUTH_LOGIN,
                json={"username": Config.USERNAME, "password": Config.PASSWORD},
                timeout=10
            )
            if not resp.json().get('success'):
                return None, "Falha no login"
        except Exception as e:
            return None, f"Erro de conexão: {e}"
    
    # Busca dados do playback
    payload = {
        "channel_id": str(channel_id),
        "type": "TV",
        "city": Config.DEFAULT_CITY,
        "state": Config.DEFAULT_STATE,
        "drm_type": "widevine",
        "drm_provider": "verimatrix"
    }
    
    try:
        resp = session.post(Config.URL_PLAYBACK, json=payload, verify=False, timeout=10)
        data = resp.json()
        
        if not data.get('success'):
            error_code = data.get('err', {}).get('code', 'Unknown')
            return None, f"API Error: {error_code}"
        
        d = data.get('data', {})
        save_cdn_cookies(session.cookies)
        
        return {
            'manifest': resolve_cdn_url(d.get('manifest')),
            'manifest_raw': d.get('manifest'),
            'license': d.get('license_url') or 'https://multidrm.core.verimatrixcloud.net/widevine',
            'token': d.get('private_data'),
            'session': session,
            'spoof_ip': spoof_ip
        }, None
        
    except Exception as e:
        return None, str(e)

# ================================================================================
# DRM - PYWIDEVINE
# ================================================================================

def get_decryption_key(mpd_url, license_url, auth_token, session):
    """Obtém chave de decriptação via Pywidevine"""
    if not PYWIDEVINE_AVAILABLE:
        return None
    
    if not os.path.exists(Config.DEVICE_FILE):
        print("❌ device.wvd não encontrado")
        return None
    
    try:
        # Baixa manifesto e extrai PSSH
        r = session.get(mpd_url)
        xml = r.text
        
        pssh = None
        matches = re.findall(r'<cenc:pssh[^>]*>(.*?)</cenc:pssh>', xml)
        if matches:
            pssh = matches[-1]
        
        if not pssh:
            prots = re.findall(r'<ContentProtection.*?edef8ba9-79d6-4ace-a3c8-27dcd51d21ed.*?>.*?</ContentProtection>', xml, re.DOTALL)
            for p in prots:
                m = re.search(r'>([A-Za-z0-9+/=]{20,})<', p)
                if m:
                    pssh = m.group(1)
                    break
        
        if not pssh:
            print("❌ PSSH não encontrado")
            return None
        
        # Gera challenge e obtém licença
        device = Device.load(Config.DEVICE_FILE)
        cdm = Cdm.from_device(device)
        session_id = cdm.open()
        challenge = cdm.get_license_challenge(session_id, PSSH(pssh))
        
        headers = session.headers.copy()
        headers['authorization'] = auth_token
        
        res = session.post(license_url, data=challenge, headers=headers)
        
        if res.status_code == 200:
            cdm.parse_license(session_id, res.content)
            for k in cdm.get_keys(session_id):
                if k.type == 'CONTENT':
                    key = f"{k.kid.hex}:{k.key.hex()}"
                    cdm.close(session_id)
                    return key
        
        cdm.close(session_id)
        
    except Exception as e:
        print(f"❌ Erro DRM: {e}")
    
    return None

# ================================================================================
# YOUBORA HEARTBEAT
# ================================================================================

class YouboraHeartbeat:
    def __init__(self, stream_url, channel_id, spoof_ip):
        self.stream_url = stream_url
        self.channel_id = channel_id
        self.spoof_ip = spoof_ip
        self.running = False
        self.session_code = f"V_UNI_{''.join(random.choices(string.ascii_lowercase + string.digits, k=10))}"
    
    def run(self):
        self.running = True
        headers = get_stealth_headers(spoof_ip=self.spoof_ip)
        
        common = {
            "accountCode": Config.YOUBORA_ACCOUNT,
            "system": Config.YOUBORA_ACCOUNT,
            "ip": self.spoof_ip,
            "isp": "Claro S.A.",
            "sessionRoot": self.session_code,
        }
        
        try:
            start_params = {**common, "player": "Unified", "playerVersion": "1.0.0",
                          "contentId": self.channel_id, "live": "true",
                          "code": f"{self.session_code}_{int(time.time()*1000)}"}
            requests.get(f"{Config.YOUBORA_HOST}/start", params=start_params, headers=headers, timeout=5)
        except:
            pass
        
        count = 0
        while self.running:
            time.sleep(5)
            count += 1
            try:
                ping_params = {**common, "code": f"{self.session_code}_{int(time.time()*1000)}",
                             "pingTime": "5", "playhead": str(count * 5), "bitrate": "4500000"}
                requests.get(f"{Config.YOUBORA_HOST}/ping", params=ping_params, headers=headers, timeout=5)
            except:
                pass
    
    def stop(self):
        self.running = False
        try:
            requests.get(f"{Config.YOUBORA_HOST}/stop",
                        params={"accountCode": Config.YOUBORA_ACCOUNT, "sessionRoot": self.session_code},
                        timeout=3)
        except:
            pass

# ================================================================================
# STREAMER MPEG-TS
# ================================================================================

class MPEGTSStreamer:
    def __init__(self, channel_id):
        self.channel_id = channel_id
        self.heartbeat = None
        self.process = None
    
    def start(self):
        spoof_ip = get_random_br_ip()
        data, error = get_stream_data(self.channel_id, use_cookies=False, spoof_ip=spoof_ip)
        
        if error:
            print(f"❌ {error}")
            return None
        
        mpd = data['manifest_raw']
        key = get_decryption_key(mpd, data['license'], data['token'], data['session'])
        
        # Inicia heartbeat
        self.heartbeat = YouboraHeartbeat(mpd, self.channel_id, spoof_ip)
        t = threading.Thread(target=self.heartbeat.run, daemon=True)
        t.start()
        
        # Comando streamlink
        cmd = [
            STREAMLINK_BIN,
            "--http-header", f"User-Agent={HEADERS_TIZEN['User-Agent']}",
            "--http-header", "Referer=https://development.3ss.tv/",
            "--http-header", f"X-Forwarded-For={spoof_ip}",
            "--http-header", f"X-Real-IP={spoof_ip}",
            "--http-header", f"Client-IP={spoof_ip}",
            mpd, "best", "--stdout",
            "--ffmpeg-fout", "mpegts",
            "--ringbuffer-size", "16M",
            "--stream-segment-threads", "4",
            "--stream-timeout", "15",
            "--force"
        ]
        
        if key:
            key_only = key.split(":")[1] if ":" in key else key
            cmd.extend(["-decryption_key", key_only])
        
        print(f"🎬 Iniciando stream: {self.channel_id}")
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**7)
        return self.process
    
    def stop(self):
        if self.heartbeat:
            self.heartbeat.stop()
        if self.process:
            try:
                self.process.kill()
            except:
                pass

# ================================================================================
# ROTAS DA API
# ================================================================================

@app.route('/api/channels')
def api_channels():
    """Lista todos os canais"""
    return jsonify(fetch_channels())

@app.route('/api/stats')
def api_stats():
    """Estatísticas de visitas e usuários online"""
    return jsonify({
        'visits': VISITOR_COUNT,
        'online': get_online_count(),
        'streams': len(ACTIVE_STREAMS)
    })

@app.route('/api/stream/<channel_id>')
def api_stream(channel_id):
    """Dados do stream para player web (DRM nativo)"""
    data, error = get_stream_data(channel_id, use_cookies=True)
    
    if error:
        return jsonify({'error': error}), 400
    
    return jsonify({
        'manifest': data['manifest'],
        'license': data['license'],
        'token': data['token']
    })

@app.route('/api/auth/start', methods=['POST'])
def api_auth_start():
    """Inicia processo de autenticação via código"""
    global auth_session
    auth_session = requests.Session()
    auth_session.headers.update(HEADERS_TIZEN)
    
    try:
        resp = auth_session.get(Config.URL_AUTH_TOKEN)
        data = resp.json()
        if 'data' in data and 'token' in data['data']:
            return jsonify({'code': data['data']['token']})
        return jsonify({'error': 'Falha ao gerar código'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/poll', methods=['POST'])
def api_auth_poll():
    """Verifica status da autenticação"""
    code = request.json.get('code')
    if not code:
        return jsonify({'error': 'Código não informado'}), 400
    
    try:
        resp = auth_session.post(Config.URL_AUTH_TOKEN, json={"token": code})
        data = resp.json()
        
        if data.get('success'):
            save_cookies(auth_session.cookies)
            return jsonify({'success': True})
        
        return jsonify({'success': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ================================================================================
# ROTAS DE STREAMING MPEG-TS
# ================================================================================

@app.route('/live/<channel_id>')
@app.route('/live/<channel_id>.ts')
def live_stream(channel_id):
    """Stream MPEG-TS para players externos (VLC, M3U)"""
    if not PYWIDEVINE_AVAILABLE:
        return "Pywidevine não disponível", 503
    
    if not os.path.exists(STREAMLINK_BIN):
        return "Streamlink não encontrado", 503
    
    print(f"📺 [LIVE] Conectando: {channel_id}")
    streamer = MPEGTSStreamer(channel_id)
    process = streamer.start()
    
    if not process:
        return "Erro ao iniciar stream", 404
    
    def generate():
        try:
            while True:
                data = process.stdout.read(65536)
                if not data:
                    if process.poll() is not None:
                        break
                yield data
        except:
            pass
        finally:
            streamer.stop()
            print(f"🔌 [LIVE] Desconectado: {channel_id}")
    
    return Response(stream_with_context(generate()), mimetype='video/mp2t')

@app.route('/playlist.m3u')
@app.route('/lista.m3u')
def playlist_m3u():
    """Gera playlist M3U com todos os canais"""
    channels = fetch_channels()
    base_url = request.url_root.rstrip('/').replace('http://', 'https://')
    
    lines = ['#EXTM3U']
    for ch in channels:
        if ch['id'] and ch['id'] != '0':
            lines.append(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-logo="{ch["logo"]}" group-title="{ch["category"]}",{ch["title"]}')
            lines.append(f'{base_url}/live/{ch["id"]}.ts')
    
    return Response('\n'.join(lines), mimetype='audio/x-mpegurl',
                   headers={'Content-Disposition': 'attachment; filename=claro_tv.m3u'})

# ================================================================================
# ROTAS DO PROXY
# ================================================================================

@app.route('/proxy', methods=['GET', 'POST', 'OPTIONS'])
def proxy():
    """Proxy para requisições de mídia/DRM"""
    if request.method == 'OPTIONS':
        r = Response()
        r.headers['Access-Control-Allow-Origin'] = '*'
        r.headers['Access-Control-Allow-Methods'] = '*'
        r.headers['Access-Control-Allow-Headers'] = '*'
        return r
    
    target_url = request.args.get('url')
    if not target_url:
        return "URL não informada", 400
    
    headers = HEADERS_WEB.copy()
    for h in ['Range', 'Content-Type', 'Authorization']:
        if h in request.headers:
            headers[h] = request.headers[h]
    
    is_drm = 'verimatrix' in target_url or 'license' in target_url
    cookies = None if is_drm else load_cdn_cookies()
    
    try:
        if '.mpd' in target_url and request.method == 'GET':
            r = proxy_session.get(target_url, headers=headers, cookies=cookies, verify=False)
            content = r.text
            base = target_url.split('?')[0].rsplit('/', 1)[0] + '/'
            
            if '<BaseURL>' not in content:
                content = content.replace('<Period', f'<Period><BaseURL>{base}</BaseURL>')
                if '<BaseURL>' not in content:
                    content = content.replace('<MPD', f'<MPD><BaseURL>{base}</BaseURL>')
            
            resp = Response(content, status=r.status_code, content_type='application/dash+xml')
        else:
            data = request.get_data() if request.method == 'POST' else None
            r = proxy_session.request(request.method, target_url, headers=headers, data=data, 
                                      cookies=cookies, stream=True, verify=False)
            
            def gen():
                for chunk in r.iter_content(chunk_size=16384):
                    yield chunk
            
            resp = Response(gen(), status=r.status_code)
            for k, v in r.headers.items():
                if k.lower() in ['content-type', 'content-range', 'content-length', 'accept-ranges']:
                    resp.headers[k] = v
        
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp
    
    except Exception as e:
        return Response(str(e), status=500)

# ================================================================================
# PÁGINAS WEB
# ================================================================================

@app.route('/')
def index():
    """Página inicial com grid de canais"""
    global VISITOR_COUNT
    VISITOR_COUNT += 1
    
    session_id = request.remote_addr
    ONLINE_USERS[session_id] = time.time()
    
    return render_template_string(HTML_HOME, 
                                 total_visits=VISITOR_COUNT, 
                                 online_count=get_online_count())

@app.route('/watch/<channel_id>')
def watch(channel_id):
    """Player de vídeo"""
    session_id = request.remote_addr
    ONLINE_USERS[session_id] = time.time()
    
    return render_template_string(HTML_PLAYER, 
                                 initial_id=channel_id, 
                                 online_count=get_online_count())

@app.route('/admin')
def admin():
    """Painel administrativo"""
    return render_template_string(HTML_ADMIN)

# ================================================================================
# TEMPLATES HTML
# ================================================================================

HTML_HOME = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Claro TV+ Unified</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        :root { --primary: #e30613; --bg: #0f0f0f; --card: #1a1a1a; --text: #fff; }
        * { box-sizing: border-box; }
        body { background: var(--bg); color: var(--text); font-family: 'Inter', sans-serif; margin: 0; padding: 20px; }
        .header { display: flex; flex-direction: column; align-items: center; gap: 15px; margin-bottom: 30px; }
        .logo { font-size: 28px; font-weight: 800; color: var(--primary); cursor: pointer; }
        .stats { color: #888; font-size: 12px; display: flex; gap: 20px; }
        .stats-online { color: #4caf50; font-weight: bold; }
        .controls { width: 100%; max-width: 800px; display: flex; flex-direction: column; gap: 15px; }
        .search-bar { width: 100%; padding: 14px 20px; border-radius: 12px; border: none; background: #252525; color: #fff; font-size: 16px; outline: none; }
        .search-bar:focus { background: #333; box-shadow: 0 0 0 2px var(--primary); }
        .categories { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 5px; }
        .cat-btn { padding: 8px 16px; background: #252525; border: none; border-radius: 20px; color: #aaa; cursor: pointer; font-weight: 600; white-space: nowrap; }
        .cat-btn.active, .cat-btn:hover { background: var(--primary); color: #fff; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 15px; max-width: 1400px; margin: 0 auto; }
        .card { background: var(--card); border-radius: 12px; overflow: hidden; position: relative; transition: transform 0.2s; border: 2px solid transparent; cursor: pointer; }
        .card:hover { transform: scale(1.03); border-color: var(--primary); }
        .fav-btn { position: absolute; top: 8px; right: 8px; font-size: 20px; color: rgba(255,255,255,0.3); z-index: 10; cursor: pointer; }
        .fav-btn.is-fav { color: var(--primary); }
        .card-img { width: 100%; height: 90px; object-fit: contain; background: #000; padding: 10px; }
        .card-body { padding: 12px; }
        .ch-title { font-weight: 700; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .ch-prog { font-size: 12px; color: #888; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .ch-time { font-size: 11px; color: #555; font-weight: 600; }
        .progress-bar { width: 100%; height: 3px; background: #333; margin-top: 8px; border-radius: 2px; }
        .progress-fill { height: 100%; background: var(--primary); border-radius: 2px; }
        #loader { text-align: center; padding: 50px; color: #666; }
        .admin-link { position: fixed; top: 20px; right: 20px; color: #333; text-decoration: none; font-size: 14px; }
        .admin-link:hover { color: var(--primary); }
        .m3u-link { position: fixed; top: 20px; left: 20px; background: var(--primary); color: #fff; padding: 8px 15px; border-radius: 8px; text-decoration: none; font-size: 12px; font-weight: 600; }
    </style>
</head>
<body>
    <a href="/admin" class="admin-link">⚙️ Admin</a>
    <a href="/playlist.m3u" class="m3u-link">📺 M3U</a>
    
    <div class="header">
        <div class="logo" onclick="location.reload()">CLARO TV+ UNIFIED</div>
        <div class="stats">
            <span>Visitas: {{ total_visits }}</span>
            <span class="stats-online">Online: 👤 {{ online_count }}</span>
        </div>
        <div class="controls">
            <input type="text" class="search-bar" id="search" placeholder="🔍 Buscar canal..." onkeyup="filter()">
            <div class="categories" id="cat-list">
                <button class="cat-btn active" onclick="setCat('all')">Todos</button>
                <button class="cat-btn" onclick="setCat('fav')">❤️ Favoritos</button>
                <button class="cat-btn" onclick="setCat('Esportes')">Esportes</button>
                <button class="cat-btn" onclick="setCat('Filmes')">Filmes</button>
                <button class="cat-btn" onclick="setCat('Infantil')">Infantil</button>
                <button class="cat-btn" onclick="setCat('Notícias')">Notícias</button>
                <button class="cat-btn" onclick="setCat('Aberto/Variedades')">Abertos</button>
            </div>
        </div>
    </div>
    
    <div id="loader">Carregando canais...</div>
    <div class="grid" id="grid"></div>
    
    <script>
        let channels = [], currentCat = 'all';
        let favorites = JSON.parse(localStorage.getItem('claro_favs') || '[]');
        
        fetch('/api/channels').then(r => r.json()).then(data => {
            channels = data;
            document.getElementById('loader').style.display = 'none';
            render();
        });
        
        function render() {
            const term = document.getElementById('search').value.toLowerCase();
            const grid = document.getElementById('grid');
            grid.innerHTML = '';
            
            channels.filter(ch => {
                const matchSearch = ch.title.toLowerCase().includes(term);
                const matchCat = currentCat === 'all' || 
                                (currentCat === 'fav' && favorites.includes(ch.id)) || 
                                ch.category === currentCat;
                return matchSearch && matchCat && ch.id && ch.id !== '0';
            }).forEach(ch => {
                const isFav = favorites.includes(ch.id);
                grid.innerHTML += `
                    <div class="card" onclick="go('${ch.id}')">
                        <div class="fav-btn ${isFav ? 'is-fav' : ''}" onclick="toggleFav(event, '${ch.id}')">♥</div>
                        <img src="${ch.logo}" class="card-img" loading="lazy">
                        <div class="card-body">
                            <div class="ch-title">${ch.title}</div>
                            <div class="ch-prog">${ch.program}</div>
                            <div class="ch-time">${ch.time}</div>
                            <div class="progress-bar"><div class="progress-fill" style="width:${ch.progress}%"></div></div>
                        </div>
                    </div>`;
            });
        }
        
        function setCat(cat) {
            currentCat = cat;
            document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
            render();
        }
        
        function toggleFav(e, id) {
            e.stopPropagation();
            if (favorites.includes(id)) favorites = favorites.filter(i => i !== id);
            else favorites.push(id);
            localStorage.setItem('claro_favs', JSON.stringify(favorites));
            render();
        }
        
        function filter() { render(); }
        function go(id) { window.location.href = '/watch/' + id; }
        
        setInterval(() => {
            fetch('/api/stats').then(r => r.json()).then(data => {
                document.querySelector('.stats').innerHTML = 
                    `<span>Visitas: ${data.visits}</span><span class="stats-online">Online: 👤 ${data.online}</span>`;
            });
        }, 60000);
    </script>
</body>
</html>
"""

HTML_PLAYER = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Player - Claro TV+</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.3.5/shaka-player.ui.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.3.5/controls.min.css">
    <style>
        body { background: #000; margin: 0; height: 100vh; overflow: hidden; font-family: 'Inter', sans-serif; }
        .overlay { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 100; }
        .p-header { position: absolute; top: 0; left: 0; width: 100%; padding: 20px; background: linear-gradient(to bottom, rgba(0,0,0,0.8), transparent); display: flex; gap: 15px; align-items: center; opacity: 0; transition: opacity 0.3s; pointer-events: auto; }
        .overlay:hover .p-header { opacity: 1; }
        .btn-icon { background: rgba(255,255,255,0.1); border: none; color: #fff; border-radius: 50%; cursor: pointer; font-size: 18px; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; }
        .btn-icon:hover { background: #e30613; }
        .online-status { color: #4caf50; font-weight: bold; font-size: 14px; margin-right: 15px; }
        .sidebar { position: absolute; top: 0; right: -350px; width: 320px; height: 100%; background: rgba(15,15,15,0.95); transition: right 0.3s; pointer-events: auto; display: flex; flex-direction: column; border-left: 1px solid #333; }
        .sidebar.open { right: 0; }
        .sb-header { padding: 20px; border-bottom: 1px solid #333; display: flex; justify-content: space-between; align-items: center; color: #fff; font-weight: bold; }
        .sb-search { padding: 15px; border-bottom: 1px solid #333; }
        .sb-input { width: 100%; background: #222; border: none; padding: 10px; color: #fff; border-radius: 6px; outline: none; }
        .sb-list { flex: 1; overflow-y: auto; padding: 10px; }
        .sb-item { display: flex; align-items: center; gap: 10px; padding: 10px; border-radius: 8px; cursor: pointer; color: #ccc; }
        .sb-item:hover { background: #333; color: #fff; }
        .sb-item.active { background: #e30613; color: #fff; }
        .sb-img { width: 40px; height: 30px; object-fit: contain; background: #000; border-radius: 4px; }
        .settings-menu { position: absolute; top: 70px; right: 20px; background: rgba(15,15,15,0.95); width: 250px; border-radius: 8px; border: 1px solid #333; display: none; pointer-events: auto; }
        .settings-menu.open { display: block; }
        .sm-item { padding: 10px 15px; border-bottom: 1px solid #333; color: #ccc; cursor: pointer; font-size: 14px; }
        .sm-item:hover { background: #333; color: #fff; }
        .sm-title { padding: 10px 15px; font-weight: bold; color: #e30613; font-size: 12px; }
        .stats-box { position: absolute; top: 70px; left: 20px; background: rgba(0,0,0,0.7); padding: 10px; border-radius: 6px; color: #0f0; font-family: monospace; font-size: 12px; display: none; }
        #video-container { width: 100%; height: 100%; background: #000; }
        video { width: 100%; height: 100%; }
    </style>
</head>
<body>
    <div id="video-container" data-shaka-player-container>
        <video data-shaka-player id="video" autoplay></video>
    </div>
    <div class="overlay">
        <div class="p-header">
            <button class="btn-icon" onclick="window.location.href='/'">←</button>
            <div style="flex:1; color:#fff; font-weight:600; font-size:14px;" id="current-title">Carregando...</div>
            <div class="online-status" id="online-count">👤 {{ online_count }} Online</div>
            <button class="btn-icon" onclick="toggleStats()" title="Stats">📊</button>
            <button class="btn-icon" onclick="toggleSettings()" title="Configurações">⚙️</button>
            <button class="btn-icon" onclick="toggleSidebar()" title="Canais">☰</button>
        </div>
        <div class="sidebar" id="sidebar">
            <div class="sb-header">CANAIS <span onclick="toggleSidebar()" style="cursor:pointer">✕</span></div>
            <div class="sb-search"><input type="text" class="sb-input" placeholder="Buscar..." onkeyup="filterSb(this.value)"></div>
            <div class="sb-list" id="sb-list"></div>
        </div>
        <div class="settings-menu" id="settings">
            <div class="sm-title">QUALIDADE</div>
            <div id="quality-list"></div>
            <div class="sm-title">ÁUDIO</div>
            <div id="audio-list"></div>
            <div class="sm-title">LEGENDAS</div>
            <div id="subs-list"></div>
        </div>
        <div class="stats-box" id="stats">
            RES: <span id="st-res">-</span><br>
            BUF: <span id="st-buf">-</span>s<br>
            DROP: <span id="st-drop">-</span>
        </div>
    </div>
    <script>
        let player, channels = [], currentId = "{{ initial_id }}";
        const video = document.getElementById('video');
        
        async function init() {
            shaka.polyfill.installAll();
            if (shaka.Player.isBrowserSupported()) {
                player = new shaka.Player(video);
                new shaka.ui.Overlay(player, document.getElementById('video-container'), video);
                
                player.getNetworkingEngine().registerRequestFilter((type, req) => {
                    if (type == shaka.net.NetworkingEngine.RequestType.LICENSE) 
                        req.headers['Authorization'] = window.drmToken;
                    if (!req.uris[0].includes('/proxy?url=')) 
                        req.uris = [location.origin + '/proxy?url=' + encodeURIComponent(req.uris[0])];
                });
                
                player.configure({ 
                    abr: { enabled: true, defaultBandwidthEstimate: 10000000 }, 
                    preferredAudioLanguage: 'pt-BR',
                    streaming: { bufferingGoal: 30, rebufferingGoal: 5 }
                });
                
                loadChannel(currentId);
                fetch('/api/channels').then(r => r.json()).then(data => { channels = data; renderSidebar(); });
                setInterval(updateStats, 1000);
            }
        }
        
        async function loadChannel(id) {
            currentId = id;
            document.getElementById('current-title').innerText = "Carregando...";
            try {
                const r = await fetch('/api/stream/' + id);
                const data = await r.json();
                if (data.error) throw data.error;
                
                window.drmToken = data.token;
                player.configure({ drm: { servers: { 'com.widevine.alpha': data.license } } });
                await player.load(data.manifest);
                
                const ch = channels.find(c => c.id == id);
                if (ch) document.getElementById('current-title').innerText = ch.title + " - " + ch.program;
                renderSidebar();
                updateSettingsMenu();
            } catch (e) { 
                console.error(e); 
                alert("Erro ao carregar canal."); 
            }
        }
        
        function updateSettingsMenu() {
            const qList = document.getElementById('quality-list');
            qList.innerHTML = '<div class="sm-item" onclick="setAutoQuality()">Auto</div>';
            const tracks = player.getVariantTracks();
            const seen = new Set();
            tracks.filter(t => t.height).sort((a,b) => b.height - a.height).forEach(t => {
                if(!seen.has(t.height)) {
                    seen.add(t.height);
                    qList.innerHTML += `<div class="sm-item" onclick="setQuality(${t.id})">${t.height}p (${(t.bandwidth/1000000).toFixed(1)} Mbps)</div>`;
                }
            });
            
            const aList = document.getElementById('audio-list');
            aList.innerHTML = '';
            player.getAudioLanguagesAndRoles().forEach(l => {
                aList.innerHTML += `<div class="sm-item" onclick="player.selectAudioLanguage('${l.language}')">${l.language.toUpperCase()}</div>`;
            });
            
            const sList = document.getElementById('subs-list');
            sList.innerHTML = '<div class="sm-item" onclick="player.setTextTrackVisibility(false)">Desativado</div>';
            player.getTextLanguagesAndRoles().forEach(l => {
                sList.innerHTML += `<div class="sm-item" onclick="enableSub('${l.language}')">${l.language.toUpperCase()}</div>`;
            });
        }
        
        function enableSub(lang) { player.selectTextLanguage(lang); player.setTextTrackVisibility(true); toggleSettings(); }
        function setQuality(id) { player.configure({ abr: { enabled: false } }); const t = player.getVariantTracks().find(t => t.id === id); if(t) player.selectVariantTrack(t, true); toggleSettings(); }
        function setAutoQuality() { player.configure({ abr: { enabled: true } }); toggleSettings(); }
        function updateStats() { const s = player.getStats(); document.getElementById('st-res').innerText = s.width + 'x' + s.height; document.getElementById('st-buf').innerText = s.bufferingTime.toFixed(1); document.getElementById('st-drop').innerText = s.droppedFrames; }
        
        function renderSidebar(filter = "") {
            const list = document.getElementById('sb-list');
            list.innerHTML = "";
            channels.filter(c => c.id && c.id !== '0' && c.title.toLowerCase().includes(filter.toLowerCase())).forEach(c => {
                const div = document.createElement('div');
                div.className = `sb-item ${c.id == currentId ? 'active' : ''}`;
                div.innerHTML = `<img src="${c.logo}" class="sb-img"> <div>${c.title}</div>`;
                div.onclick = () => { loadChannel(c.id); toggleSidebar(); };
                list.appendChild(div);
            });
        }
        
        function toggleSidebar() { document.getElementById('sidebar').classList.toggle('open'); }
        function toggleSettings() { document.getElementById('settings').classList.toggle('open'); }
        function toggleStats() { const st = document.getElementById('stats'); st.style.display = st.style.display === 'block' ? 'none' : 'block'; }
        function filterSb(val) { renderSidebar(val); }
        
        document.addEventListener('DOMContentLoaded', init);
    </script>
</body>
</html>
"""

HTML_ADMIN = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Admin - Claro TV+</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        body { background: #0f0f0f; color: #fff; font-family: 'Inter', sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 20px; }
        .container { max-width: 500px; width: 100%; }
        .box { background: #1a1a1a; padding: 40px; border-radius: 15px; border: 1px solid #333; text-align: center; margin-bottom: 20px; }
        h1 { color: #e30613; margin-bottom: 10px; }
        p { color: #aaa; margin-bottom: 30px; }
        .code-display { font-size: 40px; font-weight: 800; letter-spacing: 5px; color: #fff; margin: 20px 0; background: #222; padding: 20px; border-radius: 10px; border: 2px dashed #444; }
        .btn { background: #e30613; color: #fff; border: none; padding: 15px 30px; font-size: 16px; font-weight: bold; border-radius: 8px; cursor: pointer; width: 100%; transition: 0.2s; margin-bottom: 10px; }
        .btn:hover { background: #ff1f2d; }
        .btn:disabled { background: #444; cursor: not-allowed; }
        .btn-secondary { background: #333; }
        .steps { text-align: left; background: #222; padding: 15px; border-radius: 8px; margin-bottom: 20px; font-size: 14px; line-height: 1.8; }
        .steps a { color: #e30613; text-decoration: none; font-weight: bold; }
        #status { margin-top: 15px; font-weight: bold; }
        .success { color: #4caf50; }
        .error { color: #f44336; }
        .info-box { background: #1a1a1a; padding: 20px; border-radius: 10px; border: 1px solid #333; }
        .info-row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #333; }
        .info-row:last-child { border: none; }
        .info-label { color: #888; }
        .info-value { color: #fff; font-weight: 600; }
    </style>
</head>
<body>
    <div class="container">
        <div class="box">
            <h1>🔧 PAINEL ADMIN</h1>
            <p>Renovação de Cookies e Configurações</p>
            
            <div id="initial-view">
                <button class="btn" onclick="startLogin()">GERAR CÓDIGO DE ACESSO</button>
                <button class="btn btn-secondary" onclick="window.location.href='/'">← Voltar</button>
            </div>
            
            <div id="login-view" style="display:none;">
                <div class="steps">
                    1. Acesse: <a href="https://www.clarotvmais.com.br/ativar" target="_blank">clarotvmais.com.br/ativar</a><br>
                    2. Faça login com sua conta Claro.<br>
                    3. Digite o código abaixo:
                </div>
                <div class="code-display" id="code-box">----</div>
                <div id="status">Aguardando ativação...</div>
            </div>
        </div>
        
        <div class="info-box">
            <div class="info-row">
                <span class="info-label">Versão</span>
                <span class="info-value">Unified 1.0</span>
            </div>
            <div class="info-row">
                <span class="info-label">Pywidevine</span>
                <span class="info-value" id="pywidevine-status">Verificando...</span>
            </div>
            <div class="info-row">
                <span class="info-label">Streamlink</span>
                <span class="info-value" id="streamlink-status">Verificando...</span>
            </div>
            <div class="info-row">
                <span class="info-label">Streams Ativos</span>
                <span class="info-value" id="streams-count">-</span>
            </div>
        </div>
    </div>
    
    <script>
        let pollInterval;
        
        // Verifica status do sistema
        fetch('/api/stats').then(r => r.json()).then(data => {
            document.getElementById('streams-count').innerText = data.streams || 0;
        });
        
        // Simula verificação de dependências
        document.getElementById('pywidevine-status').innerHTML = '{{ "✅ Instalado" if pywidevine else "❌ Não instalado" }}';
        document.getElementById('streamlink-status').innerHTML = '✅ Disponível';
        
        async function startLogin() {
            const btn = document.querySelector('.btn');
            btn.disabled = true;
            btn.innerText = "Gerando...";
            
            try {
                const res = await fetch('/api/auth/start', { method: 'POST' });
                const data = await res.json();
                
                if (data.code) {
                    document.getElementById('initial-view').style.display = 'none';
                    document.getElementById('login-view').style.display = 'block';
                    document.getElementById('code-box').innerText = data.code;
                    pollInterval = setInterval(() => checkStatus(data.code), 5000);
                } else {
                    alert("Erro: " + (data.error || "Desconhecido"));
                    btn.disabled = false;
                    btn.innerText = "GERAR CÓDIGO DE ACESSO";
                }
            } catch(e) {
                alert("Erro de conexão");
                btn.disabled = false;
                btn.innerText = "GERAR CÓDIGO DE ACESSO";
            }
        }
        
        async function checkStatus(code) {
            try {
                const res = await fetch('/api/auth/poll', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({code: code})
                });
                const data = await res.json();
                
                if (data.success) {
                    clearInterval(pollInterval);
                    document.getElementById('status').innerHTML = '<span class="success">✅ SUCESSO! Cookies renovados.</span>';
                    setTimeout(() => window.location.href = '/', 2000);
                }
            } catch(e) { console.error(e); }
        }
    </script>
</body>
</html>
""".replace('{{ "✅ Instalado" if pywidevine else "❌ Não instalado" }}', 
            '✅ Instalado' if PYWIDEVINE_AVAILABLE else '❌ Não instalado')

# ================================================================================
# INICIALIZAÇÃO
# ================================================================================

def main():
    print("\n" + "="*70)
    print(" 🚀 CLARO TV+ UNIFIED - Versão Completa")
    print("="*70)
    print(f" 📡 Interface Web: https://0.0.0.0:{Config.PORTA_HTTPS}")
    print(f" 🔧 Painel Admin:  https://0.0.0.0:{Config.PORTA_HTTPS}/admin")
    print(f" 📺 Playlist M3U:  https://0.0.0.0:{Config.PORTA_HTTPS}/playlist.m3u")
    print(f" 🎬 Stream MPEG-TS: https://0.0.0.0:{Config.PORTA_HTTPS}/live/<ID>.ts")
    print("="*70)
    print(f" ✅ Pywidevine: {'Disponível' if PYWIDEVINE_AVAILABLE else 'Não disponível'}")
    print(f" ✅ Streamlink: {STREAMLINK_BIN}")
    print(f" ✅ FFmpeg: {FFMPEG_BIN}")
    print("="*70 + "\n")
    
    if HYPERCORN_AVAILABLE and os.path.exists(Config.CERT_FILE) and os.path.exists(Config.KEY_FILE):
        # Modo HTTPS com Hypercorn (HTTP/2)
        config = HypercornConfig()
        config.bind = [f"0.0.0.0:{Config.PORTA_HTTPS}"]
        config.alpn_protocols = ["h2", "http/1.1"]
        config.certfile = Config.CERT_FILE
        config.keyfile = Config.KEY_FILE
        print("🔒 Modo HTTPS (Hypercorn HTTP/2)")
        asyncio.run(serve(app, config))
    else:
        # Modo HTTP simples (para desenvolvimento)
        print("⚠️  Modo HTTP (sem SSL) - Use apenas para desenvolvimento")
        app.run(host='0.0.0.0', port=Config.PORTA_HTTPS, threaded=True, debug=False)

# Alias para Hypercorn Config
if HYPERCORN_AVAILABLE:
    HypercornConfig = Config.__class__
    from hypercorn.config import Config as HypercornConfig

if __name__ == '__main__':
    main()
