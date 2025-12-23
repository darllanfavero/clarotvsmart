#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
🚀 CLARO TV+ UNIFIED V3 - Painel Admin Completo
================================================================================
- Seleção de idioma, qualidade, legendas
- Configurações do Streamlink editáveis
- Interface moderna e completa
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
app.secret_key = os.environ.get('SECRET_KEY', 'claro_unified_v3_2024')

# ================================================================================
# CONFIGURAÇÃO DO SERVIDOR
# ================================================================================

class ServerConfig:
    PORTA = int(os.environ.get('PORT', 8443))
    CERT_FILE = "cert.pem"
    KEY_FILE = "key.pem"
    COOKIES_FILE = "cookies_finais.txt"
    CDN_COOKIES_FILE = "cookies_cdn.json"
    SETTINGS_FILE = "stream_settings.json"
    DEVICE_FILE = "device.wvd"
    
    # Credenciais Claro
    USERNAME = os.environ.get('CLARO_USER', 'nocbrasil')
    PASSWORD = os.environ.get('CLARO_PASS', 'n0cbr4s1l')
    
    # URLs API
    API_BASE = "https://androidtv-mediation-layer.clarobrasil.mobi"
    URL_CATALOGO = f"{API_BASE}/api/v1/catalog/content_providers"
    URL_PLAYBACK = f"{API_BASE}/api/entitlement/playback"
    URL_AUTH_TOKEN = f"{API_BASE}/api/v1/auth/token"
    URL_AUTH_LOGIN = f"{API_BASE}/api/auth/login"
    
    # Youbora
    YOUBORA_HOST = "https://infinity-c35.youboranqs01.com"
    YOUBORA_ACCOUNT = "clarobrasildev"
    
    ONLINE_TIMEOUT = 300

# ================================================================================
# CONFIGURAÇÕES DE STREAM (EDITÁVEIS PELO ADMIN)
# ================================================================================

DEFAULT_STREAM_SETTINGS = {
    # Idioma
    "locale": "pt_BR",
    "audio_lang": "por",
    "hls_audio_select": "",  # vazio = automático
    
    # Qualidade
    "quality": "best",
    "max_quality": "",  # ex: ">1080p" para excluir
    
    # Legendas
    "mux_subtitles": False,
    "subtitle_lang": "",
    
    # Buffer e Performance
    "ringbuffer_size": "16M",
    "stream_timeout": "20",
    "segment_threads": "4",
    "segment_timeout": "10",
    
    # FFmpeg
    "ffmpeg_fout": "mpegts",
    "ffmpeg_video_transcode": "copy",
    "ffmpeg_audio_transcode": "aac",
    "ffmpeg_copyts": False,
    "ffmpeg_start_at_zero": True,
    
    # Avançado
    "force": True,
    "http_timeout": "20",
    "retry_max": "3",
    "retry_streams": "1",
}

