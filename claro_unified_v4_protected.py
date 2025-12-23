#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
🛡️ CLARO TV+ UNIFIED V4 - COM PROTEÇÃO ANTI-BLOQUEIO
================================================================================
Versão com:
- Rate limiting inteligente
- Detecção de bloqueio
- Limite de streams simultâneos
- Cooldown automático por canal
- Retry com backoff exponencial
- Suporte a proxy rotativo (opcional)
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
from functools import lru_cache, wraps
from collections import defaultdict

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

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('hypercorn.access').setLevel(logging.ERROR)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'claro_unified_v4_protected')

# ================================================================================
# 🛡️ CONFIGURAÇÕES DE PROTEÇÃO ANTI-BLOQUEIO
# ================================================================================

class ProtectionConfig:
    """Configurações de proteção contra bloqueio"""
    
    # === LIMITES DE STREAM ===
    MAX_CONCURRENT_STREAMS = int(os.environ.get('MAX_STREAMS', 50))  # Limite de streams simultâneos
    
    # === RATE LIMITING ===
    MAX_REQUESTS_PER_SECOND = 50  # Requisições por segundo (global)
    MAX_REQUESTS_PER_MINUTE_CLOUDFRONT = 2000  # Limite CloudFront por minuto
    
    # === DELAYS ===
    MIN_DELAY_BETWEEN_REQUESTS_MS = 50  # Delay mínimo entre requisições (ms)
    JITTER_MAX_MS = 30  # Variação aleatória adicional (ms)
    STREAM_START_DELAY_MS = 200  # Delay ao iniciar novo stream
    
    # === COOLDOWN POR ERROS ===
    COOLDOWN_ON_403 = 60  # Segundos de cooldown após erro 403
    COOLDOWN_ON_429 = 120  # Segundos de cooldown após erro 429
    MAX_ERRORS_BEFORE_BLOCK = 3  # Erros antes de bloquear canal
    
    # === RETRY ===
    MAX_RETRIES = 3
    RETRY_BACKOFF = 1.5  # Multiplicador de backoff
    
    # === PROXIES (opcional) ===
    # Formato: ["socks5://ip:port", "http://user:pass@ip:port", ...]
    PROXY_LIST = json.loads(os.environ.get('PROXY_LIST', '[]'))
    ROTATE_PROXY_EVERY_N_REQUESTS = 100


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
    
    USERNAME = os.environ.get('CLARO_USER', 'nocbrasil')
    PASSWORD = os.environ.get('CLARO_PASS', 'n0cbr4s1l')
    
    API_BASE = "https://androidtv-mediation-layer.clarobrasil.mobi"
    URL_CATALOGO = f"{API_BASE}/api/v1/catalog/content_providers"
    URL_PLAYBACK = f"{API_BASE}/api/entitlement/playback"
    URL_AUTH_TOKEN = f"{API_BASE}/api/v1/auth/token"
    URL_AUTH_LOGIN = f"{API_BASE}/api/auth/login"
    
    YOUBORA_HOST = "https://infinity-c35.youboranqs01.com"
    YOUBORA_ACCOUNT = "clarobrasildev"
    
    ONLINE_TIMEOUT = 300


# ================================================================================
# 🛡️ SISTEMA DE PROTEÇÃO
# ================================================================================

class RateLimiter:
    """Rate limiter com token bucket"""
    
    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_time = time.time()
        self.lock = threading.Lock()
    
    def acquire(self, tokens: int = 1) -> float:
        with self.lock:
            now = time.time()
            elapsed = now - self.last_time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_time = now
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0
            return (tokens - self.tokens) / self.rate
    
    def wait(self, tokens: int = 1):
        wait_time = self.acquire(tokens)
        if wait_time > 0:
            time.sleep(wait_time)


