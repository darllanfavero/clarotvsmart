#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
🛡️ MÓDULO ANTI-BLOQUEIO PARA CLARO UNIFIED
================================================================================
Proteções contra rate limiting e bloqueio de IP do CloudFront
================================================================================
"""

import time
import random
import threading
import logging
from collections import defaultdict
from functools import wraps

logger = logging.getLogger(__name__)

# ================================================================================
# CONFIGURAÇÃO DE LIMITES
# ================================================================================

class RateLimitConfig:
    """Configurações de rate limiting"""
    
    # Limite de requisições por segundo (global)
    MAX_REQUESTS_PER_SECOND = 100
    
    # Limite de requisições por minuto por domínio
    MAX_REQUESTS_PER_MINUTE_PER_DOMAIN = 3000
    
    # Limite de canais simultâneos
    MAX_CONCURRENT_STREAMS = 50
    
    # Delay mínimo entre requisições para mesmo canal (ms)
    MIN_REQUEST_DELAY_MS = 100
    
    # Delay adicional aleatório (ms)
    RANDOM_DELAY_MS = 50
    
    # Tempo de cooldown após erro 403/429 (segundos)
    ERROR_COOLDOWN_SECONDS = 30
    
    # Número de erros antes de parar completamente
    MAX_CONSECUTIVE_ERRORS = 5
    
    # Habilitar jitter (variação aleatória)
    ENABLE_JITTER = True


# ================================================================================
# RATE LIMITER GLOBAL
# ================================================================================

class RateLimiter:
    """Rate limiter com token bucket algorithm"""
    
    def __init__(self, rate: float, capacity: int):
        self.rate = rate  # tokens por segundo
        self.capacity = capacity
        self.tokens = capacity
        self.last_time = time.time()
        self.lock = threading.Lock()
    
    def acquire(self, tokens: int = 1) -> float:
        """Tenta adquirir tokens. Retorna tempo de espera se necessário."""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_time
            
            # Repor tokens baseado no tempo passado
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_time = now
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0
            else:
                wait_time = (tokens - self.tokens) / self.rate
                return wait_time
    
    def wait(self, tokens: int = 1):
        """Espera até conseguir tokens"""
        wait_time = self.acquire(tokens)
        if wait_time > 0:
            time.sleep(wait_time)


# ================================================================================
# GERENCIADOR DE STREAMS
# ================================================================================

class StreamManager:
    """Gerencia streams ativos e previne sobrecarga"""
    
    def __init__(self, max_streams: int = 50):
        self.max_streams = max_streams
        self.active_streams = {}
        self.lock = threading.Lock()
        self.error_counts = defaultdict(int)
        self.blocked_until = defaultdict(float)
        self.global_rate_limiter = RateLimiter(
            rate=RateLimitConfig.MAX_REQUESTS_PER_SECOND,
            capacity=RateLimitConfig.MAX_REQUESTS_PER_SECOND * 2
        )
        self.domain_request_counts = defaultdict(lambda: {"count": 0, "reset_time": 0})
    
    def can_start_stream(self, channel_id: str) -> tuple[bool, str]:
        """Verifica se pode iniciar novo stream"""
        with self.lock:
            # Verificar limite de streams
            if len(self.active_streams) >= self.max_streams:
                return False, f"Limite de {self.max_streams} streams atingido"
            
            # Verificar se canal está em cooldown
            if channel_id in self.blocked_until:
                if time.time() < self.blocked_until[channel_id]:
                    remaining = int(self.blocked_until[channel_id] - time.time())
                    return False, f"Canal em cooldown por {remaining}s"
                else:
                    del self.blocked_until[channel_id]
            
            # Verificar erros consecutivos
            if self.error_counts.get(channel_id, 0) >= RateLimitConfig.MAX_CONSECUTIVE_ERRORS:
                return False, "Muitos erros consecutivos - canal desabilitado"
            
            return True, "OK"
    
    def register_stream(self, channel_id: str, stream_info: dict):
        """Registra um stream ativo"""
        with self.lock:
            self.active_streams[channel_id] = {
                "start_time": time.time(),
                "info": stream_info,
                "requests": 0
            }
            # Reset error count ao iniciar com sucesso
            self.error_counts[channel_id] = 0
    
    def unregister_stream(self, channel_id: str):
        """Remove um stream"""
        with self.lock:
            if channel_id in self.active_streams:
                del self.active_streams[channel_id]
    
    def register_error(self, channel_id: str, error_code: int):
        """Registra um erro para um canal"""
        with self.lock:
            if error_code in [403, 429]:
                self.error_counts[channel_id] += 1
                cooldown = RateLimitConfig.ERROR_COOLDOWN_SECONDS * self.error_counts[channel_id]
                self.blocked_until[channel_id] = time.time() + cooldown
                logger.warning(f"Canal {channel_id}: Erro {error_code}, cooldown {cooldown}s")
    
    def register_success(self, channel_id: str):
        """Registra sucesso e decrementa contador de erros"""
        with self.lock:
            if self.error_counts.get(channel_id, 0) > 0:
                self.error_counts[channel_id] -= 1
    
    def get_delay(self) -> float:
        """Calcula delay para próxima requisição"""
        base_delay = RateLimitConfig.MIN_REQUEST_DELAY_MS / 1000
        if RateLimitConfig.ENABLE_JITTER:
            jitter = random.uniform(0, RateLimitConfig.RANDOM_DELAY_MS / 1000)
            return base_delay + jitter
        return base_delay
    
    def wait_for_rate_limit(self):
        """Aguarda rate limiter global"""
        self.global_rate_limiter.wait()
    
    def check_domain_limit(self, domain: str) -> bool:
        """Verifica limite por domínio"""
        with self.lock:
            now = time.time()
            info = self.domain_request_counts[domain]
            
            # Reset contador a cada minuto
            if now >= info["reset_time"]:
                info["count"] = 0
                info["reset_time"] = now + 60
            
            if info["count"] >= RateLimitConfig.MAX_REQUESTS_PER_MINUTE_PER_DOMAIN:
                return False
            
            info["count"] += 1
            return True
    
    def get_stats(self) -> dict:
        """Retorna estatísticas"""
        with self.lock:
            return {
                "active_streams": len(self.active_streams),
                "max_streams": self.max_streams,
                "blocked_channels": len(self.blocked_until),
                "channels_with_errors": sum(1 for c in self.error_counts.values() if c > 0),
                "streams": list(self.active_streams.keys())
            }


# ================================================================================
# GERENCIADOR DE PROXY (para rotação)
# ================================================================================

class ProxyManager:
    """Gerencia pool de proxies (se configurado)"""
    
    def __init__(self, proxies: list = None):
        self.proxies = proxies or []
        self.current_index = 0
        self.failed_proxies = set()
        self.lock = threading.Lock()
    
    def add_proxy(self, proxy: str):
        """Adiciona um proxy ao pool"""
        with self.lock:
            if proxy not in self.proxies:
                self.proxies.append(proxy)
    
    def get_proxy(self) -> str:
        """Retorna próximo proxy disponível"""
        with self.lock:
            if not self.proxies:
                return None
            
            available = [p for p in self.proxies if p not in self.failed_proxies]
            if not available:
                # Reset failed proxies e tenta novamente
                self.failed_proxies.clear()
                available = self.proxies
            
            if not available:
                return None
            
            self.current_index = (self.current_index + 1) % len(available)
            return available[self.current_index]
    
    def mark_failed(self, proxy: str):
        """Marca proxy como falhado"""
        with self.lock:
            self.failed_proxies.add(proxy)
    
    def mark_success(self, proxy: str):
        """Marca proxy como funcionando"""
        with self.lock:
            self.failed_proxies.discard(proxy)


# ================================================================================
# DETECTOR DE BLOQUEIO
# ================================================================================

class BlockDetector:
    """Detecta sinais de bloqueio iminente"""
    
    def __init__(self):
        self.response_times = []
        self.error_history = []
        self.lock = threading.Lock()
    
    def record_response(self, status_code: int, response_time: float):
        """Registra uma resposta"""
        with self.lock:
            now = time.time()
            
            # Manter apenas últimos 5 minutos
            cutoff = now - 300
            self.response_times = [(t, rt) for t, rt in self.response_times if t > cutoff]
            self.error_history = [(t, e) for t, e in self.error_history if t > cutoff]
            
            self.response_times.append((now, response_time))
            
            if status_code >= 400:
                self.error_history.append((now, status_code))
    
    def is_being_throttled(self) -> tuple[bool, str]:
        """Detecta se está sendo limitado"""
        with self.lock:
            if not self.response_times:
                return False, "Sem dados"
            
            # Verificar aumento no tempo de resposta
            if len(self.response_times) >= 10:
                recent = [rt for _, rt in self.response_times[-10:]]
                older = [rt for _, rt in self.response_times[:-10][-10:]] if len(self.response_times) > 10 else recent
                
                avg_recent = sum(recent) / len(recent)
                avg_older = sum(older) / len(older)
                
                if avg_recent > avg_older * 2:
                    return True, f"Tempo de resposta aumentou: {avg_older:.2f}s → {avg_recent:.2f}s"
            
            # Verificar taxa de erros
            now = time.time()
            recent_errors = [e for t, e in self.error_history if t > now - 60]
            
            if len(recent_errors) >= 5:
                return True, f"{len(recent_errors)} erros no último minuto"
            
            # Verificar erros 429/403
            block_errors = [e for e in recent_errors if e in [403, 429]]
            if block_errors:
                return True, f"Erros de bloqueio detectados: {block_errors}"
            
            return False, "OK"


# ================================================================================
# INSTÂNCIAS GLOBAIS
# ================================================================================

stream_manager = StreamManager(max_streams=RateLimitConfig.MAX_CONCURRENT_STREAMS)
proxy_manager = ProxyManager()
block_detector = BlockDetector()


# ================================================================================
# DECORADORES
# ================================================================================

def rate_limited(func):
    """Decorator para aplicar rate limiting"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        stream_manager.wait_for_rate_limit()
        delay = stream_manager.get_delay()
        time.sleep(delay)
        return func(*args, **kwargs)
    return wrapper


