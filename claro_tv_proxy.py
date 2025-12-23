#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
🚀 CLARO TV+ UNIFIED V3 - COM PROXY ANTBANCLARO
================================================================================
- Integração com proxy para contornar bloqueio CloudFront
- Seleção de idioma, qualidade, legendas
- Configurações do Streamlink editáveis
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
    
    # ============================================
    # 🔥 PROXY ANTBANCLARO - CONTORNA BLOQUEIO
    # ============================================
    PROXY_ENABLED = True
    PROXY_URL = os.environ.get('PROXY_URL', 'http://200.218.236.2')
    
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
    "locale": "pt_BR",
    "audio_lang": "por",
    "hls_audio_select": "",
    "quality": "best",
    "max_quality": "",
    "mux_subtitles": False,
    "subtitle_lang": "",
    "ringbuffer_size": "16M",
    "stream_timeout": "20",
    "segment_threads": "4",
    "segment_timeout": "10",
    "ffmpeg_fout": "mpegts",
    "ffmpeg_video_transcode": "copy",
    "ffmpeg_audio_transcode": "aac",
    "ffmpeg_copyts": False,
    "ffmpeg_start_at_zero": True,
    "force": True,
    "http_timeout": "20",
    "retry_max": "3",
    "retry_streams": "1",
    # Proxy settings
    "proxy_enabled": True,
    "proxy_url": "http://200.218.236.2",
}

