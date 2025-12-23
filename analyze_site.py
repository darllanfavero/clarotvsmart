#!/usr/bin/env python3
"""
Script para analisar site e coletar informações de rede/APIs
"""
import requests
import json
from urllib.parse import urljoin, urlparse
from datetime import datetime
import ssl
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import warnings
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

class SiteAnalyzer:
    def __init__(self, base_url):
        self.base_url = base_url
        self.session = requests.Session()
        self.network_logs = []
        
        # Configurar retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Desabilitar verificação SSL para sites com certificados auto-assinados
        self.session.verify = False
        
    def log_request(self, method, url, response=None, error=None):
        """Registra uma requisição de rede"""
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'method': method,
            'url': url,
            'status_code': response.status_code if response else None,
            'headers_request': dict(response.request.headers) if response else {},
            'headers_response': dict(response.headers) if response else {},
            'response_size': len(response.content) if response else 0,
            'content_type': response.headers.get('Content-Type', '') if response else '',
            'error': str(error) if error else None
        }
        
        # Tentar extrair JSON se possível
        if response and 'application/json' in response.headers.get('Content-Type', ''):
            try:
                log_entry['response_data'] = response.json()
            except:
                log_entry['response_preview'] = response.text[:500]
        elif response:
            log_entry['response_preview'] = response.text[:500]
            
        self.network_logs.append(log_entry)
        return log_entry
    
    def analyze_page(self, url):
        """Analisa uma página e coleta todas as informações"""
        print(f"\n{'='*60}")
        print(f"Analisando: {url}")
        print(f"{'='*60}\n")
        
        try:
            # Requisição principal
            response = self.session.get(url, timeout=30)
            log = self.log_request('GET', url, response)
            print(f"✓ GET {url}")
            print(f"  Status: {response.status_code}")
            print(f"  Content-Type: {response.headers.get('Content-Type', 'N/A')}")
            print(f"  Size: {len(response.content)} bytes")
            
            # Tentar encontrar links para recursos (JS, CSS, imagens, etc.)
            if 'text/html' in response.headers.get('Content-Type', ''):
                content = response.text
                
                # Procurar por URLs de recursos
                import re
                patterns = {
                    'scripts': r'<script[^>]+src=["\']([^"\']+)["\']',
                    'stylesheets': r'<link[^>]+href=["\']([^"\']+)["\']',
                    'images': r'<img[^>]+src=["\']([^"\']+)["\']',
                    'api_calls': r'["\'](/api/[^"\']+)["\']',
                    'fetch_calls': r'fetch\(["\']([^"\']+)["\']',
                    'ajax_calls': r'\.(get|post|put|delete)\(["\']([^"\']+)["\']',
                }
                
                found_resources = {}
                for resource_type, pattern in patterns.items():
                    matches = re.findall(pattern, content, re.IGNORECASE)
                    if matches:
                        found_resources[resource_type] = list(set(matches))
                
                if found_resources:
                    print(f"\n  Recursos encontrados no HTML:")
                    for resource_type, resources in found_resources.items():
                        print(f"    {resource_type}: {len(resources)} encontrados")
                        for resource in resources[:5]:  # Mostrar apenas os primeiros 5
                            full_url = urljoin(url, resource)
                            print(f"      - {full_url}")
                            
                            # Tentar fazer requisição para recursos importantes
                            if resource_type in ['api_calls', 'fetch_calls', 'ajax_calls']:
                                try:
                                    res = self.session.get(full_url, timeout=10)
                                    self.log_request('GET', full_url, res)
                                    print(f"        → Status: {res.status_code}")
                                except Exception as e:
                                    self.log_request('GET', full_url, error=e)
                                    print(f"        → Erro: {e}")
            
            return response
            
        except requests.exceptions.SSLError as e:
            print(f"✗ Erro SSL: {e}")
            self.log_request('GET', url, error=e)
            return None
        except requests.exceptions.RequestException as e:
            print(f"✗ Erro na requisição: {e}")
            self.log_request('GET', url, error=e)
            return None
    
    def generate_summary(self):
        """Gera resumo de todas as APIs e requisições"""
        print(f"\n{'='*60}")
        print("RESUMO DA ANÁLISE")
        print(f"{'='*60}\n")
        
        # Estatísticas gerais
        total_requests = len(self.network_logs)
        successful = len([l for l in self.network_logs if l['status_code'] and 200 <= l['status_code'] < 300])
        errors = len([l for l in self.network_logs if l['error'] or (l['status_code'] and l['status_code'] >= 400)])
        
        print(f"Total de requisições: {total_requests}")
        print(f"Sucesso: {successful}")
        print(f"Erros: {errors}\n")
        
        # Agrupar por tipo de conteúdo
        content_types = {}
        for log in self.network_logs:
            ct = log['content_type'].split(';')[0] if log['content_type'] else 'unknown'
            content_types[ct] = content_types.get(ct, 0) + 1
        
        print("Tipos de conteúdo:")
        for ct, count in sorted(content_types.items(), key=lambda x: x[1], reverse=True):
            print(f"  {ct}: {count}")
        
        # APIs encontradas
        print("\nAPIs e Endpoints encontrados:")
        api_endpoints = {}
        for log in self.network_logs:
            url = log['url']
            parsed = urlparse(url)
            path = parsed.path
            
            # Identificar APIs
            if '/api/' in path or path.startswith('/api'):
                api_endpoints[path] = {
                    'method': log['method'],
                    'status': log['status_code'],
                    'url': url
                }
            elif any(keyword in path for keyword in ['/watch/', '/video/', '/stream/', '/player/']):
                api_endpoints[path] = {
                    'method': log['method'],
                    'status': log['status_code'],
                    'url': url
                }
        
        if api_endpoints:
            for endpoint, info in sorted(api_endpoints.items()):
                status = info['status'] if info['status'] else 'N/A'
                print(f"  {info['method']} {endpoint} → Status: {status}")
        else:
            print("  Nenhuma API específica identificada")
        
        # Métodos HTTP usados
        methods = {}
        for log in self.network_logs:
            method = log['method']
            methods[method] = methods.get(method, 0) + 1
        
        print("\nMétodos HTTP:")
        for method, count in sorted(methods.items()):
            print(f"  {method}: {count}")
        
        # Códigos de status
        status_codes = {}
        for log in self.network_logs:
            status = log['status_code']
            if status:
                status_codes[status] = status_codes.get(status, 0) + 1
        
        print("\nCódigos de status HTTP:")
        for status, count in sorted(status_codes.items()):
            print(f"  {status}: {count}")
        
        return {
            'total_requests': total_requests,
            'successful': successful,
            'errors': errors,
            'content_types': content_types,
            'api_endpoints': api_endpoints,
            'methods': methods,
            'status_codes': status_codes
        }