class StreamProtector:
    """Gerenciador de proteção para streams"""
    
    def __init__(self):
        self.active_streams = {}
        self.channel_errors = defaultdict(int)
        self.channel_cooldown = {}
        self.domain_requests = defaultdict(lambda: {"count": 0, "reset": 0})
        self.lock = threading.Lock()
        
        # Rate limiters
        self.global_limiter = RateLimiter(
            rate=ProtectionConfig.MAX_REQUESTS_PER_SECOND,
            capacity=ProtectionConfig.MAX_REQUESTS_PER_SECOND * 2
        )
        
        # Métricas
        self.total_requests = 0
        self.total_errors = 0
        self.blocked_requests = 0
        
        # Proxy rotation
        self.proxies = ProtectionConfig.PROXY_LIST.copy()
        self.proxy_index = 0
        self.proxy_request_count = 0
        self.failed_proxies = set()
    
    def can_start_stream(self, channel_id: str) -> tuple:
        """Verifica se pode iniciar stream"""
        with self.lock:
            # Limite de streams
            if len(self.active_streams) >= ProtectionConfig.MAX_CONCURRENT_STREAMS:
                return False, f"Limite de {ProtectionConfig.MAX_CONCURRENT_STREAMS} streams atingido"
            
            # Cooldown
            if channel_id in self.channel_cooldown:
                if time.time() < self.channel_cooldown[channel_id]:
                    remaining = int(self.channel_cooldown[channel_id] - time.time())
                    return False, f"Canal em cooldown por {remaining}s (erro anterior)"
                del self.channel_cooldown[channel_id]
            
            # Muitos erros
            if self.channel_errors.get(channel_id, 0) >= ProtectionConfig.MAX_ERRORS_BEFORE_BLOCK:
                return False, "Canal bloqueado por erros consecutivos"
            
            return True, "OK"
    
    def start_stream(self, channel_id: str, info: dict = None):
        """Registra início de stream"""
        with self.lock:
            self.active_streams[channel_id] = {
                "start": time.time(),
                "info": info or {},
                "requests": 0
            }
            self.channel_errors[channel_id] = 0  # Reset erros
            logger.info(f"📺 Stream iniciado: {channel_id} (Total: {len(self.active_streams)})")
    
    def stop_stream(self, channel_id: str):
        """Registra fim de stream"""
        with self.lock:
            if channel_id in self.active_streams:
                duration = time.time() - self.active_streams[channel_id]["start"]
                del self.active_streams[channel_id]
                logger.info(f"📴 Stream parado: {channel_id} (Duração: {duration:.0f}s, Restantes: {len(self.active_streams)})")
    
    def register_error(self, channel_id: str, error_code: int):
        """Registra erro e aplica cooldown se necessário"""
        with self.lock:
            self.total_errors += 1
            self.channel_errors[channel_id] += 1
            
            if error_code == 403:
                cooldown = ProtectionConfig.COOLDOWN_ON_403 * self.channel_errors[channel_id]
                self.channel_cooldown[channel_id] = time.time() + cooldown
                logger.warning(f"⚠️ 403 no canal {channel_id} - Cooldown {cooldown}s")
            
            elif error_code == 429:
                cooldown = ProtectionConfig.COOLDOWN_ON_429 * self.channel_errors[channel_id]
                self.channel_cooldown[channel_id] = time.time() + cooldown
                logger.warning(f"⚠️ 429 no canal {channel_id} - Cooldown {cooldown}s")
    
    def register_success(self, channel_id: str):
        """Registra sucesso"""
        with self.lock:
            if self.channel_errors.get(channel_id, 0) > 0:
                self.channel_errors[channel_id] -= 1
    
    def wait_rate_limit(self):
        """Aguarda rate limit global"""
        self.global_limiter.wait()
        
        # Delay adicional com jitter
        delay = ProtectionConfig.MIN_DELAY_BETWEEN_REQUESTS_MS / 1000
        jitter = random.uniform(0, ProtectionConfig.JITTER_MAX_MS / 1000)
        time.sleep(delay + jitter)
    
    def check_domain_limit(self, domain: str) -> bool:
        """Verifica limite por domínio (CloudFront)"""
        with self.lock:
            now = time.time()
            info = self.domain_requests[domain]
            
            if now >= info["reset"]:
                info["count"] = 0
                info["reset"] = now + 60
            
            if info["count"] >= ProtectionConfig.MAX_REQUESTS_PER_MINUTE_CLOUDFRONT:
                self.blocked_requests += 1
                return False
            
            info["count"] += 1
            self.total_requests += 1
            return True
    
    def get_proxy(self) -> str:
        """Retorna próximo proxy (se configurado)"""
        if not self.proxies:
            return None
        
        with self.lock:
            # Rotacionar proxy a cada N requisições
            self.proxy_request_count += 1
            if self.proxy_request_count >= ProtectionConfig.ROTATE_PROXY_EVERY_N_REQUESTS:
                self.proxy_request_count = 0
                self.proxy_index = (self.proxy_index + 1) % len(self.proxies)
            
            # Obter proxy não-falhado
            available = [p for p in self.proxies if p not in self.failed_proxies]
            if not available:
                self.failed_proxies.clear()
                available = self.proxies
            
            return available[self.proxy_index % len(available)] if available else None
    
    def mark_proxy_failed(self, proxy: str):
        """Marca proxy como falhado"""
        with self.lock:
            self.failed_proxies.add(proxy)
            logger.warning(f"🔴 Proxy falhou: {proxy}")
    
    def get_stats(self) -> dict:
        """Retorna estatísticas de proteção"""
        with self.lock:
            return {
                "active_streams": len(self.active_streams),
                "max_streams": ProtectionConfig.MAX_CONCURRENT_STREAMS,
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "blocked_requests": self.blocked_requests,
                "channels_in_cooldown": len(self.channel_cooldown),
                "channels_with_errors": sum(1 for e in self.channel_errors.values() if e > 0),
                "proxies_configured": len(self.proxies),
                "proxies_failed": len(self.failed_proxies),
                "streams_list": list(self.active_streams.keys())
            }


