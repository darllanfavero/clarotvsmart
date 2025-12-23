#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLARO TV+ UNIFIED V2 - Com seleção de áudio em Português
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

import requests
import urllib3
import dns.resolver
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, render_template_string, request, Response, jsonify, stream_with_context

try:
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device
    from pywidevine.pssh import PSSH
    PYWIDEVINE_AVAILABLE = True
except ImportError:
    PYWIDEVINE_AVAILABLE = False

try:
    from hypercorn.config import Config as HypercornConfig
    from hypercorn.asyncio import serve
    import asyncio
    HYPERCORN_AVAILABLE = True
except ImportError:
    HYPERCORN_AVAILABLE = False

logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('hypercorn.access').setLevel(logging.ERROR)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'claro_unified_v2')

# ================================================================================
# CONFIGURAÇÃO
# ================================================================================

class Config:
    PORTA_HTTPS = int(os.environ.get('PORT', 8443))
    CERT_FILE = "cert.pem"
    KEY_FILE = "key.pem"
    COOKIES_FILE = "cookies_finais.txt"
    CDN_COOKIES_FILE = "cookies_cdn.json"
    DEVICE_FILE = "device.wvd"
    USERNAME = os.environ.get('CLARO_USER', 'nocbrasil')
    PASSWORD = os.environ.get('CLARO_PASS', 'n0cbr4s1l')
    API_BASE = "https://androidtv-mediation-layer.clarobrasil.mobi"
    URL_CATALOGO = f"{API_BASE}/api/v1/catalog/content_providers"
    URL_PLAYBACK = f"{API_BASE}/api/entitlement/playback"
    URL_AUTH_TOKEN = f"{API_BASE}/api/v1/auth/token"
    URL_AUTH_LOGIN = f"{API_BASE}/api/auth/login"
    YOUBORA_HOST = "https://infinity-c35.youboranqs01.com"
    YOUBORA_ACCOUNT = "clarobrasildev"
    ONLINE_TIMEOUT_SECONDS = 300
    DEFAULT_CITY = "Sao Paulo"
    DEFAULT_STATE = "Sao Paulo"

HEADERS_TIZEN = {
    'User-Agent': 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0',
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://development.3ss.tv',
    'Referer': 'https://development.3ss.tv/',
    'x-api-key': 'b443518f-54cf-4e40-aaf6-6598bf8ac48c',
    'x-client-app-version': '1.75.0.0',
    'x-device-id': 'browser-uuid-tizen',
    'x-device-model': '2021',
    'x-device-type': 'smart_tv',
    'x-operating-system': 'tizen',
    'x-operating-system-version': '10.0.0.0',
}

HEADERS_WEB = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Origin': 'https://www.clarotvmais.com.br',
    'Referer': 'https://www.clarotvmais.com.br/'
}

VISITOR_COUNT = 0
ONLINE_USERS = {}
auth_session = requests.Session()
proxy_session = requests.Session()
retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[500, 502, 503, 504])
proxy_session.mount('https://', HTTPAdapter(max_retries=retries))

STREAMLINK_BIN = shutil.which("streamlink") or "/root/.local/bin/streamlink"
FFMPEG_BIN = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

# ================================================================================
# FUNÇÕES AUXILIARES
# ================================================================================

def get_random_br_ip():
    ranges = [(177, 0), (179, 0), (191, 0), (186, 0), (187, 0), (200, 0), (201, 0)]
    base = random.choice(ranges)
    return f"{base[0]}.{random.randint(1, 254)}.{random.randint(1, 254)}.{random.randint(1, 254)}"

def get_stealth_headers(base_headers=None, spoof_ip=None):
    h = (base_headers or HEADERS_TIZEN).copy()
    if spoof_ip:
        h['X-Forwarded-For'] = spoof_ip
        h['X-Real-IP'] = spoof_ip
        h['Client-IP'] = spoof_ip
    h['x-request-id'] = str(uuid.uuid4())
    h['x-correlation-id'] = str(uuid.uuid4())
    return h

def get_online_count():
    now = time.time()
    expired = [sid for sid, ts in ONLINE_USERS.items() if now - ts > Config.ONLINE_TIMEOUT_SECONDS]
    for sid in expired:
        del ONLINE_USERS[sid]
    return len(ONLINE_USERS)

def load_cookies():
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
    try:
        with open(Config.COOKIES_FILE, 'w') as f:
            for cookie in cookie_jar:
                f.write(f"{cookie.name}={cookie.value}\n")
        return True
    except:
        return False