def with_retry(max_retries: int = 3, backoff: float = 1.0):
    """Decorator para retry com backoff exponencial"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    last_error = e
                    wait_time = backoff * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"Tentativa {attempt + 1}/{max_retries} falhou: {e}. Aguardando {wait_time:.2f}s")
                    time.sleep(wait_time)
            raise last_error
        return wrapper
    return decorator


# ================================================================================
# FUNÇÕES AUXILIARES
# ================================================================================

def safe_request(session, method: str, url: str, **kwargs) -> tuple:
    """Faz requisição com proteções"""
    from urllib.parse import urlparse
    
    # Extrair domínio
    domain = urlparse(url).netloc
    
    # Verificar limite do domínio
    if not stream_manager.check_domain_limit(domain):
        return None, "Rate limit por domínio atingido"
    
    # Aplicar rate limiting global
    stream_manager.wait_for_rate_limit()
    
    # Obter proxy se disponível
    proxy = proxy_manager.get_proxy()
    if proxy:
        kwargs['proxies'] = {'http': proxy, 'https': proxy}
    
    # Fazer requisição
    start_time = time.time()
    try:
        response = getattr(session, method)(url, **kwargs)
        response_time = time.time() - start_time
        
        # Registrar para detecção
        block_detector.record_response(response.status_code, response_time)
        
        # Verificar throttling
        is_throttled, reason = block_detector.is_being_throttled()
        if is_throttled:
            logger.warning(f"⚠️ Possível throttling detectado: {reason}")
        
        # Marcar proxy
        if proxy:
            if response.status_code in [403, 429]:
                proxy_manager.mark_failed(proxy)
            else:
                proxy_manager.mark_success(proxy)
        
        return response, None
        
    except Exception as e:
        if proxy:
            proxy_manager.mark_failed(proxy)
        return None, str(e)


def get_protection_status() -> dict:
    """Retorna status das proteções"""
    is_throttled, throttle_reason = block_detector.is_being_throttled()
    
    return {
        "stream_stats": stream_manager.get_stats(),
        "is_throttled": is_throttled,
        "throttle_reason": throttle_reason,
        "proxies_available": len(proxy_manager.proxies),
        "proxies_failed": len(proxy_manager.failed_proxies),
        "rate_limit_config": {
            "max_requests_per_second": RateLimitConfig.MAX_REQUESTS_PER_SECOND,
            "max_concurrent_streams": RateLimitConfig.MAX_CONCURRENT_STREAMS,
            "max_requests_per_minute_per_domain": RateLimitConfig.MAX_REQUESTS_PER_MINUTE_PER_DOMAIN
        }
    }


# ================================================================================
# EXEMPLO DE USO
# ================================================================================

if __name__ == "__main__":
    # Teste básico
    print("🛡️ Módulo Anti-Bloqueio")
    print(f"   Max streams simultâneos: {RateLimitConfig.MAX_CONCURRENT_STREAMS}")
    print(f"   Max req/segundo: {RateLimitConfig.MAX_REQUESTS_PER_SECOND}")
    print(f"   Max req/minuto por domínio: {RateLimitConfig.MAX_REQUESTS_PER_MINUTE_PER_DOMAIN}")
    
    # Teste de rate limiter
    print("\n📊 Testando rate limiter...")
    rl = RateLimiter(rate=10, capacity=20)
    
    for i in range(25):
        wait = rl.acquire()
        if wait > 0:
            print(f"   Requisição {i+1}: Aguardando {wait:.3f}s")
            time.sleep(wait)
        else:
            print(f"   Requisição {i+1}: OK")
    
    print("\n✅ Módulo funcionando!")