# Instância global do protetor
protector = StreamProtector()


# ================================================================================
# DECORADORES DE PROTEÇÃO
# ================================================================================

def protected_request(func):
    """Decorator para requisições protegidas"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        protector.wait_rate_limit()
        return func(*args, **kwargs)
    return wrapper


def with_retry_backoff(max_retries=None, backoff=None):
    """Decorator para retry com backoff exponencial"""
    max_r = max_retries or ProtectionConfig.MAX_RETRIES
    back = backoff or ProtectionConfig.RETRY_BACKOFF
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_r):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    wait = back ** attempt + random.uniform(0, 1)
                    logger.warning(f"Retry {attempt + 1}/{max_r}: {e}. Aguardando {wait:.1f}s")
                    time.sleep(wait)
            raise last_error
        return wrapper
    return decorator


# ================================================================================
# CONFIGURAÇÕES DE STREAM
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
}

def load_stream_settings():
    if os.path.exists(ServerConfig.SETTINGS_FILE):
        try:
            with open(ServerConfig.SETTINGS_FILE, 'r') as f:
                return {**DEFAULT_STREAM_SETTINGS, **json.load(f)}
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
    {"code": "es_MX", "name": "🇲🇽 Español (México)"},
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
]

AVAILABLE_BUFFER_SIZES = [
    {"code": "8M", "name": "8 MB (Baixa latência)"},
    {"code": "16M", "name": "16 MB (Padrão)"},
    {"code": "32M", "name": "32 MB (Estável)"},
    {"code": "64M", "name": "64 MB (Máximo)"},
]

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
# API CLARO (COM PROTEÇÃO)
# ================================================================================

@protected_request
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

@with_retry_backoff()
def get_stream_data(channel_id, use_cookies=True, spoof_ip=None):
    session = requests.Session()
    spoof_ip = spoof_ip or get_random_br_ip()
    session.headers.update(get_stealth_headers(spoof_ip))
    
    # Adicionar proxy se disponível
    proxy = protector.get_proxy()
    if proxy:
        session.proxies = {'http': proxy, 'https': proxy}
    
    if use_cookies:
        cookies = load_cookies()
        if not cookies: return None, "Cookies não encontrados"
        session.cookies.update(cookies)
    else:
        try:
            protector.wait_rate_limit()
            resp = session.post(ServerConfig.URL_AUTH_LOGIN, 
                              json={"username": ServerConfig.USERNAME, "password": ServerConfig.PASSWORD}, timeout=10)
            if not resp.json().get('success'): return None, "Falha no login"
        except Exception as e:
            if proxy:
                protector.mark_proxy_failed(proxy)
            return None, str(e)
    
    payload = {
        "channel_id": str(channel_id), "type": "TV",
        "city": "Sao Paulo", "state": "Sao Paulo",
        "drm_type": "widevine", "drm_provider": "verimatrix"
    }
    
    try:
        protector.wait_rate_limit()
        resp = session.post(ServerConfig.URL_PLAYBACK, json=payload, verify=False, timeout=10)
        data = resp.json()
        
        if resp.status_code in [403, 429]:
            protector.register_error(channel_id, resp.status_code)
            if proxy:
                protector.mark_proxy_failed(proxy)
            return None, f"Erro {resp.status_code}: Rate limited ou bloqueado"
        
        if not data.get('success'): 
            return None, f"API Error: {data.get('err', {}).get('code')}"
        
        d = data.get('data', {})
        save_cdn_cookies(session.cookies)
        protector.register_success(channel_id)
        
        return {
            'manifest': resolve_cdn_url(d.get('manifest')),
            'manifest_raw': d.get('manifest'),
            'license': d.get('license_url') or 'https://multidrm.core.verimatrixcloud.net/widevine',
            'token': d.get('private_data'),
            'session': session,
            'spoof_ip': spoof_ip
        }, None
    except Exception as e:
        if proxy:
            protector.mark_proxy_failed(proxy)
        return None, str(e)


# ================================================================================
# DRM - PYWIDEVINE
# ================================================================================

@with_retry_backoff()
def get_decryption_key(mpd_url, license_url, auth_token, session):
    if not PYWIDEVINE_AVAILABLE or not os.path.exists(ServerConfig.DEVICE_FILE):
        return None
    try:
        protector.wait_rate_limit()
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
        
        protector.wait_rate_limit()
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
        logger.error(f"❌ DRM: {e}")
    return None


# ================================================================================
# YOUBORA HEARTBEAT
# ================================================================================

class YouboraHeartbeat:
    def __init__(self, url, channel_id, ip):
        self.url, self.channel_id, self.ip = url, channel_id, ip
        self.running = False
        self.code = f"V4_{''.join(random.choices(string.ascii_lowercase+string.digits, k=10))}"
    
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
# STREAMER PROTEGIDO
# ================================================================================

class ProtectedStreamer:
    """Streamer com proteções integradas"""
    
    def __init__(self, channel_id, override_settings=None):
        self.channel_id = channel_id
        self.settings = {**STREAM_SETTINGS, **(override_settings or {})}
        self.heartbeat = None
        self.streamlink_proc = None
        self.ffmpeg_proc = None
        self.stream_id = str(uuid.uuid4())[:8]
    
    def build_streamlink_command(self, mpd_url, spoof_ip, key=None):
        s = self.settings
        cmd = [STREAMLINK_BIN]
        
        # Headers HTTP
        cmd.extend(["--http-header", f"User-Agent={HEADERS_TIZEN['User-Agent']}"])
        cmd.extend(["--http-header", "Referer=https://development.3ss.tv/"])
        cmd.extend(["--http-header", f"X-Forwarded-For={spoof_ip}"])
        cmd.extend(["--http-header", f"X-Real-IP={spoof_ip}"])
        
        if s.get('locale'):
            cmd.extend(["--locale", s['locale']])
        if s.get('hls_audio_select'):
            cmd.extend(["--hls-audio-select", s['hls_audio_select']])
        if s.get('max_quality'):
            cmd.extend(["--stream-sorting-excludes", s['max_quality']])
        if s.get('mux_subtitles'):
            cmd.append("--mux-subtitles")
        
        cmd.extend(["--ringbuffer-size", s.get('ringbuffer_size', '16M')])
        cmd.extend(["--stream-timeout", s.get('stream_timeout', '20')])
        cmd.extend(["--stream-segment-threads", s.get('segment_threads', '4')])
        cmd.extend(["--stream-segment-timeout", s.get('segment_timeout', '10')])
        cmd.extend(["--http-timeout", s.get('http_timeout', '20')])
        cmd.extend(["--retry-max", s.get('retry_max', '3')])
        cmd.extend(["--retry-streams", s.get('retry_streams', '1')])
        
        if s.get('force', True):
            cmd.append("--force")
        
        cmd.append(mpd_url)
        cmd.append(s.get('quality', 'best'))
        cmd.append("--stdout")
        
        if key:
            key_val = key.split(":")[1] if ":" in key else key
            cmd.extend(["-decryption_key", key_val])
        
        return cmd
    
    def build_ffmpeg_command(self):
        s = self.settings
        cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-i", "pipe:0"]
        
        cmd.extend(["-map", "0:v:0"])
        
        audio_lang = s.get('audio_lang', 'por')
        if audio_lang and audio_lang != 'und':
            cmd.extend(["-map", f"0:a:m:language:{audio_lang}?"])
        cmd.extend(["-map", "0:a:0?"])
        
        cmd.extend(["-c:v", s.get('ffmpeg_video_transcode', 'copy')])
        
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
    
    def stream(self):
        """Inicia stream com proteções"""
        
        # Verificar se pode iniciar
        can_start, reason = protector.can_start_stream(self.channel_id)
        if not can_start:
            logger.warning(f"🚫 Bloqueado canal {self.channel_id}: {reason}")
            yield f"# Erro: {reason}\n".encode()
            return
        
        # Delay inicial (evita burst)
        time.sleep(ProtectionConfig.STREAM_START_DELAY_MS / 1000)
        
        # Obter dados do stream
        data, err = get_stream_data(self.channel_id, use_cookies=False)
        if err:
            logger.error(f"❌ Erro ao obter dados: {err}")
            yield f"# Erro: {err}\n".encode()
            return
        
        mpd_url = data['manifest']
        spoof_ip = data['spoof_ip']
        
        # Obter chave DRM
        key = get_decryption_key(mpd_url, data['license'], data['token'], data['session'])
        if not key:
            logger.warning(f"⚠️ Chave DRM não obtida para canal {self.channel_id}")
        
        # Registrar stream
        protector.start_stream(self.channel_id, {
            "stream_id": self.stream_id,
            "spoof_ip": spoof_ip
        })
        
        # Heartbeat
        self.heartbeat = YouboraHeartbeat(mpd_url, self.channel_id, spoof_ip)
        threading.Thread(target=self.heartbeat.run, daemon=True).start()
        
        try:
            streamlink_cmd = self.build_streamlink_command(mpd_url, spoof_ip, key)
            ffmpeg_cmd = self.build_ffmpeg_command()
            
            logger.info(f"🎬 Iniciando stream {self.channel_id} [{self.stream_id}]")
            
            self.streamlink_proc = subprocess.Popen(
                streamlink_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            self.ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=self.streamlink_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            while True:
                data = self.ffmpeg_proc.stdout.read(65536)
                if not data:
                    break
                yield data
                
        except Exception as e:
            logger.error(f"❌ Erro no stream {self.channel_id}: {e}")
            protector.register_error(self.channel_id, 500)
        finally:
            self.stop()
    
    def stop(self):
        if self.heartbeat:
            self.heartbeat.stop()
        if self.streamlink_proc:
            self.streamlink_proc.terminate()
        if self.ffmpeg_proc:
            self.ffmpeg_proc.terminate()
        protector.stop_stream(self.channel_id)


# ================================================================================
# ROTAS
# ================================================================================

@app.route('/')
def home():
    global VISITOR_COUNT
    VISITOR_COUNT += 1
    ONLINE_USERS[request.remote_addr] = time.time()
    return render_template_string(HTML_HOME)

@app.route('/watch/<live_id>')
def watch(live_id):
    ONLINE_USERS[request.remote_addr] = time.time()
    return render_template_string(HTML_PLAYER, live_id=live_id)

@app.route('/admin')
def admin():
    return render_template_string(HTML_ADMIN)

@app.route('/api/channels')
def api_channels():
    return jsonify({"channels": fetch_channels()})

@app.route('/api/stats')
def api_stats():
    protection_stats = protector.get_stats()
    return jsonify({
        "visitors": VISITOR_COUNT,
        "online": get_online_count(),
        "active_streams": protection_stats["active_streams"],
        "max_streams": protection_stats["max_streams"],
        "protection": protection_stats
    })

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    global STREAM_SETTINGS
    if request.method == 'POST':
        data = request.get_json()
        STREAM_SETTINGS = {**DEFAULT_STREAM_SETTINGS, **data}
        save_stream_settings(STREAM_SETTINGS)
        return jsonify({"success": True, "settings": STREAM_SETTINGS})
    return jsonify({"success": True, "settings": STREAM_SETTINGS})

@app.route('/api/protection')
def api_protection():
    """Retorna status da proteção anti-bloqueio"""
    return jsonify({
        "success": True,
        "protection": protector.get_stats(),
        "config": {
            "max_concurrent_streams": ProtectionConfig.MAX_CONCURRENT_STREAMS,
            "max_requests_per_second": ProtectionConfig.MAX_REQUESTS_PER_SECOND,
            "cooldown_on_403": ProtectionConfig.COOLDOWN_ON_403,
            "cooldown_on_429": ProtectionConfig.COOLDOWN_ON_429,
            "max_errors_before_block": ProtectionConfig.MAX_ERRORS_BEFORE_BLOCK,
        }
    })

@app.route('/api/system-info')
def api_system_info():
    return jsonify({
        "success": True,
        "streamlink": get_streamlink_info(),
        "ffmpeg": get_ffmpeg_info(),
        "pywidevine": {"available": PYWIDEVINE_AVAILABLE, "device_exists": os.path.exists(ServerConfig.DEVICE_FILE)},
        "protection": protector.get_stats()
    })

@app.route('/live/<channel_id>')
@app.route('/live/<channel_id>.ts')
def live_stream(channel_id):
    channel_id = channel_id.replace('.ts', '')
    audio_lang = request.args.get('lang', STREAM_SETTINGS.get('audio_lang', 'por'))
    
    # Verificar se pode iniciar
    can_start, reason = protector.can_start_stream(channel_id)
    if not can_start:
        return jsonify({"error": reason}), 429
    
    streamer = ProtectedStreamer(channel_id, {'audio_lang': audio_lang})
    
    return Response(
        stream_with_context(streamer.stream()),
        mimetype='video/mp2t',
        headers={
            'Cache-Control': 'no-cache',
            'X-Stream-ID': streamer.stream_id,
            'X-Accel-Buffering': 'no'
        }
    )

@app.route('/playlist.m3u')
def playlist():
    channels = fetch_channels()
    host = request.host
    lang = request.args.get('lang', 'por')
    scheme = 'https' if request.is_secure else 'http'
    
    m3u = '#EXTM3U\n'
    for ch in channels:
        m3u += f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-logo="{ch["logo"]}" group-title="{ch["category"]}",{ch["title"]}\n'
        m3u += f'{scheme}://{host}/live/{ch["id"]}.ts?lang={lang}\n'
    
    return Response(m3u, mimetype='audio/x-mpegurl')


# ================================================================================
# HTML TEMPLATES (SIMPLIFICADO)
# ================================================================================

HTML_HOME = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Claro TV+ V4 Protected</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: system-ui, sans-serif; background: #0a0a0a; color: #fff; min-height: 100vh; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .header { background: linear-gradient(135deg, #e31937, #b01030); padding: 20px; border-radius: 16px; margin-bottom: 20px; }
        .header h1 { font-size: 24px; display: flex; align-items: center; gap: 10px; }
        .protection-bar { background: #1a1a1a; padding: 10px 15px; border-radius: 8px; margin-top: 10px; font-size: 13px; display: flex; gap: 20px; }
        .protection-bar span { color: #4ade80; }
        .stats { display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px; }
        .stat { background: #1a1a1a; padding: 15px 20px; border-radius: 10px; }
        .stat-value { font-size: 24px; font-weight: bold; color: #e31937; }
        .stat-label { font-size: 12px; color: #888; }
        .channels-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 15px; }
        .channel { background: #1a1a1a; border-radius: 12px; padding: 15px; cursor: pointer; transition: all 0.2s; }
        .channel:hover { transform: translateY(-2px); background: #252525; }
        .channel-title { font-weight: 600; margin-bottom: 5px; }
        .channel-program { font-size: 12px; color: #888; }
        .links { margin-top: 20px; display: flex; gap: 10px; }
        .links a { color: #e31937; text-decoration: none; padding: 10px 20px; border: 1px solid #e31937; border-radius: 8px; }
        .links a:hover { background: #e31937; color: #fff; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🛡️ Claro TV+ V4 Protected</h1>
            <div class="protection-bar" id="protection">Carregando proteção...</div>
        </div>
        
        <div class="stats" id="stats"></div>
        
        <div class="channels-grid" id="channels">Carregando canais...</div>
        
        <div class="links">
            <a href="/admin">⚙️ Admin</a>
            <a href="/playlist.m3u">📋 Playlist M3U</a>
            <a href="/api/protection">🛡️ Status Proteção</a>
        </div>
    </div>
    
    <script>
        async function load() {
            const [statsRes, channelsRes, protRes] = await Promise.all([
                fetch('/api/stats'),
                fetch('/api/channels'),
                fetch('/api/protection')
            ]);
            
            const stats = await statsRes.json();
            const channels = await channelsRes.json();
            const prot = await protRes.json();
            
            document.getElementById('stats').innerHTML = `
                <div class="stat"><div class="stat-value">${stats.visitors}</div><div class="stat-label">Visitantes</div></div>
                <div class="stat"><div class="stat-value">${stats.online}</div><div class="stat-label">Online</div></div>
                <div class="stat"><div class="stat-value">${stats.active_streams}/${stats.max_streams}</div><div class="stat-label">Streams Ativos</div></div>
            `;
            
            document.getElementById('protection').innerHTML = `
                <span>🛡️ Limite: ${prot.config.max_concurrent_streams} streams</span>
                <span>⚡ Rate: ${prot.config.max_requests_per_second} req/s</span>
                <span>📊 Total: ${prot.protection.total_requests} requisições</span>
                <span>❌ Erros: ${prot.protection.total_errors}</span>
            `;
            
            document.getElementById('channels').innerHTML = channels.channels.map(ch => `
                <div class="channel" onclick="location='/watch/${ch.id}'">
                    <div class="channel-title">${ch.title}</div>
                    <div class="channel-program">${ch.program}</div>
                </div>
            `).join('');
        }
        
        load();
        setInterval(load, 30000);
    </script>
</body>
</html>
"""