def main():
    base_url = "https://151.244.40.192:8443"
    watch_url = f"{base_url}/watch/70"
    
    analyzer = SiteAnalyzer(base_url)
    
    print("="*60)
    print("ANÁLISE DE SITE E COLETA DE DADOS DE REDE")
    print("="*60)
    
    # Analisar página principal
    analyzer.analyze_page(base_url)
    
    # Analisar página de watch
    analyzer.analyze_page(watch_url)
    
    # Tentar descobrir endpoint de stream para o canal 70
    print(f"\n{'='*60}")
    print("Tentando descobrir endpoint de stream para canal 70")
    print(f"{'='*60}\n")
    
    stream_variations = [
        f"{base_url}/api/stream/70",
        f"{base_url}/api/stream?id=70",
        f"{base_url}/api/channel/70/stream",
        f"{base_url}/api/channel/70",
        f"{base_url}/api/watch/70",
        f"{base_url}/stream/70",
        f"{base_url}/api/v1/stream/70",
        f"{base_url}/api/v1/channels/70/stream",
    ]
    
    for stream_url in stream_variations:
        try:
            response = analyzer.session.get(stream_url, timeout=10)
            analyzer.log_request('GET', stream_url, response)
            print(f"✓ GET {stream_url} → Status: {response.status_code}")
            if response.status_code == 200:
                print(f"  Content-Type: {response.headers.get('Content-Type', 'N/A')}")
                if 'application/json' in response.headers.get('Content-Type', ''):
                    try:
                        data = response.json()
                        print(f"  Dados: {json.dumps(data, indent=2)[:300]}")
                    except:
                        pass
        except Exception as e:
            analyzer.log_request('GET', stream_url, error=e)
            print(f"✗ GET {stream_url} → Erro: {e}")
    
    # Gerar resumo
    summary = analyzer.generate_summary()
    
    # Salvar logs completos
    output_file = 'network_analysis.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'summary': summary,
            'all_requests': analyzer.network_logs
        }, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Logs completos salvos em: {output_file}")
    print(f"✓ Total de {len(analyzer.network_logs)} requisições registradas")

if __name__ == "__main__":
    main()