def load_cdn_cookies():
    if not os.path.exists(Config.CDN_COOKIES_FILE):
        return {}
    try:
        with open(Config.CDN_COOKIES_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_cdn_cookies(cookie_jar):
    try:
        cookies_dict = requests.utils.dict_from_cookiejar(cookie_jar)
        with open(Config.CDN_COOKIES_FILE, 'w') as f:
            json.dump(cookies_dict, f)
    except:
        pass

@lru_cache(maxsize=200)
def resolve_cdn_url(url):
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
    name = name.lower()
    if any(x in name for x in ['sport', 'espn', 'premiere', 'combate']): return 'Esportes'
    if any(x in name for x in ['telecine', 'hbo', 'megapix', 'universal']): return 'Filmes'
    if any(x in name for x in ['cartoon', 'nick', 'gloob', 'disney']): return 'Infantil'
    if any(x in name for x in ['news', 'cnn', 'bandnews']): return 'Notícias'
    if any(x in name for x in ['globo', 'sbt', 'record', 'band']): return 'Aberto/Variedades'
    return 'Outros'

def format_time(epoch):
    if epoch > 2000000000: epoch /= 1000
    return datetime.fromtimestamp(epoch).strftime('%H:%M')

# ================================================================================
# API CLARO
# ================================================================================

def fetch_channels():
    cookies = load_cookies()
    if not cookies: return []
    try:
        resp = requests.get(Config.URL_CATALOGO, headers=HEADERS_TIZEN, params={"size": "600"}, cookies=cookies, verify=False)
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
                percent = max(0, min(100, ((now - start) / (end - start)) * 100)) if end > start else 0
                time_str = f"{format_time(start)} - {format_time(end)}" if start > 0 else ""
                channels.append({
                    'id': item['live_id'], 'title': item['title'], 'logo': item.get('logo', ''),
                    'program': prog.get('title', 'Ao Vivo'), 'time': time_str,
                    'category': categorize_channel(item['title']), 'progress': int(percent)
                })
        return channels
    except:
        return []

def get_stream_data(channel_id, use_cookies=True, spoof_ip=None):
    session = requests.Session()
    spoof_ip = spoof_ip or get_random_br_ip()
    session.headers.update(get_stealth_headers(spoof_ip=spoof_ip))
    
    if use_cookies:
        cookies = load_cookies()
        if not cookies: return None, "Cookies não encontrados"
        session.cookies.update(cookies)
    else:
        try:
            time.sleep(random.uniform(0.1, 0.3))
            resp = session.post(Config.URL_AUTH_LOGIN, json={"username": Config.USERNAME, "password": Config.PASSWORD}, timeout=10)
            if not resp.json().get('success'): return None, "Falha no login"
        except Exception as e:
            return None, f"Erro: {e}"
    
    payload = {"channel_id": str(channel_id), "type": "TV", "city": Config.DEFAULT_CITY,
               "state": Config.DEFAULT_STATE, "drm_type": "widevine", "drm_provider": "verimatrix"}
    
    try:
        resp = session.post(Config.URL_PLAYBACK, json=payload, verify=False, timeout=10)
        data = resp.json()
        if not data.get('success'): return None, f"API Error: {data.get('err', {}).get('code')}"
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
    if not PYWIDEVINE_AVAILABLE or not os.path.exists(Config.DEVICE_FILE):
        return None
    try:
        r = session.get(mpd_url)
        xml = r.text
        pssh = None
        matches = re.findall(r'<cenc:pssh[^>]*>(.*?)</cenc:pssh>', xml)
        if matches: pssh = matches[-1]
        if not pssh:
            prots = re.findall(r'<ContentProtection.*?edef8ba9.*?>.*?</ContentProtection>', xml, re.DOTALL)
            for p in prots:
                m = re.search(r'>([A-Za-z0-9+/=]{20,})<', p)
                if m: pssh = m.group(1); break
        if not pssh: return None
        
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
        print(f"❌ DRM Error: {e}")
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
        common = {"accountCode": Config.YOUBORA_ACCOUNT, "ip": self.spoof_ip, "sessionRoot": self.session_code}
        try:
            requests.get(f"{Config.YOUBORA_HOST}/start", params={**common, "contentId": self.channel_id, "live": "true"}, headers=headers, timeout=5)
        except: pass
        count = 0
        while self.running:
            time.sleep(5)
            count += 1
            try:
                requests.get(f"{Config.YOUBORA_HOST}/ping", params={**common, "pingTime": "5", "playhead": str(count * 5)}, headers=headers, timeout=5)
            except: pass
    
    def stop(self):
        self.running = False

# ================================================================================
# STREAMER MPEG-TS COM SELEÇÃO DE ÁUDIO
# ================================================================================

class MPEGTSStreamer:
    """
    Streamer que usa Streamlink + FFmpeg para selecionar faixa de áudio por idioma
    
    Idiomas suportados:
    - por = Português
    - eng = Inglês  
    - spa = Espanhol
    - und = Undefined (primeira faixa)
    """
    
    def __init__(self, channel_id, audio_lang="por"):
        self.channel_id = channel_id
        self.audio_lang = audio_lang
        self.heartbeat = None
        self.streamlink_proc = None
        self.ffmpeg_proc = None
    
    def start(self):
        spoof_ip = get_random_br_ip()
        data, error = get_stream_data(self.channel_id, use_cookies=False, spoof_ip=spoof_ip)
        if error:
            print(f"❌ {error}")
            return None
        
        mpd = data['manifest_raw']
        key = get_decryption_key(mpd, data['license'], data['token'], data['session'])
        
        # Heartbeat
        self.heartbeat = YouboraHeartbeat(mpd, self.channel_id, spoof_ip)
        threading.Thread(target=self.heartbeat.run, daemon=True).start()
        
        # Streamlink - pega o stream bruto
        streamlink_cmd = [
            STREAMLINK_BIN,
            "--http-header", f"User-Agent={HEADERS_TIZEN['User-Agent']}",
            "--http-header", "Referer=https://development.3ss.tv/",
            "--http-header", f"X-Forwarded-For={spoof_ip}",
            "--http-header", f"X-Real-IP={spoof_ip}",
            "--locale", "pt_BR",
            mpd, "best", "--stdout",
            "--ringbuffer-size", "16M",
            "--stream-segment-threads", "4",
            "--stream-timeout", "20",
            "--force"
        ]
        
        if key:
            key_only = key.split(":")[1] if ":" in key else key
            streamlink_cmd.extend(["-decryption_key", key_only])
        
        # FFmpeg - seleciona faixa de áudio por idioma
        # Mapeamento: 0:v (vídeo), 0:a com filtro de idioma
        ffmpeg_cmd = [
            FFMPEG_BIN,
            "-hide_banner",
            "-loglevel", "error",
            "-i", "pipe:0",
            "-map", "0:v:0",  # Primeiro stream de vídeo
        ]
        
        # Adiciona seleção de áudio baseada no idioma
        if self.audio_lang == "por":
            # Tenta português, fallback para primeira faixa
            ffmpeg_cmd.extend(["-map", "0:a:m:language:por?", "-map", "0:a:0?"])
        elif self.audio_lang == "eng":
            ffmpeg_cmd.extend(["-map", "0:a:m:language:eng?", "-map", "0:a:0?"])
        elif self.audio_lang == "spa":
            ffmpeg_cmd.extend(["-map", "0:a:m:language:spa?", "-map", "0:a:0?"])
        else:
            # Primeira faixa disponível
            ffmpeg_cmd.extend(["-map", "0:a:0"])
        
        ffmpeg_cmd.extend([
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ac", "2",  # Stereo
            "-f", "mpegts",
            "pipe:1"
        ])
        
        print(f"🎬 Stream: {self.channel_id} | Áudio: {self.audio_lang}")
        print(f"   Streamlink: {' '.join(streamlink_cmd[:5])}...")
        
        # Pipeline: Streamlink -> FFmpeg
        self.streamlink_proc = subprocess.Popen(
            streamlink_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            bufsize=10**7
        )
        
        self.ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=self.streamlink_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=10**7
        )
        
        return self.ffmpeg_proc
    
    def stop(self):
        if self.heartbeat:
            self.heartbeat.stop()
        for proc in [self.ffmpeg_proc, self.streamlink_proc]:
            if proc:
                try:
                    proc.kill()
                except:
                    pass