HTML_PLAYER = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Player - Canal {{ live_id }}</title>
    <script src="https://cdn.jsdelivr.net/npm/shaka-player@4.7.11/dist/shaka-player.compiled.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #000; color: #fff; font-family: system-ui, sans-serif; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        video { width: 100%; border-radius: 12px; background: #111; }
        .info { margin-top: 15px; display: flex; gap: 15px; }
        .info a { color: #e31937; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <video id="video" controls autoplay></video>
        <div class="info">
            <a href="/">← Voltar</a>
            <span>Canal: {{ live_id }}</span>
            <a href="/live/{{ live_id }}.ts?lang=por" target="_blank">📺 MPEG-TS</a>
        </div>
    </div>
    <script>
        async function init() {
            shaka.polyfill.installAll();
            const video = document.getElementById('video');
            const player = new shaka.Player(video);
            
            const res = await fetch('/api/stream/{{ live_id }}');
            const data = await res.json();
            
            if (data.success) {
                player.configure({
                    drm: { servers: { 'com.widevine.alpha': data.license } }
                });
                player.getNetworkingEngine().registerRequestFilter((type, request) => {
                    if (type === shaka.net.NetworkingEngine.RequestType.LICENSE) {
                        request.headers['authorization'] = data.token;
                    }
                });
                await player.load(data.manifest);
            }
        }
        init();
    </script>
</body>
</html>
"""

HTML_ADMIN = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Admin - Claro TV+ V4</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: system-ui, sans-serif; background: #0a0a0a; color: #fff; padding: 20px; }
        .container { max-width: 1000px; margin: 0 auto; }
        h1 { margin-bottom: 20px; }
        .section { background: #1a1a1a; padding: 20px; border-radius: 12px; margin-bottom: 20px; }
        .section h2 { margin-bottom: 15px; font-size: 18px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; }
        .stat { background: #252525; padding: 15px; border-radius: 8px; }
        .stat-value { font-size: 24px; font-weight: bold; color: #e31937; }
        .stat-label { font-size: 12px; color: #888; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; color: #888; font-size: 13px; }
        .form-group select, .form-group input { width: 100%; padding: 10px; border-radius: 6px; border: 1px solid #333; background: #252525; color: #fff; }
        button { background: #e31937; color: #fff; border: none; padding: 12px 24px; border-radius: 8px; cursor: pointer; font-size: 14px; }
        button:hover { background: #c01530; }
        .back { color: #e31937; text-decoration: none; display: inline-block; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" class="back">← Voltar</a>
        <h1>🛡️ Painel Admin - V4 Protected</h1>
        
        <div class="section">
            <h2>📊 Status de Proteção</h2>
            <div class="grid" id="protection-stats"></div>
        </div>
        
        <div class="section">
            <h2>⚙️ Configurações de Stream</h2>
            <div class="grid">
                <div class="form-group">
                    <label>Idioma de Áudio</label>
                    <select id="audio_lang">
                        <option value="por">🇧🇷 Português</option>
                        <option value="eng">🇺🇸 English</option>
                        <option value="spa">🇪🇸 Español</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Qualidade</label>
                    <select id="quality">
                        <option value="best">🏆 Melhor</option>
                        <option value="1080p,best">📺 1080p</option>
                        <option value="720p,best">📺 720p</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Buffer</label>
                    <select id="ringbuffer_size">
                        <option value="8M">8 MB</option>
                        <option value="16M">16 MB</option>
                        <option value="32M">32 MB</option>
                    </select>
                </div>
            </div>
            <button onclick="saveSettings()">💾 Salvar Configurações</button>
        </div>
        
        <div class="section">
            <h2>📋 Streams Ativos</h2>
            <div id="active-streams"></div>
        </div>
    </div>
    
    <script>
        async function loadStatus() {
            const res = await fetch('/api/protection');
            const data = await res.json();
            const p = data.protection;
            
            document.getElementById('protection-stats').innerHTML = `
                <div class="stat"><div class="stat-value">${p.active_streams}/${p.max_streams}</div><div class="stat-label">Streams Ativos</div></div>
                <div class="stat"><div class="stat-value">${p.total_requests}</div><div class="stat-label">Requisições</div></div>
                <div class="stat"><div class="stat-value">${p.total_errors}</div><div class="stat-label">Erros</div></div>
                <div class="stat"><div class="stat-value">${p.blocked_requests}</div><div class="stat-label">Bloqueados</div></div>
                <div class="stat"><div class="stat-value">${p.channels_in_cooldown}</div><div class="stat-label">Em Cooldown</div></div>
                <div class="stat"><div class="stat-value">${p.proxies_configured}</div><div class="stat-label">Proxies</div></div>
            `;
            
            document.getElementById('active-streams').innerHTML = p.streams_list.length 
                ? p.streams_list.map(s => `<span style="background:#252525;padding:5px 10px;border-radius:4px;margin:2px;display:inline-block;">Canal ${s}</span>`).join('')
                : '<span style="color:#888">Nenhum stream ativo</span>';
        }
        
        async function loadSettings() {
            const res = await fetch('/api/settings');
            const data = await res.json();
            document.getElementById('audio_lang').value = data.settings.audio_lang || 'por';
            document.getElementById('quality').value = data.settings.quality || 'best';
            document.getElementById('ringbuffer_size').value = data.settings.ringbuffer_size || '16M';
        }
        
        async function saveSettings() {
            const settings = {
                audio_lang: document.getElementById('audio_lang').value,
                quality: document.getElementById('quality').value,
                ringbuffer_size: document.getElementById('ringbuffer_size').value
            };
            await fetch('/api/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(settings)
            });
            alert('✅ Configurações salvas!');
        }
        
        loadStatus();
        loadSettings();
        setInterval(loadStatus, 5000);
    </script>
</body>
</html>
"""


# ================================================================================
# ROTA DE STREAM API (para web player)
# ================================================================================

@app.route('/api/stream/<live_id>')
def api_stream(live_id):
    data, err = get_stream_data(live_id)
    if err:
        return jsonify({"success": False, "error": err})
    return jsonify({
        "success": True,
        "manifest": data['manifest'],
        "license": data['license'],
        "token": data['token']
    })


# ================================================================================
# PROXY
# ================================================================================

@app.route('/proxy')
def proxy_request():
    url = request.args.get('url')
    if not url:
        return "URL required", 400
    
    try:
        protector.wait_rate_limit()
        headers = {**HEADERS_WEB}
        cookies = load_cdn_cookies()
        resp = proxy_session.get(url, headers=headers, cookies=cookies, stream=True, verify=False, timeout=15)
        
        excluded = ['transfer-encoding', 'content-encoding', 'content-length']
        resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}
        
        return Response(resp.iter_content(chunk_size=8192), status=resp.status_code, headers=resp_headers)
    except Exception as e:
        return str(e), 500