def load_stream_settings():
    if os.path.exists(ServerConfig.SETTINGS_FILE):
        try:
            with open(ServerConfig.SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
                return {**DEFAULT_STREAM_SETTINGS, **saved}
        except:
            pass
    return DEFAULT_STREAM_SETTINGS.copy()

def save_stream_settings(settings):
    try:
        with open(ServerConfig.SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
        return True
    except:
        return False

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

AVAILABLE_LOCALES = [
    {"code": "pt_BR", "name": "🇧🇷 Português (Brasil)"},
    {"code": "en_US", "name": "🇺🇸 English (US)"},
    {"code": "es_ES", "name": "🇪🇸 Español (España)"},
]

AVAILABLE_AUDIO_LANGS = [
    {"code": "por", "name": "🇧🇷 Português"},
    {"code": "eng", "name": "🇺🇸 English"},
    {"code": "spa", "name": "🇪🇸 Español"},
    {"code": "und", "name": "🌐 Original"},
]

AVAILABLE_QUALITIES = [
    {"code": "best", "name": "🏆 Melhor Disponível"},
    {"code": "1080p,best", "name": "📺 1080p (Full HD)"},
    {"code": "720p,best", "name": "📺 720p (HD)"},
    {"code": "480p,best", "name": "📺 480p (SD)"},
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

STREAMLINK_BIN = shutil.which("streamlink") or "/root/.local/bin/streamlink"
FFMPEG_BIN = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

# ================================================================================
# FUNÇÕES DO PROXY ANTBANCLARO
# ================================================================================

def apply_proxy_to_url(url):
    """
    🔥 Aplica o proxy AntBanClaro na URL para contornar bloqueio
    """
    if not url:
        return url
    
    # Verifica se proxy está habilitado
    proxy_enabled = STREAM_SETTINGS.get('proxy_enabled', ServerConfig.PROXY_ENABLED)
    proxy_url = STREAM_SETTINGS.get('proxy_url', ServerConfig.PROXY_URL)
    
    if proxy_enabled and proxy_url:
        # Remove trailing slash do proxy
        proxy_url = proxy_url.rstrip('/')
        # Retorna URL através do proxy
        return f"{proxy_url}/{url}"
    
    return url

def get_proxy_status():
    """Verifica status do proxy"""
    proxy_url = STREAM_SETTINGS.get('proxy_url', ServerConfig.PROXY_URL)
    try:
        r = requests.get(proxy_url, timeout=5)
        return {"online": True, "url": proxy_url}
    except:
        return {"online": False, "url": proxy_url}

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

def resolve_cdn_url(url):
    """
    🔥 MODIFICADO: Resolve URL usando proxy para contornar bloqueio
    """
    # Aplica proxy se habilitado
    return apply_proxy_to_url(url)

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
    try:
        result = subprocess.run([STREAMLINK_BIN, '--version'], capture_output=True, text=True, timeout=5)
        version = result.stdout.strip() if result.stdout else "Desconhecido"
        return {"available": True, "version": version, "path": STREAMLINK_BIN}
    except:
        return {"available": False, "version": "N/A", "path": STREAMLINK_BIN}

def get_ffmpeg_info():
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
    """
    🔥 MODIFICADO: Retorna URLs com proxy aplicado
    """
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
        
        # URL original (sem proxy) para DRM
        manifest_raw = d.get('manifest')
        
        # 🔥 URL com proxy para streaming
        manifest_proxied = apply_proxy_to_url(manifest_raw)
        
        return {
            'manifest': manifest_proxied,      # URL COM proxy (para streaming)
            'manifest_raw': manifest_raw,       # URL SEM proxy (para DRM)
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
    """Nota: Usa URL raw (sem proxy) para obter PSSH"""
    if not PYWIDEVINE_AVAILABLE or not os.path.exists(ServerConfig.DEVICE_FILE):
        return None
    try:
        # Usa URL raw para DRM
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
# STREAMER COM PROXY INTEGRADO
# ================================================================================

class ConfigurableStreamer:
    """
    🔥 Streamer com suporte a proxy AntBanClaro
    """
    
    def __init__(self, channel_id, override_settings=None):
        self.channel_id = channel_id
        self.settings = {**STREAM_SETTINGS, **(override_settings or {})}
        self.heartbeat = None
        self.streamlink_proc = None
        self.ffmpeg_proc = None
    
    def build_streamlink_command(self, mpd_url, spoof_ip, key=None):
        """
        🔥 MODIFICADO: Aplica proxy na URL do manifesto
        """
        s = self.settings
        cmd = [STREAMLINK_BIN]
        
        # 🔥 APLICA PROXY NA URL DO MANIFESTO
        proxy_enabled = s.get('proxy_enabled', True)
        proxy_url = s.get('proxy_url', ServerConfig.PROXY_URL)
        
        if proxy_enabled and proxy_url:
            proxy_url = proxy_url.rstrip('/')
            mpd_url = f"{proxy_url}/{mpd_url}"
            print(f"🔥 Proxy habilitado: {proxy_url}")
        
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
        
        # Qualidade máxima
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
        s = self.settings
        cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-i", "pipe:0"]
        
        cmd.extend(["-map", "0:v:0"])
        
        audio_lang = s.get('audio_lang', 'por')
        if audio_lang and audio_lang != 'und':
            cmd.extend(["-map", f"0:a:m:language:{audio_lang}?"])
        cmd.extend(["-map", "0:a:0?"])
        
        video_codec = s.get('ffmpeg_video_transcode', 'copy')
        cmd.extend(["-c:v", video_codec])
        
        audio_codec = s.get('ffmpeg_audio_transcode', 'aac')
        cmd.extend(["-c:a", audio_codec])
        if audio_codec != 'copy':
            cmd.extend(["-b:a", "192k", "-ac", "2"])
        
        if s.get('ffmpeg_copyts'):
            cmd.append("-copyts")
        if s.get('ffmpeg_start_at_zero'):
            cmd.append("-start_at_zero")
        
        cmd.extend(["-f", s.get('ffmpeg_fout', 'mpegts')])
        cmd.append("pipe:1")
        
        return cmd
    
    def start(self):
        spoof_ip = get_random_br_ip()
        
        # 🔥 Usa URL RAW para obter dados (DRM precisa da URL original)
        data, error = get_stream_data(self.channel_id, use_cookies=False, spoof_ip=spoof_ip)
        if error:
            print(f"❌ {error}")
            return None
        
        # Usa URL raw para DRM
        mpd_raw = data['manifest_raw']
        key = get_decryption_key(mpd_raw, data['license'], data['token'], data['session'])
        
        # Heartbeat
        self.heartbeat = YouboraHeartbeat(mpd_raw, self.channel_id, spoof_ip)
        threading.Thread(target=self.heartbeat.run, daemon=True).start()
        
        # 🔥 Comandos - Streamlink usará URL com proxy automaticamente
        streamlink_cmd = self.build_streamlink_command(mpd_raw, spoof_ip, key)
        ffmpeg_cmd = self.build_ffmpeg_command()
        
        print(f"🎬 Canal: {self.channel_id}")
        print(f"   Idioma: {self.settings.get('locale')} | Áudio: {self.settings.get('audio_lang')}")
        print(f"   Qualidade: {self.settings.get('quality')} | Buffer: {self.settings.get('ringbuffer_size')}")
        print(f"   🔥 Proxy: {self.settings.get('proxy_url') if self.settings.get('proxy_enabled') else 'Desabilitado'}")
        
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
            'proxy_status': get_proxy_status(),
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
        'settings_file': os.path.exists(ServerConfig.SETTINGS_FILE),
        'proxy': get_proxy_status()
    })

@app.route('/api/proxy/test')
def api_proxy_test():
    """Testa se o proxy está funcionando"""
    proxy_url = STREAM_SETTINGS.get('proxy_url', ServerConfig.PROXY_URL)
    test_url = "https://www.google.com"
    try:
        r = requests.get(f"{proxy_url}/{test_url}", timeout=10)
        return jsonify({
            'success': True,
            'proxy_url': proxy_url,
            'status_code': r.status_code
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'proxy_url': proxy_url,
            'error': str(e)
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
    data, error = get_stream_data(channel_id, use_cookies=False)
    if error:
        return jsonify({'success': False, 'error': error})
    return jsonify({
        'success': True,
        'manifest': data['manifest'][:100] + '...',
        'manifest_proxied': data['manifest'].startswith(ServerConfig.PROXY_URL),
        'has_license': bool(data['license']),
        'has_token': bool(data['token'])
    })

# ================================================================================
# ROTAS DE STREAMING
# ================================================================================

@app.route('/live/<channel_id>')
@app.route('/live/<channel_id>.ts')
def live_stream(channel_id):
    override = {}
    if request.args.get('lang'):
        override['audio_lang'] = request.args.get('lang')
    if request.args.get('locale'):
        override['locale'] = request.args.get('locale')
    if request.args.get('quality'):
        override['quality'] = request.args.get('quality')
    if request.args.get('proxy') == '0':
        override['proxy_enabled'] = False
    
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
    proxy_status = get_proxy_status()
    return render_template_string(HTML_HOME, visits=VISITOR_COUNT, online=get_online_count(), 
                                  proxy_online=proxy_status['online'], proxy_url=proxy_status['url'])

@app.route('/watch/<channel_id>')
def watch(channel_id):
    ONLINE_USERS[request.remote_addr] = time.time()
    return render_template_string(HTML_PLAYER, channel_id=channel_id)

@app.route('/admin')
def admin():
    return render_template_string(HTML_ADMIN)

# ================================================================================
# TEMPLATES HTML (Resumidos por espaço)
# ================================================================================

HTML_HOME = '''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claro TV+ V3</title>
<style>
:root{--p:#e30613;--bg:#0a0a0a;--card:#141414;--border:#222}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:#fff;font-family:sans-serif;min-height:100vh}
.navbar{background:#111;padding:15px 20px;display:flex;justify-content:space-between;align-items:center}
.logo{font-size:24px;font-weight:800;color:var(--p)}
.nav-links a{color:#888;text-decoration:none;padding:8px 16px;margin-left:10px}
.nav-links a:hover{background:var(--p);color:#fff;border-radius:8px}
.stats-bar{background:#111;padding:10px 20px;display:flex;gap:30px;font-size:13px;color:#666}
.stat-value{color:#fff;font-weight:600}
.proxy-status{padding:3px 10px;border-radius:12px;font-size:11px;margin-left:10px}
.proxy-online{background:#0f02;color:#0f0;border:1px solid #0f0}
.proxy-offline{background:#f002;color:#f00;border:1px solid #f00}
.container{max-width:1400px;margin:0 auto;padding:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:15px}
.card{background:var(--card);border-radius:12px;overflow:hidden;cursor:pointer;border:2px solid transparent;transition:.3s}
.card:hover{transform:translateY(-5px);border-color:var(--p)}
.card-img{width:100%;height:100px;object-fit:contain;background:#000;padding:15px}
.card-body{padding:15px}
.card-title{font-weight:700;font-size:14px}
</style>
</head>
<body>
<nav class="navbar">
<div class="logo">CLARO TV+ <span style="font-size:12px;color:#666">V3 + PROXY</span></div>
<div class="nav-links">
<a href="/">📺 Canais</a>
<a href="/admin">⚙️ Admin</a>
<a href="/playlist.m3u">📋 M3U</a>
</div>
</nav>
<div class="stats-bar">
<div>👁️ Visitas: <span class="stat-value">{{visits}}</span></div>
<div>👤 Online: <span class="stat-value">{{online}}</span></div>
<div>🔥 Proxy: <span class="proxy-status {% if proxy_online %}proxy-online{% else %}proxy-offline{% endif %}">{% if proxy_online %}ONLINE{% else %}OFFLINE{% endif %}</span></div>
</div>
<div class="container">
<div class="grid" id="grid"></div>
</div>
<script>
fetch('/api/channels').then(r=>r.json()).then(channels=>{
document.getElementById('grid').innerHTML=channels.filter(c=>c.id&&c.id!='0')
.map(c=>'<div class="card" onclick="location.href=\\'/watch/'+c.id+'\\'"><img src="'+c.logo+'" class="card-img"><div class="card-body"><div class="card-title">'+c.title+'</div></div></div>').join('');
});
</script>
</body>
</html>'''

HTML_PLAYER = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Player</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.3.5/shaka-player.ui.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/shaka-player/4.3.5/controls.min.css">
<style>body{background:#000;margin:0;height:100vh}#container{width:100%;height:100%}video{width:100%;height:100%}</style>
</head><body>
<div id="container" data-shaka-player-container><video id="video" data-shaka-player autoplay></video></div>
<script>
const channelId="{{channel_id}}";
async function init(){
shaka.polyfill.installAll();
const player=new shaka.Player(document.getElementById('video'));
new shaka.ui.Overlay(player,document.getElementById('container'),document.getElementById('video'));
player.getNetworkingEngine().registerRequestFilter((type,req)=>{
if(type==shaka.net.NetworkingEngine.RequestType.LICENSE)req.headers['Authorization']=window.drmToken;
if(!req.uris[0].includes('/proxy?url='))req.uris=[location.origin+'/proxy?url='+encodeURIComponent(req.uris[0])];
});
const r=await fetch('/api/stream/'+channelId);
const d=await r.json();
window.drmToken=d.token;
player.configure({drm:{servers:{'com.widevine.alpha':d.license}}});
await player.load(d.manifest);
}
document.addEventListener('DOMContentLoaded',init);
</script>
</body></html>'''

HTML_ADMIN = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Admin</title>
<style>
:root{--p:#e30613;--bg:#0a0a0a;--card:#141414}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:#fff;font-family:sans-serif;padding:20px}
.card{background:var(--card);border-radius:16px;padding:25px;margin-bottom:20px}
.card-title{font-size:18px;font-weight:700;margin-bottom:20px}
.form-group{margin-bottom:15px}
.form-label{display:block;font-size:13px;color:#888;margin-bottom:8px}
.form-input,.form-select{width:100%;padding:12px;border-radius:10px;border:1px solid #333;background:#111;color:#fff}
.form-check{display:flex;align-items:center;gap:10px}
.form-check input{width:20px;height:20px;accent-color:var(--p)}
.btn{padding:12px 24px;border-radius:10px;border:none;font-weight:600;cursor:pointer;margin-right:10px}
.btn-primary{background:var(--p);color:#fff}
.btn-secondary{background:#333;color:#fff}
.status-ok{color:#0f0}.status-error{color:#f00}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:20px}
</style>
</head><body>
<h1 style="color:#e30613;margin-bottom:20px">⚙️ ADMIN - Claro TV+ V3</h1>

<div class="card">
<div class="card-title">🔥 Configuração do Proxy AntBanClaro</div>
<div class="grid-2">
<div class="form-group">
<label class="form-check">
<input type="checkbox" id="proxy-enabled" checked>
<span>Habilitar Proxy (contorna bloqueio)</span>
</label>
</div>
<div class="form-group">
<label class="form-label">URL DO PROXY</label>
<input type="text" class="form-input" id="proxy-url" value="http://200.218.236.2">
</div>
</div>
<button class="btn btn-secondary" onclick="testProxy()">🧪 Testar Proxy</button>
<span id="proxy-test-result"></span>
</div>

<div class="card">
<div class="card-title">🌍 Idioma e Qualidade</div>
<div class="grid-2">
<div class="form-group">
<label class="form-label">IDIOMA DO ÁUDIO</label>
<select class="form-select" id="audio-lang">
<option value="por">🇧🇷 Português</option>
<option value="eng">🇺🇸 English</option>
<option value="spa">🇪🇸 Español</option>
</select>
</div>
<div class="form-group">
<label class="form-label">QUALIDADE</label>
<select class="form-select" id="quality">
<option value="best">🏆 Melhor</option>
<option value="1080p,best">📺 1080p</option>
<option value="720p,best">📺 720p</option>
</select>
</div>
</div>
</div>

<div class="card">
<div class="card-title">⚡ Buffer e Performance</div>
<div class="grid-2">
<div class="form-group">
<label class="form-label">TAMANHO DO BUFFER</label>
<select class="form-select" id="buffer">
<option value="16M">16 MB (Padrão)</option>
<option value="32M">32 MB (Estável)</option>
<option value="64M">64 MB (Máximo)</option>
</select>
</div>
</div>
</div>

<div class="card">
<div class="card-title">💻 Status do Sistema</div>
<div id="system-status">Carregando...</div>
</div>

<button class="btn btn-primary" onclick="saveSettings()">💾 Salvar Configurações</button>
<button class="btn btn-secondary" onclick="loadSettings()">🔄 Recarregar</button>

<script>
async function loadSettings(){
const r=await fetch('/api/settings');
const d=await r.json();
document.getElementById('proxy-enabled').checked=d.settings.proxy_enabled!==false;
document.getElementById('proxy-url').value=d.settings.proxy_url||'http://200.218.236.2';
document.getElementById('audio-lang').value=d.settings.audio_lang||'por';
document.getElementById('quality').value=d.settings.quality||'best';
document.getElementById('buffer').value=d.settings.ringbuffer_size||'16M';
}

async function saveSettings(){
const settings={
proxy_enabled:document.getElementById('proxy-enabled').checked,
proxy_url:document.getElementById('proxy-url').value,
audio_lang:document.getElementById('audio-lang').value,
quality:document.getElementById('quality').value,
ringbuffer_size:document.getElementById('buffer').value
};
await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(settings)});
alert('✅ Configurações salvas!');
}

async function testProxy(){
document.getElementById('proxy-test-result').innerHTML='Testando...';
const r=await fetch('/api/proxy/test');
const d=await r.json();
document.getElementById('proxy-test-result').innerHTML=d.success?'<span class="status-ok">✅ Proxy OK!</span>':'<span class="status-error">❌ '+d.error+'</span>';
}

async function loadSystemInfo(){
const r=await fetch('/api/system-info');
const d=await r.json();
let html='<p>Streamlink: '+(d.streamlink.available?'<span class="status-ok">✅</span>':'<span class="status-error">❌</span>')+'</p>';
html+='<p>FFmpeg: '+(d.ffmpeg.available?'<span class="status-ok">✅</span>':'<span class="status-error">❌</span>')+'</p>';
html+='<p>Pywidevine: '+(d.pywidevine?'<span class="status-ok">✅</span>':'<span class="status-error">❌</span>')+'</p>';
html+='<p>Proxy: '+(d.proxy.online?'<span class="status-ok">✅ '+d.proxy.url+'</span>':'<span class="status-error">❌ Offline</span>')+'</p>';
document.getElementById('system-status').innerHTML=html;
}

document.addEventListener('DOMContentLoaded',()=>{loadSettings();loadSystemInfo();});
</script>
</body></html>'''

# ================================================================================
# MAIN
# ================================================================================

if __name__ == '__main__':
    proxy_status = get_proxy_status()
    
    print("\n" + "="*70)
    print(" 🚀 CLARO TV+ UNIFIED V3 - COM PROXY ANTBANCLARO")
    print("="*70)
    print(f" 📡 Interface: https://0.0.0.0:{ServerConfig.PORTA}")
    print(f" ⚙️ Admin: https://0.0.0.0:{ServerConfig.PORTA}/admin")
    print(f" 📺 M3U: https://0.0.0.0:{ServerConfig.PORTA}/playlist.m3u")
    print(f" 🎬 Stream: https://0.0.0.0:{ServerConfig.PORTA}/live/<ID>.ts")
    print("="*70)
    print(f" 🔥 Proxy: {ServerConfig.PROXY_URL} {'✅ ONLINE' if proxy_status['online'] else '❌ OFFLINE'}")
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