# ================================================================================
# ROTAS API
# ================================================================================

@app.route('/api/channels')
def api_channels():
    return jsonify(fetch_channels())

@app.route('/api/stats')
def api_stats():
    return jsonify({'visits': VISITOR_COUNT, 'online': get_online_count()})

@app.route('/api/stream/<channel_id>')
def api_stream(channel_id):
    data, error = get_stream_data(channel_id, use_cookies=True)
    if error: return jsonify({'error': error}), 400
    return jsonify({'manifest': data['manifest'], 'license': data['license'], 'token': data['token']})

@app.route('/api/auth/start', methods=['POST'])
def api_auth_start():
    global auth_session
    auth_session = requests.Session()
    auth_session.headers.update(HEADERS_TIZEN)
    try:
        resp = auth_session.get(Config.URL_AUTH_TOKEN)
        data = resp.json()
        if 'data' in data and 'token' in data['data']:
            return jsonify({'code': data['data']['token']})
        return jsonify({'error': 'Falha'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/poll', methods=['POST'])
def api_auth_poll():
    code = request.json.get('code')
    if not code: return jsonify({'error': 'Código ausente'}), 400
    try:
        resp = auth_session.post(Config.URL_AUTH_TOKEN, json={"token": code})
        if resp.json().get('success'):
            save_cookies(auth_session.cookies)
            return jsonify({'success': True})
        return jsonify({'success': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ================================================================================
# ROTAS DE STREAMING
# ================================================================================

@app.route('/live/<channel_id>')
@app.route('/live/<channel_id>.ts')
def live_stream(channel_id):
    """
    Stream MPEG-TS com seleção de idioma de áudio
    
    Query params:
    - lang: por (português), eng (inglês), spa (espanhol)
    
    Exemplos:
    - /live/70.ts          → Português (padrão)
    - /live/70.ts?lang=por → Português
    - /live/70.ts?lang=eng → Inglês
    """
    audio_lang = request.args.get('lang', 'por')
    
    if audio_lang not in ['por', 'eng', 'spa', 'und']:
        audio_lang = 'por'
    
    streamer = MPEGTSStreamer(channel_id, audio_lang)
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
        except GeneratorExit:
            pass
        except:
            pass
        finally:
            streamer.stop()
            print(f"🔌 Desconectado: {channel_id}")
    
    return Response(
        stream_with_context(generate()), 
        mimetype='video/mp2t',
        headers={
            'Cache-Control': 'no-cache',
            'X-Audio-Language': audio_lang
        }
    )

@app.route('/playlist.m3u')
@app.route('/lista.m3u')
def playlist_m3u():
    """Gera playlist M3U com áudio em português"""
    channels = fetch_channels()
    base_url = request.url_root.rstrip('/').replace('http://', 'https://')
    lang = request.args.get('lang', 'por')
    
    lines = ['#EXTM3U']
    for ch in channels:
        if ch['id'] and ch['id'] != '0':
            lines.append(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-logo="{ch["logo"]}" group-title="{ch["category"]}",{ch["title"]}')
            lines.append(f'{base_url}/live/{ch["id"]}.ts?lang={lang}')
    
    return Response('\n'.join(lines), mimetype='audio/x-mpegurl',
                   headers={'Content-Disposition': 'attachment; filename=claro_tv.m3u'})

# ================================================================================
# PROXY
# ================================================================================

@app.route('/proxy', methods=['GET', 'POST', 'OPTIONS'])
def proxy():
    if request.method == 'OPTIONS':
        r = Response()
        r.headers['Access-Control-Allow-Origin'] = '*'
        r.headers['Access-Control-Allow-Methods'] = '*'
        r.headers['Access-Control-Allow-Headers'] = '*'
        return r
    
    target_url = request.args.get('url')
    if not target_url: return "URL não informada", 400
    
    headers = HEADERS_WEB.copy()
    for h in ['Range', 'Content-Type', 'Authorization']:
        if h in request.headers: headers[h] = request.headers[h]
    
    is_drm = 'verimatrix' in target_url or 'license' in target_url
    cookies = None if is_drm else load_cdn_cookies()
    
    try:
        if '.mpd' in target_url:
            r = proxy_session.get(target_url, headers=headers, cookies=cookies, verify=False)
            content = r.text
            base = target_url.split('?')[0].rsplit('/', 1)[0] + '/'
            if '<BaseURL>' not in content:
                content = content.replace('<Period', f'<Period><BaseURL>{base}</BaseURL>')
            resp = Response(content, status=r.status_code, content_type='application/dash+xml')
        else:
            data = request.get_data() if request.method == 'POST' else None
            r = proxy_session.request(request.method, target_url, headers=headers, data=data, cookies=cookies, stream=True, verify=False)
            def gen():
                for chunk in r.iter_content(16384): yield chunk
            resp = Response(gen(), status=r.status_code)
            for k, v in r.headers.items():
                if k.lower() in ['content-type', 'content-range', 'content-length']:
                    resp.headers[k] = v
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp
    except Exception as e:
        return str(e), 500

# ================================================================================
# PÁGINAS WEB
# ================================================================================

@app.route('/')
def index():
    global VISITOR_COUNT
    VISITOR_COUNT += 1
    ONLINE_USERS[request.remote_addr] = time.time()
    return render_template_string(HTML_HOME, total_visits=VISITOR_COUNT, online_count=get_online_count())

@app.route('/watch/<channel_id>')
def watch(channel_id):
    ONLINE_USERS[request.remote_addr] = time.time()
    return render_template_string(HTML_PLAYER, initial_id=channel_id, online_count=get_online_count())

@app.route('/admin')
def admin():
    return render_template_string(HTML_ADMIN)

# ================================================================================
# TEMPLATES HTML
# ================================================================================

HTML_HOME = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claro TV+ V2</title>
<style>
:root{--p:#e30613;--bg:#0f0f0f;--card:#1a1a1a}*{box-sizing:border-box}
body{background:var(--bg);color:#fff;font-family:sans-serif;margin:0;padding:20px}
.header{text-align:center;margin-bottom:30px}.logo{font-size:28px;font-weight:800;color:var(--p)}
.stats{color:#888;font-size:12px;margin-top:10px}
.controls{max-width:800px;margin:0 auto 20px}
.search{width:100%;padding:12px;border-radius:10px;border:none;background:#252525;color:#fff;font-size:16px}
.cats{display:flex;gap:10px;overflow-x:auto;margin-top:15px}
.cat{padding:8px 16px;background:#252525;border:none;border-radius:20px;color:#aaa;cursor:pointer}
.cat.active,.cat:hover{background:var(--p);color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:15px;max-width:1400px;margin:0 auto}
.card{background:var(--card);border-radius:12px;overflow:hidden;cursor:pointer;border:2px solid transparent;transition:.2s}
.card:hover{transform:scale(1.03);border-color:var(--p)}
.card img{width:100%;height:90px;object-fit:contain;background:#000;padding:10px}
.card-body{padding:12px}.title{font-weight:700;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.prog{font-size:12px;color:#888}
.links{position:fixed;top:20px;right:20px;display:flex;gap:10px}
.links a{color:#fff;background:var(--p);padding:8px 15px;border-radius:8px;text-decoration:none;font-size:12px}
</style></head>
<body>
<div class="links"><a href="/admin">⚙️ Admin</a><a href="/playlist.m3u">📺 M3U</a></div>
<div class="header">
<div class="logo">CLARO TV+ V2</div>
<div class="stats">Visitas: {{total_visits}} | Online: 👤 {{online_count}} | 🎧 Áudio: Português</div>
</div>
<div class="controls">
<input class="search" id="search" placeholder="🔍 Buscar canal..." onkeyup="filter()">
<div class="cats">
<button class="cat active" onclick="setCat('all')">Todos</button>
<button class="cat" onclick="setCat('Esportes')">Esportes</button>
<button class="cat" onclick="setCat('Filmes')">Filmes</button>
<button class="cat" onclick="setCat('Infantil')">Infantil</button>
<button class="cat" onclick="setCat('Notícias')">Notícias</button>
</div></div>
<div class="grid" id="grid"></div>
<script>
let channels=[],cat='all';
fetch('/api/channels').then(r=>r.json()).then(d=>{channels=d;render()});
function render(){
const t=document.getElementById('search').value.toLowerCase();
document.getElementById('grid').innerHTML=channels.filter(c=>
c.id&&c.id!='0'&&c.title.toLowerCase().includes(t)&&(cat=='all'||c.category==cat)
).map(c=>'<div class="card" onclick="location.href=\\'/watch/'+c.id+'\\'"><img src="'+c.logo+'"><div class="card-body"><div class="title">'+c.title+'</div><div class="prog">'+c.program+'</div></div></div>').join('')}
function setCat(c){cat=c;document.querySelectorAll('.cat').forEach(b=>b.classList.remove('active'));event.target.classList.add('active');render()}
function filter(){render()}
</script></body></html>'''

HTML_PLAYER = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Player</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.3.5/shaka-player.ui.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.3.5/controls.min.css">
<style>
body{background:#000;margin:0;height:100vh}
#vc{width:100%;height:100%}video{width:100%;height:100%}
.back{position:fixed;top:20px;left:20px;background:rgba(0,0,0,.7);color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none;z-index:100}
</style></head>
<body>
<a href="/" class="back">← Voltar</a>
<div id="vc" data-shaka-player-container><video id="v" data-shaka-player autoplay></video></div>
<script>
let player;const video=document.getElementById('v');
async function init(){
shaka.polyfill.installAll();
if(shaka.Player.isBrowserSupported()){
player=new shaka.Player(video);
new shaka.ui.Overlay(player,document.getElementById('vc'),video);
player.getNetworkingEngine().registerRequestFilter((t,r)=>{
if(t==shaka.net.NetworkingEngine.RequestType.LICENSE)r.headers['Authorization']=window.drmToken;
if(!r.uris[0].includes('/proxy?url='))r.uris=[location.origin+'/proxy?url='+encodeURIComponent(r.uris[0])]});
player.configure({abr:{enabled:true},preferredAudioLanguage:'pt-BR',streaming:{bufferingGoal:30}});
load()}}
async function load(){
const r=await fetch('/api/stream/{{initial_id}}');
const d=await r.json();
if(d.error){alert(d.error);return}
window.drmToken=d.token;
player.configure({drm:{servers:{'com.widevine.alpha':d.license}}});
await player.load(d.manifest)}
document.addEventListener('DOMContentLoaded',init);
</script></body></html>'''

HTML_ADMIN = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Admin</title>
<style>
body{background:#0f0f0f;color:#fff;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}
.box{background:#1a1a1a;padding:40px;border-radius:15px;text-align:center;max-width:400px}
h1{color:#e30613}
.btn{background:#e30613;color:#fff;border:none;padding:15px 30px;font-size:16px;border-radius:8px;cursor:pointer;width:100%;margin:10px 0}
.code{font-size:40px;font-weight:800;letter-spacing:5px;margin:20px 0;background:#222;padding:20px;border-radius:10px}
.steps{text-align:left;background:#222;padding:15px;border-radius:8px;font-size:14px;line-height:1.8}
.steps a{color:#e30613}
</style></head>
<body>
<div class="box">
<h1>🔧 ADMIN</h1>
<div id="v1">
<button class="btn" onclick="start()">GERAR CÓDIGO</button>
<button class="btn" style="background:#333" onclick="location.href='/'">← Voltar</button>
</div>
<div id="v2" style="display:none">
<div class="steps">1. Acesse: <a href="https://clarotvmais.com.br/ativar" target="_blank">clarotvmais.com.br/ativar</a><br>2. Faça login<br>3. Digite o código:</div>
<div class="code" id="code">----</div>
<div id="st">Aguardando...</div>
</div></div>
<script>
let poll;
async function start(){
document.querySelector('.btn').disabled=true;
const r=await fetch('/api/auth/start',{method:'POST'});
const d=await r.json();
if(d.code){
document.getElementById('v1').style.display='none';
document.getElementById('v2').style.display='block';
document.getElementById('code').innerText=d.code;
poll=setInterval(()=>check(d.code),5000)
}else{alert('Erro')}}
async function check(code){
const r=await fetch('/api/auth/poll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
const d=await r.json();
if(d.success){clearInterval(poll);document.getElementById('st').innerHTML='<span style="color:#4caf50">✅ SUCESSO!</span>';setTimeout(()=>location.href='/',2000)}}
</script></body></html>'''

# ================================================================================
# MAIN
# ================================================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print(" 🚀 CLARO TV+ UNIFIED V2 - Com Áudio em Português")
    print("="*70)
    print(f" 📡 Interface: https://0.0.0.0:{Config.PORTA_HTTPS}")
    print(f" 📺 M3U: https://0.0.0.0:{Config.PORTA_HTTPS}/playlist.m3u")
    print(f" 🎬 Stream: https://0.0.0.0:{Config.PORTA_HTTPS}/live/<ID>.ts")
    print("="*70)
    print(" 🎧 Idiomas de áudio disponíveis:")
    print("    ?lang=por → Português (padrão)")
    print("    ?lang=eng → Inglês")
    print("    ?lang=spa → Espanhol")
    print("="*70 + "\n")
    
    if HYPERCORN_AVAILABLE and os.path.exists(Config.CERT_FILE) and os.path.exists(Config.KEY_FILE):
        config = HypercornConfig()
        config.bind = [f"0.0.0.0:{Config.PORTA_HTTPS}"]
        config.certfile = Config.CERT_FILE
        config.keyfile = Config.KEY_FILE
        print("🔒 Modo HTTPS (Hypercorn)")
        asyncio.run(serve(app, config))
    else:
        print("⚠️  Modo HTTP")
        app.run(host='0.0.0.0', port=Config.PORTA_HTTPS, threaded=True)