def load_stream_settings():
    """Carrega configurações salvas ou usa padrão"""
    if os.path.exists(ServerConfig.SETTINGS_FILE):
        try:
            with open(ServerConfig.SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
                # Mescla com padrões (para novas opções)
                return {**DEFAULT_STREAM_SETTINGS, **saved}
        except:
            pass
    return DEFAULT_STREAM_SETTINGS.copy()

def save_stream_settings(settings):
    """Salva configurações"""
    try:
        with open(ServerConfig.SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
        return True
    except:
        return False

# Carrega settings global
STREAM_SETTINGS = load_stream_settings()

# ================================================================================
# HEADERS E VARIÁVEIS GLOBAIS
# ================================================================================

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

# Opções disponíveis para o painel
AVAILABLE_LOCALES = [
    {"code": "pt_BR", "name": "🇧🇷 Português (Brasil)"},
    {"code": "en_US", "name": "🇺🇸 English (US)"},
    {"code": "es_ES", "name": "🇪🇸 Español (España)"},
    {"code": "es_MX", "name": "🇲🇽 Español (México)"},
    {"code": "fr_FR", "name": "🇫🇷 Français"},
    {"code": "de_DE", "name": "🇩🇪 Deutsch"},
]

AVAILABLE_AUDIO_LANGS = [
    {"code": "por", "name": "🇧🇷 Português"},
    {"code": "eng", "name": "🇺🇸 English"},
    {"code": "spa", "name": "🇪🇸 Español"},
    {"code": "fre", "name": "🇫🇷 Français"},
    {"code": "ger", "name": "🇩🇪 Deutsch"},
    {"code": "ita", "name": "🇮🇹 Italiano"},
    {"code": "und", "name": "🌐 Original/Undefined"},
]

AVAILABLE_QUALITIES = [
    {"code": "best", "name": "🏆 Melhor Disponível"},
    {"code": "1080p,best", "name": "📺 1080p (Full HD)"},
    {"code": "720p,best", "name": "📺 720p (HD)"},
    {"code": "480p,best", "name": "📺 480p (SD)"},
    {"code": "360p,best", "name": "📺 360p"},
    {"code": "worst", "name": "📉 Menor Qualidade"},
]

AVAILABLE_BUFFER_SIZES = [
    {"code": "8M", "name": "8 MB (Baixa latência)"},
    {"code": "16M", "name": "16 MB (Padrão)"},
    {"code": "32M", "name": "32 MB (Estável)"},
    {"code": "64M", "name": "64 MB (Máximo)"},
]

VISITOR_COUNT = 0
ONLINE_USERS = {}
ACTIVE_STREAMS = {}
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
    ranges = [(177,), (179,), (191,), (186,), (187,), (200,), (201,)]
    base = random.choice(ranges)[0]
    return f"{base}.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}"

def get_stealth_headers(spoof_ip=None):
    h = HEADERS_TIZEN.copy()
    if spoof_ip:
        h['X-Forwarded-For'] = spoof_ip
        h['X-Real-IP'] = spoof_ip
        h['Client-IP'] = spoof_ip
    h['x-request-id'] = str(uuid.uuid4())
    h['x-correlation-id'] = str(uuid.uuid4())
    return h

def get_online_count():
    now = time.time()
    expired = [k for k, v in ONLINE_USERS.items() if now - v > ServerConfig.ONLINE_TIMEOUT]
    for k in expired:
        del ONLINE_USERS[k]
    return len(ONLINE_USERS)

def load_cookies():
    if not os.path.exists(ServerConfig.COOKIES_FILE):
        return None
    try:
        cookies = {}
        with open(ServerConfig.COOKIES_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    cookies[k.strip()] = v.strip()
        return cookies if cookies else None
    except:
        return None

def save_cookies(cookie_jar):
    try:
        with open(ServerConfig.COOKIES_FILE, 'w') as f:
            for c in cookie_jar:
                f.write(f"{c.name}={c.value}\n")
        return True
    except:
        return False

def load_cdn_cookies():
    try:
        with open(ServerConfig.CDN_COOKIES_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_cdn_cookies(cookie_jar):
    try:
        with open(ServerConfig.CDN_COOKIES_FILE, 'w') as f:
            json.dump(requests.utils.dict_from_cookiejar(cookie_jar), f)
    except:
        pass

@lru_cache(maxsize=200)
def resolve_cdn_url(url):
    try:
        r = proxy_session.get(url, allow_redirects=True, stream=True, verify=False, timeout=4, headers=HEADERS_WEB)
        parsed = urllib.parse.urlparse(r.url)
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
        return r.url
    except:
        return url

def categorize_channel(name):
    name = name.lower()
    if any(x in name for x in ['sport', 'espn', 'premiere', 'combate']): return 'Esportes'
    if any(x in name for x in ['telecine', 'hbo', 'megapix', 'universal', 'tnt']): return 'Filmes'
    if any(x in name for x in ['cartoon', 'nick', 'gloob', 'disney']): return 'Infantil'
    if any(x in name for x in ['news', 'cnn', 'bandnews']): return 'Notícias'
    if any(x in name for x in ['globo', 'sbt', 'record', 'band']): return 'Aberto/Variedades'
    return 'Outros'

def format_time(epoch):
    if epoch > 2000000000: epoch /= 1000
    return datetime.fromtimestamp(epoch).strftime('%H:%M')

def get_streamlink_info():
    """Retorna informações do Streamlink"""
    try:
        result = subprocess.run([STREAMLINK_BIN, '--version'], capture_output=True, text=True, timeout=5)
        version = result.stdout.strip() if result.stdout else "Desconhecido"
        return {"available": True, "version": version, "path": STREAMLINK_BIN}
    except:
        return {"available": False, "version": "N/A", "path": STREAMLINK_BIN}

def get_ffmpeg_info():
    """Retorna informações do FFmpeg"""
    try:
        result = subprocess.run([FFMPEG_BIN, '-version'], capture_output=True, text=True, timeout=5)
        lines = result.stdout.split('\n')
        version = lines[0] if lines else "Desconhecido"
        return {"available": True, "version": version, "path": FFMPEG_BIN}
    except:
        return {"available": False, "version": "N/A", "path": FFMPEG_BIN}

# ================================================================================
# API CLARO
# ================================================================================

def fetch_channels():
    cookies = load_cookies()
    if not cookies: return []
    try:
        resp = requests.get(ServerConfig.URL_CATALOGO, headers=HEADERS_TIZEN, 
                          params={"size": "600"}, cookies=cookies, verify=False)
        items = resp.json().get('data', {}).get('items', [])
        channels = []
        for item in items:
            if item.get('live_id') and item.get('title'):
                prog = item.get('current_program', {})
                start, end = prog.get('start_epoch', 0), prog.get('end_epoch', 0)
                now = time.time()
                if start > 2e9: start /= 1000
                if end > 2e9: end /= 1000
                percent = max(0, min(100, ((now-start)/(end-start))*100)) if end > start else 0
                channels.append({
                    'id': item['live_id'],
                    'title': item['title'],
                    'logo': item.get('logo', ''),
                    'program': prog.get('title', 'Ao Vivo'),
                    'time': f"{format_time(start)} - {format_time(end)}" if start else "",
                    'category': categorize_channel(item['title']),
                    'progress': int(percent)
                })
        return channels
    except:
        return []

def get_stream_data(channel_id, use_cookies=True, spoof_ip=None):
    session = requests.Session()
    spoof_ip = spoof_ip or get_random_br_ip()
    session.headers.update(get_stealth_headers(spoof_ip))
    
    if use_cookies:
        cookies = load_cookies()
        if not cookies: return None, "Cookies não encontrados"
        session.cookies.update(cookies)
    else:
        try:
            time.sleep(random.uniform(0.1, 0.3))
            resp = session.post(ServerConfig.URL_AUTH_LOGIN, 
                              json={"username": ServerConfig.USERNAME, "password": ServerConfig.PASSWORD}, timeout=10)
            if not resp.json().get('success'): return None, "Falha no login"
        except Exception as e:
            return None, str(e)
    
    payload = {
        "channel_id": str(channel_id), "type": "TV",
        "city": "Sao Paulo", "state": "Sao Paulo",
        "drm_type": "widevine", "drm_provider": "verimatrix"
    }
    
    try:
        resp = session.post(ServerConfig.URL_PLAYBACK, json=payload, verify=False, timeout=10)
        data = resp.json()
        if not data.get('success'): 
            return None, f"API Error: {data.get('err', {}).get('code')}"
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
    if not PYWIDEVINE_AVAILABLE or not os.path.exists(ServerConfig.DEVICE_FILE):
        return None
    try:
        xml = session.get(mpd_url).text
        pssh = None
        matches = re.findall(r'<cenc:pssh[^>]*>(.*?)</cenc:pssh>', xml)
        if matches: pssh = matches[-1]
        if not pssh:
            for p in re.findall(r'<ContentProtection.*?edef8ba9.*?>.*?</ContentProtection>', xml, re.DOTALL):
                m = re.search(r'>([A-Za-z0-9+/=]{20,})<', p)
                if m: pssh = m.group(1); break
        if not pssh: return None
        
        device = Device.load(ServerConfig.DEVICE_FILE)
        cdm = Cdm.from_device(device)
        sid = cdm.open()
        challenge = cdm.get_license_challenge(sid, PSSH(pssh))
        headers = session.headers.copy()
        headers['authorization'] = auth_token
        res = session.post(license_url, data=challenge, headers=headers)
        if res.status_code == 200:
            cdm.parse_license(sid, res.content)
            for k in cdm.get_keys(sid):
                if k.type == 'CONTENT':
                    key = f"{k.kid.hex}:{k.key.hex()}"
                    cdm.close(sid)
                    return key
        cdm.close(sid)
    except Exception as e:
        print(f"❌ DRM: {e}")
    return None

# ================================================================================
# YOUBORA HEARTBEAT
# ================================================================================

class YouboraHeartbeat:
    def __init__(self, url, channel_id, ip):
        self.url, self.channel_id, self.ip = url, channel_id, ip
        self.running = False
        self.code = f"V3_{''.join(random.choices(string.ascii_lowercase+string.digits, k=10))}"
    
    def run(self):
        self.running = True
        h = get_stealth_headers(self.ip)
        base = {"accountCode": ServerConfig.YOUBORA_ACCOUNT, "ip": self.ip, "sessionRoot": self.code}
        try: requests.get(f"{ServerConfig.YOUBORA_HOST}/start", params={**base, "contentId": self.channel_id}, headers=h, timeout=5)
        except: pass
        n = 0
        while self.running:
            time.sleep(5); n += 1
            try: requests.get(f"{ServerConfig.YOUBORA_HOST}/ping", params={**base, "playhead": str(n*5)}, headers=h, timeout=5)
            except: pass
    
    def stop(self): self.running = False

# ================================================================================
# STREAMER COM CONFIGURAÇÕES DO ADMIN
# ================================================================================

class ConfigurableStreamer:
    """Streamer que usa as configurações do painel admin"""
    
    def __init__(self, channel_id, override_settings=None):
        self.channel_id = channel_id
        self.settings = {**STREAM_SETTINGS, **(override_settings or {})}
        self.heartbeat = None
        self.streamlink_proc = None
        self.ffmpeg_proc = None
    
    def build_streamlink_command(self, mpd_url, spoof_ip, key=None):
        """Constrói comando do Streamlink baseado nas configurações"""
        s = self.settings
        cmd = [STREAMLINK_BIN]
        
        # Headers HTTP
        cmd.extend(["--http-header", f"User-Agent={HEADERS_TIZEN['User-Agent']}"])
        cmd.extend(["--http-header", "Referer=https://development.3ss.tv/"])
        cmd.extend(["--http-header", f"X-Forwarded-For={spoof_ip}"])
        cmd.extend(["--http-header", f"X-Real-IP={spoof_ip}"])
        
        # Locale/Idioma
        if s.get('locale'):
            cmd.extend(["--locale", s['locale']])
        
        # HLS Audio Select
        if s.get('hls_audio_select'):
            cmd.extend(["--hls-audio-select", s['hls_audio_select']])
        
        # Qualidade máxima (exclusão)
        if s.get('max_quality'):
            cmd.extend(["--stream-sorting-excludes", s['max_quality']])
        
        # Legendas
        if s.get('mux_subtitles'):
            cmd.append("--mux-subtitles")
        
        # Buffer
        cmd.extend(["--ringbuffer-size", s.get('ringbuffer_size', '16M')])
        cmd.extend(["--stream-timeout", s.get('stream_timeout', '20')])
        cmd.extend(["--stream-segment-threads", s.get('segment_threads', '4')])
        cmd.extend(["--stream-segment-timeout", s.get('segment_timeout', '10')])
        
        # HTTP timeout
        cmd.extend(["--http-timeout", s.get('http_timeout', '20')])
        
        # Retry
        cmd.extend(["--retry-max", s.get('retry_max', '3')])
        cmd.extend(["--retry-streams", s.get('retry_streams', '1')])
        
        # Force
        if s.get('force', True):
            cmd.append("--force")
        
        # URL e Qualidade
        cmd.append(mpd_url)
        cmd.append(s.get('quality', 'best'))
        cmd.append("--stdout")
        
        # Chave de decriptação
        if key:
            key_only = key.split(":")[1] if ":" in key else key
            cmd.extend(["-decryption_key", key_only])
        
        return cmd
    
    def build_ffmpeg_command(self):
        """Constrói comando do FFmpeg baseado nas configurações"""
        s = self.settings
        cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-i", "pipe:0"]
        
        # Mapeamento de vídeo
        cmd.extend(["-map", "0:v:0"])
        
        # Mapeamento de áudio por idioma
        audio_lang = s.get('audio_lang', 'por')
        if audio_lang and audio_lang != 'und':
            cmd.extend(["-map", f"0:a:m:language:{audio_lang}?"])
        cmd.extend(["-map", "0:a:0?"])  # Fallback
        
        # Transcodificação de vídeo
        video_codec = s.get('ffmpeg_video_transcode', 'copy')
        cmd.extend(["-c:v", video_codec])
        
        # Transcodificação de áudio
        audio_codec = s.get('ffmpeg_audio_transcode', 'aac')
        cmd.extend(["-c:a", audio_codec])
        if audio_codec != 'copy':
            cmd.extend(["-b:a", "192k", "-ac", "2"])
        
        # Timestamps
        if s.get('ffmpeg_copyts'):
            cmd.append("-copyts")
        if s.get('ffmpeg_start_at_zero'):
            cmd.append("-start_at_zero")
        
        # Formato de saída
        cmd.extend(["-f", s.get('ffmpeg_fout', 'mpegts')])
        cmd.append("pipe:1")
        
        return cmd
    
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
        
        # Comandos
        streamlink_cmd = self.build_streamlink_command(mpd, spoof_ip, key)
        ffmpeg_cmd = self.build_ffmpeg_command()
        
        print(f"🎬 Canal: {self.channel_id}")
        print(f"   Idioma: {self.settings.get('locale')} | Áudio: {self.settings.get('audio_lang')}")
        print(f"   Qualidade: {self.settings.get('quality')} | Buffer: {self.settings.get('ringbuffer_size')}")
        
        # Executa pipeline
        self.streamlink_proc = subprocess.Popen(
            streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**7
        )
        self.ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd, stdin=self.streamlink_proc.stdout, 
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**7
        )
        
        return self.ffmpeg_proc
    
    def stop(self):
        if self.heartbeat: self.heartbeat.stop()
        for p in [self.ffmpeg_proc, self.streamlink_proc]:
            if p:
                try: p.kill()
                except: pass

# ================================================================================
# ROTAS API
# ================================================================================

@app.route('/api/channels')
def api_channels():
    return jsonify(fetch_channels())

@app.route('/api/stats')
def api_stats():
    return jsonify({
        'visits': VISITOR_COUNT,
        'online': get_online_count(),
        'streams': len(ACTIVE_STREAMS)
    })

@app.route('/api/stream/<channel_id>')
def api_stream(channel_id):
    data, error = get_stream_data(channel_id, use_cookies=True)
    if error: return jsonify({'error': error}), 400
    return jsonify({
        'manifest': data['manifest'],
        'license': data['license'],
        'token': data['token']
    })

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    global STREAM_SETTINGS
    if request.method == 'GET':
        return jsonify({
            'settings': STREAM_SETTINGS,
            'options': {
                'locales': AVAILABLE_LOCALES,
                'audio_langs': AVAILABLE_AUDIO_LANGS,
                'qualities': AVAILABLE_QUALITIES,
                'buffer_sizes': AVAILABLE_BUFFER_SIZES
            }
        })
    else:
        new_settings = request.json
        STREAM_SETTINGS.update(new_settings)
        save_stream_settings(STREAM_SETTINGS)
        return jsonify({'success': True, 'settings': STREAM_SETTINGS})

@app.route('/api/system-info')
def api_system_info():
    return jsonify({
        'streamlink': get_streamlink_info(),
        'ffmpeg': get_ffmpeg_info(),
        'pywidevine': PYWIDEVINE_AVAILABLE,
        'device_file': os.path.exists(ServerConfig.DEVICE_FILE),
        'cookies_file': os.path.exists(ServerConfig.COOKIES_FILE),
        'settings_file': os.path.exists(ServerConfig.SETTINGS_FILE)
    })

@app.route('/api/auth/start', methods=['POST'])
def api_auth_start():
    global auth_session
    auth_session = requests.Session()
    auth_session.headers.update(HEADERS_TIZEN)
    try:
        resp = auth_session.get(ServerConfig.URL_AUTH_TOKEN)
        data = resp.json()
        if 'data' in data and 'token' in data['data']:
            return jsonify({'code': data['data']['token']})
        return jsonify({'error': 'Falha ao gerar código'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/poll', methods=['POST'])
def api_auth_poll():
    code = request.json.get('code')
    if not code: return jsonify({'error': 'Código ausente'}), 400
    try:
        resp = auth_session.post(ServerConfig.URL_AUTH_TOKEN, json={"token": code})
        if resp.json().get('success'):
            save_cookies(auth_session.cookies)
            return jsonify({'success': True})
        return jsonify({'success': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/test-stream/<channel_id>')
def api_test_stream(channel_id):
    """Testa se consegue obter dados do canal"""
    data, error = get_stream_data(channel_id, use_cookies=False)
    if error:
        return jsonify({'success': False, 'error': error})
    return jsonify({
        'success': True,
        'manifest': data['manifest'][:100] + '...',
        'has_license': bool(data['license']),
        'has_token': bool(data['token'])
    })

# ================================================================================
# ROTAS DE STREAMING
# ================================================================================

@app.route('/live/<channel_id>')
@app.route('/live/<channel_id>.ts')
def live_stream(channel_id):
    """Stream MPEG-TS com configurações do admin"""
    # Override por query params
    override = {}
    if request.args.get('lang'):
        override['audio_lang'] = request.args.get('lang')
    if request.args.get('locale'):
        override['locale'] = request.args.get('locale')
    if request.args.get('quality'):
        override['quality'] = request.args.get('quality')
    
    streamer = ConfigurableStreamer(channel_id, override if override else None)
    process = streamer.start()
    
    if not process:
        return "Erro ao iniciar stream", 404
    
    stream_id = str(uuid.uuid4())
    ACTIVE_STREAMS[stream_id] = {'channel': channel_id, 'started': time.time()}
    
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
            ACTIVE_STREAMS.pop(stream_id, None)
            print(f"🔌 Desconectado: {channel_id}")
    
    return Response(
        stream_with_context(generate()),
        mimetype='video/mp2t',
        headers={'Cache-Control': 'no-cache', 'X-Stream-ID': stream_id}
    )

@app.route('/playlist.m3u')
def playlist_m3u():
    channels = fetch_channels()
    base = request.url_root.rstrip('/').replace('http://', 'https://')
    lang = STREAM_SETTINGS.get('audio_lang', 'por')
    lines = ['#EXTM3U']
    for ch in channels:
        if ch['id'] and ch['id'] != '0':
            lines.append(f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-logo="{ch["logo"]}" group-title="{ch["category"]}",{ch["title"]}')
            lines.append(f'{base}/live/{ch["id"]}.ts?lang={lang}')
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
    
    url = request.args.get('url')
    if not url: return "URL ausente", 400
    
    headers = HEADERS_WEB.copy()
    for h in ['Range', 'Content-Type', 'Authorization']:
        if h in request.headers: headers[h] = request.headers[h]
    
    is_drm = 'verimatrix' in url or 'license' in url
    cookies = None if is_drm else load_cdn_cookies()
    
    try:
        if '.mpd' in url:
            r = proxy_session.get(url, headers=headers, cookies=cookies, verify=False)
            content = r.text
            base = url.split('?')[0].rsplit('/', 1)[0] + '/'
            if '<BaseURL>' not in content:
                content = content.replace('<Period', f'<Period><BaseURL>{base}</BaseURL>')
            resp = Response(content, status=r.status_code, content_type='application/dash+xml')
        else:
            data = request.get_data() if request.method == 'POST' else None
            r = proxy_session.request(request.method, url, headers=headers, data=data, 
                                     cookies=cookies, stream=True, verify=False)
            resp = Response((chunk for chunk in r.iter_content(16384)), status=r.status_code)
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
    return render_template_string(HTML_HOME, visits=VISITOR_COUNT, online=get_online_count())

@app.route('/watch/<channel_id>')
def watch(channel_id):
    ONLINE_USERS[request.remote_addr] = time.time()
    return render_template_string(HTML_PLAYER, channel_id=channel_id)

@app.route('/admin')
def admin():
    return render_template_string(HTML_ADMIN)

# ================================================================================
# TEMPLATES HTML
# ================================================================================

HTML_HOME = '''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claro TV+ V3</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
:root{--p:#e30613;--bg:#0a0a0a;--card:#141414;--border:#222}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:#fff;font-family:'Inter',sans-serif;min-height:100vh}
.navbar{background:#111;border-bottom:1px solid var(--border);padding:15px 20px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:100}
.logo{font-size:24px;font-weight:800;color:var(--p)}
.nav-links{display:flex;gap:15px}
.nav-links a{color:#888;text-decoration:none;font-size:14px;padding:8px 16px;border-radius:8px;transition:.2s}
.nav-links a:hover,.nav-links a.active{background:var(--p);color:#fff}
.stats-bar{background:#111;padding:10px 20px;display:flex;gap:30px;font-size:13px;color:#666;border-bottom:1px solid var(--border)}
.stat{display:flex;align-items:center;gap:8px}
.stat-value{color:#fff;font-weight:600}
.container{max-width:1400px;margin:0 auto;padding:20px}
.search-box{margin-bottom:20px}
.search-input{width:100%;padding:14px 20px;border-radius:12px;border:1px solid var(--border);background:#111;color:#fff;font-size:16px;outline:none}
.search-input:focus{border-color:var(--p)}
.categories{display:flex;gap:10px;margin-bottom:20px;overflow-x:auto;padding-bottom:10px}
.cat-btn{padding:10px 20px;background:#111;border:1px solid var(--border);border-radius:25px;color:#888;cursor:pointer;font-weight:600;white-space:nowrap;transition:.2s}
.cat-btn:hover,.cat-btn.active{background:var(--p);border-color:var(--p);color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:15px}
.card{background:var(--card);border-radius:12px;overflow:hidden;cursor:pointer;border:2px solid transparent;transition:.3s}
.card:hover{transform:translateY(-5px);border-color:var(--p);box-shadow:0 10px 40px rgba(227,6,19,.2)}
.card-img{width:100%;height:100px;object-fit:contain;background:#000;padding:15px}
.card-body{padding:15px}
.card-title{font-weight:700;font-size:14px;margin-bottom:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card-prog{font-size:12px;color:#666;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card-time{font-size:11px;color:#444;margin-top:5px}
.progress-bar{height:3px;background:#222;border-radius:2px;margin-top:10px;overflow:hidden}
.progress-fill{height:100%;background:var(--p);border-radius:2px}
.loader{text-align:center;padding:50px;color:#444}
@media(max-width:600px){.grid{grid-template-columns:repeat(auto-fill,minmax(140px,1fr))}}
</style>
</head>
<body>
<nav class="navbar">
<div class="logo">CLARO TV+ <span style="font-size:12px;color:#666">V3</span></div>
<div class="nav-links">
<a href="/" class="active">📺 Canais</a>
<a href="/admin">⚙️ Admin</a>
<a href="/playlist.m3u">📋 M3U</a>
</div>
</nav>
<div class="stats-bar">
<div class="stat">👁️ Visitas: <span class="stat-value">{{visits}}</span></div>
<div class="stat">👤 Online: <span class="stat-value">{{online}}</span></div>
<div class="stat" id="streams-stat">🎬 Streams: <span class="stat-value">0</span></div>
</div>
<div class="container">
<div class="search-box">
<input type="text" class="search-input" id="search" placeholder="🔍 Buscar canal..." onkeyup="filter()">
</div>
<div class="categories" id="categories">
<button class="cat-btn active" onclick="setCat('all')">Todos</button>
<button class="cat-btn" onclick="setCat('Esportes')">⚽ Esportes</button>
<button class="cat-btn" onclick="setCat('Filmes')">🎬 Filmes</button>
<button class="cat-btn" onclick="setCat('Infantil')">🧒 Infantil</button>
<button class="cat-btn" onclick="setCat('Notícias')">📰 Notícias</button>
<button class="cat-btn" onclick="setCat('Aberto/Variedades')">📺 Abertos</button>
</div>
<div class="grid" id="grid"></div>
<div class="loader" id="loader">Carregando canais...</div>
</div>
<script>
let channels=[],cat='all';
fetch('/api/channels').then(r=>r.json()).then(d=>{
channels=d;
document.getElementById('loader').style.display='none';
render();
});
setInterval(()=>{
fetch('/api/stats').then(r=>r.json()).then(d=>{
document.querySelector('#streams-stat .stat-value').textContent=d.streams;
});
},10000);
function render(){
const t=document.getElementById('search').value.toLowerCase();
const g=document.getElementById('grid');
g.innerHTML=channels.filter(c=>c.id&&c.id!='0'&&c.title.toLowerCase().includes(t)&&(cat=='all'||c.category==cat))
.map(c=>`<div class="card" onclick="location.href='/watch/${c.id}'">
<img src="${c.logo}" class="card-img" loading="lazy">
<div class="card-body">
<div class="card-title">${c.title}</div>
<div class="card-prog">${c.program}</div>
<div class="card-time">${c.time}</div>
<div class="progress-bar"><div class="progress-fill" style="width:${c.progress}%"></div></div>
</div></div>`).join('');
}
function setCat(c){
cat=c;
document.querySelectorAll('.cat-btn').forEach(b=>b.classList.remove('active'));
event.target.classList.add('active');
render();
}
function filter(){render()}
</script>
</body>
</html>'''

HTML_PLAYER = '''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Player - Claro TV+</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.3.5/shaka-player.ui.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.3.5/controls.min.css">
<style>
body{background:#000;margin:0;height:100vh;font-family:sans-serif}
#container{width:100%;height:100%}
video{width:100%;height:100%}
.top-bar{position:fixed;top:0;left:0;right:0;padding:20px;background:linear-gradient(to bottom,rgba(0,0,0,.9),transparent);display:flex;justify-content:space-between;align-items:center;z-index:100;opacity:0;transition:.3s}
body:hover .top-bar{opacity:1}
.back-btn{background:rgba(255,255,255,.1);color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-size:14px;display:flex;align-items:center;gap:8px}
.back-btn:hover{background:#e30613}
.channel-info{color:#fff;text-align:right}
.channel-title{font-weight:700;font-size:16px}
.channel-prog{font-size:12px;color:#888}
</style>
</head>
<body>
<div class="top-bar">
<button class="back-btn" onclick="location.href='/'">← Voltar</button>
<div class="channel-info">
<div class="channel-title" id="title">Carregando...</div>
<div class="channel-prog" id="prog"></div>
</div>
</div>
<div id="container" data-shaka-player-container>
<video id="video" data-shaka-player autoplay></video>
</div>
<script>
const channelId="{{channel_id}}";
let player;
async function init(){
shaka.polyfill.installAll();
if(!shaka.Player.isBrowserSupported()){alert('Navegador não suportado');return}
player=new shaka.Player(document.getElementById('video'));
new shaka.ui.Overlay(player,document.getElementById('container'),document.getElementById('video'));
player.getNetworkingEngine().registerRequestFilter((type,req)=>{
if(type==shaka.net.NetworkingEngine.RequestType.LICENSE)req.headers['Authorization']=window.drmToken;
if(!req.uris[0].includes('/proxy?url='))req.uris=[location.origin+'/proxy?url='+encodeURIComponent(req.uris[0])];
});
player.configure({
abr:{enabled:true,defaultBandwidthEstimate:5000000},
preferredAudioLanguage:'pt-BR',
preferredTextLanguage:'pt-BR',
streaming:{bufferingGoal:30,rebufferingGoal:5}
});
// Carrega info do canal
fetch('/api/channels').then(r=>r.json()).then(channels=>{
const ch=channels.find(c=>c.id==channelId);
if(ch){
document.getElementById('title').textContent=ch.title;
document.getElementById('prog').textContent=ch.program;
}
});
// Carrega stream
const r=await fetch('/api/stream/'+channelId);
const d=await r.json();
if(d.error){alert(d.error);return}
window.drmToken=d.token;
player.configure({drm:{servers:{'com.widevine.alpha':d.license}}});
await player.load(d.manifest);
}
document.addEventListener('DOMContentLoaded',init);
</script>
</body>
</html>'''

HTML_ADMIN = '''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin - Claro TV+ V3</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
:root{--p:#e30613;--bg:#0a0a0a;--card:#141414;--border:#222;--success:#00c853;--warning:#ff9800}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:#fff;font-family:'Inter',sans-serif;min-height:100vh}
.navbar{background:#111;border-bottom:1px solid var(--border);padding:15px 20px;display:flex;justify-content:space-between;align-items:center}
.logo{font-size:24px;font-weight:800;color:var(--p)}
.nav-links a{color:#888;text-decoration:none;font-size:14px;padding:8px 16px;border-radius:8px;margin-left:10px}
.nav-links a:hover{background:var(--p);color:#fff}
.container{max-width:1200px;margin:0 auto;padding:20px}
.tabs{display:flex;gap:10px;margin-bottom:20px;border-bottom:1px solid var(--border);padding-bottom:15px}
.tab{padding:12px 24px;background:transparent;border:none;color:#666;cursor:pointer;font-size:14px;font-weight:600;border-radius:8px;transition:.2s}
.tab:hover{color:#fff}
.tab.active{background:var(--p);color:#fff}
.panel{display:none}
.panel.active{display:block}
.card{background:var(--card);border-radius:16px;padding:25px;margin-bottom:20px;border:1px solid var(--border)}
.card-title{font-size:18px;font-weight:700;margin-bottom:20px;display:flex;align-items:center;gap:10px}
.card-title span{font-size:24px}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}
.form-group{margin-bottom:15px}
.form-label{display:block;font-size:13px;color:#888;margin-bottom:8px;font-weight:600}
.form-select,.form-input{width:100%;padding:12px 15px;border-radius:10px;border:1px solid var(--border);background:#111;color:#fff;font-size:14px;outline:none}
.form-select:focus,.form-input:focus{border-color:var(--p)}
.form-check{display:flex;align-items:center;gap:10px;cursor:pointer}
.form-check input{width:20px;height:20px;accent-color:var(--p)}
.btn{padding:12px 24px;border-radius:10px;border:none;font-weight:600;cursor:pointer;font-size:14px;transition:.2s}
.btn-primary{background:var(--p);color:#fff}
.btn-primary:hover{background:#ff1f2d}
.btn-secondary{background:#333;color:#fff}
.btn-secondary:hover{background:#444}
.status-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:15px}
.status-item{background:#111;padding:15px;border-radius:10px;border:1px solid var(--border)}
.status-label{font-size:12px;color:#666;margin-bottom:5px}
.status-value{font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px}
.status-ok{color:var(--success)}
.status-error{color:var(--p)}
.code-box{background:#000;border-radius:10px;padding:20px;font-family:monospace;font-size:13px;color:#0f0;overflow-x:auto;max-height:300px;overflow-y:auto}
.auth-box{text-align:center;padding:40px}
.auth-code{font-size:48px;font-weight:800;letter-spacing:10px;background:#000;padding:20px 40px;border-radius:15px;margin:20px 0;border:2px dashed #333}
.auth-steps{text-align:left;background:#111;padding:20px;border-radius:10px;margin-bottom:20px}
.auth-steps a{color:var(--p)}
.toast{position:fixed;bottom:20px;right:20px;background:var(--success);color:#fff;padding:15px 25px;border-radius:10px;font-weight:600;opacity:0;transition:.3s;z-index:1000}
.toast.show{opacity:1}
.toast.error{background:var(--p)}
</style>
</head>
<body>
<nav class="navbar">
<div class="logo">⚙️ PAINEL ADMIN</div>
<div class="nav-links">
<a href="/">📺 Canais</a>
<a href="/playlist.m3u">📋 M3U</a>
</div>
</nav>
<div class="container">
<div class="tabs">
<button class="tab active" onclick="showPanel('settings')">🎛️ Configurações</button>
<button class="tab" onclick="showPanel('auth')">🔑 Autenticação</button>
<button class="tab" onclick="showPanel('system')">💻 Sistema</button>
<button class="tab" onclick="showPanel('preview')">👁️ Preview Comando</button>
</div>

<!-- PAINEL: CONFIGURAÇÕES -->
<div class="panel active" id="panel-settings">
<div class="card">
<div class="card-title"><span>🌍</span> Idioma e Áudio</div>
<div class="form-grid">
<div class="form-group">
<label class="form-label">LOCALE (Região)</label>
<select class="form-select" id="set-locale"></select>
</div>
<div class="form-group">
<label class="form-label">IDIOMA DO ÁUDIO</label>
<select class="form-select" id="set-audio-lang"></select>
</div>
<div class="form-group">
<label class="form-label">HLS AUDIO SELECT (Avançado)</label>
<input type="text" class="form-input" id="set-hls-audio" placeholder="Ex: Portuguese,English ou pt,en">
</div>
</div>
</div>

<div class="card">
<div class="card-title"><span>📺</span> Qualidade de Vídeo</div>
<div class="form-grid">
<div class="form-group">
<label class="form-label">QUALIDADE PADRÃO</label>
<select class="form-select" id="set-quality"></select>
</div>
<div class="form-group">
<label class="form-label">EXCLUIR QUALIDADES (Ex: >1080p)</label>
<input type="text" class="form-input" id="set-max-quality" placeholder="Deixe vazio para todas">
</div>
</div>
</div>

<div class="card">
<div class="card-title"><span>📝</span> Legendas</div>
<div class="form-grid">
<div class="form-group">
<label class="form-check">
<input type="checkbox" id="set-mux-subtitles">
<span>Incluir legendas no stream (--mux-subtitles)</span>
</label>
</div>
<div class="form-group">
<label class="form-label">IDIOMA DA LEGENDA</label>
<input type="text" class="form-input" id="set-subtitle-lang" placeholder="Ex: pt, en, es">
</div>
</div>
</div>

<div class="card">
<div class="card-title"><span>⚡</span> Buffer e Performance</div>
<div class="form-grid">
<div class="form-group">
<label class="form-label">TAMANHO DO BUFFER</label>
<select class="form-select" id="set-buffer"></select>
</div>
<div class="form-group">
<label class="form-label">TIMEOUT DO STREAM (segundos)</label>
<input type="number" class="form-input" id="set-timeout" value="20">
</div>
<div class="form-group">
<label class="form-label">THREADS DE SEGMENTO</label>
<input type="number" class="form-input" id="set-threads" value="4">
</div>
<div class="form-group">
<label class="form-label">TIMEOUT DE SEGMENTO (segundos)</label>
<input type="number" class="form-input" id="set-seg-timeout" value="10">
</div>
</div>
</div>

<div class="card">
<div class="card-title"><span>🎬</span> FFmpeg</div>
<div class="form-grid">
<div class="form-group">
<label class="form-label">FORMATO DE SAÍDA</label>
<select class="form-select" id="set-ffmpeg-fout">
<option value="mpegts">MPEG-TS (Recomendado)</option>
<option value="matroska">Matroska (MKV)</option>
<option value="mp4">MP4</option>
</select>
</div>
<div class="form-group">
<label class="form-label">CODEC DE VÍDEO</label>
<select class="form-select" id="set-ffmpeg-video">
<option value="copy">Copy (Sem recodificar)</option>
<option value="h264">H.264</option>
<option value="libx264">libx264</option>
</select>
</div>
<div class="form-group">
<label class="form-label">CODEC DE ÁUDIO</label>
<select class="form-select" id="set-ffmpeg-audio">
<option value="aac">AAC (Recomendado)</option>
<option value="copy">Copy (Sem recodificar)</option>
<option value="mp3">MP3</option>
<option value="ac3">AC3</option>
</select>
</div>
<div class="form-group">
<label class="form-check">
<input type="checkbox" id="set-ffmpeg-copyts">
<span>Copiar timestamps (--ffmpeg-copyts)</span>
</label>
</div>
<div class="form-group">
<label class="form-check">
<input type="checkbox" id="set-ffmpeg-start-zero" checked>
<span>Iniciar em zero (--ffmpeg-start-at-zero)</span>
</label>
</div>
</div>
</div>

<div style="display:flex;gap:15px;margin-top:20px">
<button class="btn btn-primary" onclick="saveSettings()">💾 Salvar Configurações</button>
<button class="btn btn-secondary" onclick="loadSettings()">🔄 Recarregar</button>
<button class="btn btn-secondary" onclick="resetSettings()">↩️ Restaurar Padrões</button>
</div>
</div>

<!-- PAINEL: AUTENTICAÇÃO -->
<div class="panel" id="panel-auth">
<div class="card">
<div class="card-title"><span>🔑</span> Renovar Cookies de Autenticação</div>
<div class="auth-box" id="auth-initial">
<p style="color:#888;margin-bottom:20px">Renove os cookies para manter o acesso aos canais</p>
<button class="btn btn-primary" onclick="startAuth()">🚀 GERAR CÓDIGO DE ACESSO</button>
</div>
<div class="auth-box" id="auth-pending" style="display:none">
<div class="auth-steps">
<p><strong>Siga os passos:</strong></p>
<p>1. Acesse: <a href="https://www.clarotvmais.com.br/ativar" target="_blank">clarotvmais.com.br/ativar</a></p>
<p>2. Faça login com sua conta Claro</p>
<p>3. Digite o código abaixo:</p>
</div>
<div class="auth-code" id="auth-code">------</div>
<p id="auth-status" style="color:#888">Aguardando ativação...</p>
</div>
</div>
</div>

<!-- PAINEL: SISTEMA -->
<div class="panel" id="panel-system">
<div class="card">
<div class="card-title"><span>💻</span> Informações do Sistema</div>
<div class="status-grid" id="system-status">
<div class="status-item">
<div class="status-label">Carregando...</div>
</div>
</div>
</div>
<div class="card">
<div class="card-title"><span>📊</span> Estatísticas</div>
<div class="status-grid" id="stats-grid">
<div class="status-item">
<div class="status-label">VISITAS</div>
<div class="status-value" id="stat-visits">-</div>
</div>
<div class="status-item">
<div class="status-label">ONLINE</div>
<div class="status-value" id="stat-online">-</div>
</div>
<div class="status-item">
<div class="status-label">STREAMS ATIVOS</div>
<div class="status-value" id="stat-streams">-</div>
</div>
</div>
</div>
</div>

<!-- PAINEL: PREVIEW -->
<div class="panel" id="panel-preview">
<div class="card">
<div class="card-title"><span>👁️</span> Preview do Comando Streamlink</div>
<p style="color:#888;margin-bottom:15px">Este é o comando que será executado com as configurações atuais:</p>
<div class="code-box" id="preview-cmd">Carregando...</div>
</div>
<div class="card">
<div class="card-title"><span>🧪</span> Testar Canal</div>
<div style="display:flex;gap:10px">
<input type="text" class="form-input" id="test-channel" placeholder="ID do canal (ex: 70)" style="max-width:200px">
<button class="btn btn-primary" onclick="testChannel()">▶️ Testar</button>
</div>
<div id="test-result" style="margin-top:15px"></div>
</div>
</div>
</div>

<div class="toast" id="toast"></div>

<script>
let settings={};
let options={};

// Inicialização
document.addEventListener('DOMContentLoaded',()=>{
loadSettings();
loadSystemInfo();
loadStats();
setInterval(loadStats,10000);
});

function showPanel(name){
document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
document.getElementById('panel-'+name).classList.add('active');
event.target.classList.add('active');
if(name=='preview')updatePreview();
if(name=='system')loadSystemInfo();
}

function showToast(msg,isError=false){
const t=document.getElementById('toast');
t.textContent=msg;
t.className='toast show'+(isError?' error':'');
setTimeout(()=>t.className='toast',3000);
}

async function loadSettings(){
const r=await fetch('/api/settings');
const d=await r.json();
settings=d.settings;
options=d.options;
populateOptions();
applySettings();
}

function populateOptions(){
// Locales
const localeSelect=document.getElementById('set-locale');
localeSelect.innerHTML=options.locales.map(l=>`<option value="${l.code}">${l.name}</option>`).join('');
// Audio langs
const audioSelect=document.getElementById('set-audio-lang');
audioSelect.innerHTML=options.audio_langs.map(l=>`<option value="${l.code}">${l.name}</option>`).join('');
// Qualities
const qualitySelect=document.getElementById('set-quality');
qualitySelect.innerHTML=options.qualities.map(q=>`<option value="${q.code}">${q.name}</option>`).join('');
// Buffer
const bufferSelect=document.getElementById('set-buffer');
bufferSelect.innerHTML=options.buffer_sizes.map(b=>`<option value="${b.code}">${b.name}</option>`).join('');
}

function applySettings(){
document.getElementById('set-locale').value=settings.locale||'pt_BR';
document.getElementById('set-audio-lang').value=settings.audio_lang||'por';
document.getElementById('set-hls-audio').value=settings.hls_audio_select||'';
document.getElementById('set-quality').value=settings.quality||'best';
document.getElementById('set-max-quality').value=settings.max_quality||'';
document.getElementById('set-mux-subtitles').checked=settings.mux_subtitles||false;
document.getElementById('set-subtitle-lang').value=settings.subtitle_lang||'';
document.getElementById('set-buffer').value=settings.ringbuffer_size||'16M';
document.getElementById('set-timeout').value=settings.stream_timeout||'20';
document.getElementById('set-threads').value=settings.segment_threads||'4';
document.getElementById('set-seg-timeout').value=settings.segment_timeout||'10';
document.getElementById('set-ffmpeg-fout').value=settings.ffmpeg_fout||'mpegts';
document.getElementById('set-ffmpeg-video').value=settings.ffmpeg_video_transcode||'copy';
document.getElementById('set-ffmpeg-audio').value=settings.ffmpeg_audio_transcode||'aac';
document.getElementById('set-ffmpeg-copyts').checked=settings.ffmpeg_copyts||false;
document.getElementById('set-ffmpeg-start-zero').checked=settings.ffmpeg_start_at_zero!==false;
}

function gatherSettings(){
return{
locale:document.getElementById('set-locale').value,
audio_lang:document.getElementById('set-audio-lang').value,
hls_audio_select:document.getElementById('set-hls-audio').value,
quality:document.getElementById('set-quality').value,
max_quality:document.getElementById('set-max-quality').value,
mux_subtitles:document.getElementById('set-mux-subtitles').checked,
subtitle_lang:document.getElementById('set-subtitle-lang').value,
ringbuffer_size:document.getElementById('set-buffer').value,
stream_timeout:document.getElementById('set-timeout').value,
segment_threads:document.getElementById('set-threads').value,
segment_timeout:document.getElementById('set-seg-timeout').value,
ffmpeg_fout:document.getElementById('set-ffmpeg-fout').value,
ffmpeg_video_transcode:document.getElementById('set-ffmpeg-video').value,
ffmpeg_audio_transcode:document.getElementById('set-ffmpeg-audio').value,
ffmpeg_copyts:document.getElementById('set-ffmpeg-copyts').checked,
ffmpeg_start_at_zero:document.getElementById('set-ffmpeg-start-zero').checked
};
}

async function saveSettings(){
const newSettings=gatherSettings();
const r=await fetch('/api/settings',{
method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(newSettings)
});
const d=await r.json();
if(d.success){
settings=d.settings;
showToast('✅ Configurações salvas!');
}else{
showToast('❌ Erro ao salvar',true);
}
}

async function resetSettings(){
if(!confirm('Restaurar todas as configurações para o padrão?'))return;
const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
loadSettings();
showToast('↩️ Configurações restauradas');
}

function updatePreview(){
const s=gatherSettings();
let cmd='streamlink \\\\\\n';
cmd+='  --http-header "User-Agent=..." \\\\\\n';
cmd+='  --http-header "Referer=https://development.3ss.tv/" \\\\\\n';
cmd+='  --http-header "X-Forwarded-For=<IP_SPOOF>" \\\\\\n';
if(s.locale)cmd+=`  --locale "${s.locale}" \\\\\\n`;
if(s.hls_audio_select)cmd+=`  --hls-audio-select "${s.hls_audio_select}" \\\\\\n`;
if(s.max_quality)cmd+=`  --stream-sorting-excludes "${s.max_quality}" \\\\\\n`;
if(s.mux_subtitles)cmd+='  --mux-subtitles \\\\\\n';
cmd+=`  --ringbuffer-size ${s.ringbuffer_size} \\\\\\n`;
cmd+=`  --stream-timeout ${s.stream_timeout} \\\\\\n`;
cmd+=`  --stream-segment-threads ${s.segment_threads} \\\\\\n`;
cmd+='  --force \\\\\\n';
cmd+=`  "<MPD_URL>" ${s.quality} --stdout \\\\\\n`;
cmd+='  -decryption_key "<KEY>"\\n\\n';
cmd+='# FFmpeg pipeline:\\n';
cmd+=`ffmpeg -i pipe:0 -map 0:v:0 -map 0:a:m:language:${s.audio_lang}? \\\\\\n`;
cmd+=`  -c:v ${s.ffmpeg_video_transcode} -c:a ${s.ffmpeg_audio_transcode} \\\\\\n`;
if(s.ffmpeg_copyts)cmd+='  -copyts ';
if(s.ffmpeg_start_at_zero)cmd+='-start_at_zero ';
cmd+=`\\\\\\n  -f ${s.ffmpeg_fout} pipe:1`;
document.getElementById('preview-cmd').textContent=cmd;
}

async function loadSystemInfo(){
const r=await fetch('/api/system-info');
const d=await r.json();
let html='';
html+=`<div class="status-item"><div class="status-label">STREAMLINK</div><div class="status-value ${d.streamlink.available?'status-ok':'status-error'}">${d.streamlink.available?'✅':'❌'} ${d.streamlink.version}</div></div>`;
html+=`<div class="status-item"><div class="status-label">FFMPEG</div><div class="status-value ${d.ffmpeg.available?'status-ok':'status-error'}">${d.ffmpeg.available?'✅':'❌'} Instalado</div></div>`;
html+=`<div class="status-item"><div class="status-label">PYWIDEVINE</div><div class="status-value ${d.pywidevine?'status-ok':'status-error'}">${d.pywidevine?'✅ Disponível':'❌ Não instalado'}</div></div>`;
html+=`<div class="status-item"><div class="status-label">DEVICE.WVD</div><div class="status-value ${d.device_file?'status-ok':'status-error'}">${d.device_file?'✅ Encontrado':'❌ Não encontrado'}</div></div>`;
html+=`<div class="status-item"><div class="status-label">COOKIES</div><div class="status-value ${d.cookies_file?'status-ok':'status-error'}">${d.cookies_file?'✅ Configurado':'❌ Não configurado'}</div></div>`;
document.getElementById('system-status').innerHTML=html;
}

async function loadStats(){
const r=await fetch('/api/stats');
const d=await r.json();
document.getElementById('stat-visits').textContent=d.visits;
document.getElementById('stat-online').textContent=d.online;
document.getElementById('stat-streams').textContent=d.streams;
}

// Autenticação
let authPoll;
async function startAuth(){
document.getElementById('auth-initial').style.display='none';
document.getElementById('auth-pending').style.display='block';
const r=await fetch('/api/auth/start',{method:'POST'});
const d=await r.json();
if(d.code){
document.getElementById('auth-code').textContent=d.code;
authPoll=setInterval(()=>checkAuth(d.code),5000);
}else{
document.getElementById('auth-status').innerHTML='<span style="color:#e30613">❌ Erro: '+d.error+'</span>';
}
}

async function checkAuth(code){
const r=await fetch('/api/auth/poll',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
const d=await r.json();
if(d.success){
clearInterval(authPoll);
document.getElementById('auth-status').innerHTML='<span style="color:#00c853">✅ SUCESSO! Cookies renovados.</span>';
showToast('✅ Autenticação concluída!');
setTimeout(()=>{
document.getElementById('auth-initial').style.display='block';
document.getElementById('auth-pending').style.display='none';
},3000);
}
}

async function testChannel(){
const id=document.getElementById('test-channel').value;
if(!id){alert('Digite um ID de canal');return}
document.getElementById('test-result').innerHTML='<p style="color:#888">Testando...</p>';
const r=await fetch('/api/test-stream/'+id);
const d=await r.json();
if(d.success){
document.getElementById('test-result').innerHTML=`<p style="color:#00c853">✅ Canal OK!</p><p style="font-size:12px;color:#666">Manifest: ${d.manifest}</p>`;
}else{
document.getElementById('test-result').innerHTML=`<p style="color:#e30613">❌ Erro: ${d.error}</p>`;
}
}
</script>
</body>
</html>'''

# ================================================================================
# MAIN
# ================================================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print(" 🚀 CLARO TV+ UNIFIED V3 - Painel Admin Completo")
    print("="*70)
    print(f" 📡 Interface: https://0.0.0.0:{ServerConfig.PORTA}")
    print(f" ⚙️ Admin: https://0.0.0.0:{ServerConfig.PORTA}/admin")
    print(f" 📺 M3U: https://0.0.0.0:{ServerConfig.PORTA}/playlist.m3u")
    print(f" 🎬 Stream: https://0.0.0.0:{ServerConfig.PORTA}/live/<ID>.ts")
    print("="*70)
    print(f" Streamlink: {STREAMLINK_BIN}")
    print(f" FFmpeg: {FFMPEG_BIN}")
    print(f" Pywidevine: {'✅' if PYWIDEVINE_AVAILABLE else '❌'}")
    print("="*70 + "\n")
    
    if HYPERCORN_AVAILABLE and os.path.exists(ServerConfig.CERT_FILE) and os.path.exists(ServerConfig.KEY_FILE):
        config = HypercornConfig()
        config.bind = [f"0.0.0.0:{ServerConfig.PORTA}"]
        config.certfile = ServerConfig.CERT_FILE
        config.keyfile = ServerConfig.KEY_FILE
        print("🔒 HTTPS (Hypercorn)")
        asyncio.run(serve(app, config))
    else:
        print("⚠️ HTTP (sem SSL)")
        app.run(host='0.0.0.0', port=ServerConfig.PORTA, threaded=True)