# ================================================================================
# MAIN
# ================================================================================

if __name__ == '__main__':
    print("=" * 60)
    print("🛡️ CLARO TV+ UNIFIED V4 - PROTEÇÃO ANTI-BLOQUEIO")
    print("=" * 60)
    print(f"📺 Max Streams Simultâneos: {ProtectionConfig.MAX_CONCURRENT_STREAMS}")
    print(f"⚡ Max Requisições/segundo: {ProtectionConfig.MAX_REQUESTS_PER_SECOND}")
    print(f"⏱️  Cooldown em 403: {ProtectionConfig.COOLDOWN_ON_403}s")
    print(f"⏱️  Cooldown em 429: {ProtectionConfig.COOLDOWN_ON_429}s")
    print(f"🔄 Proxies configurados: {len(ProtectionConfig.PROXY_LIST)}")
    print("=" * 60)
    print(f"🌐 Servidor: https://0.0.0.0:{ServerConfig.PORTA}")
    print("=" * 60)
    
    # Verificar certificados
    if not os.path.exists(ServerConfig.CERT_FILE):
        print("⚠️ Gerando certificados SSL...")
        os.system(f'openssl req -x509 -newkey rsa:4096 -keyout {ServerConfig.KEY_FILE} -out {ServerConfig.CERT_FILE} -days 365 -nodes -subj "/CN=localhost"')
    
    # Iniciar servidor
    from werkzeug.serving import run_simple
    run_simple(
        '0.0.0.0', 
        ServerConfig.PORTA, 
        app, 
        ssl_context=(ServerConfig.CERT_FILE, ServerConfig.KEY_FILE),
        threaded=True,
        use_reloader=False
    )
