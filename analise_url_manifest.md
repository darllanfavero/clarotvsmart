# Análise Completa da URL do Manifesto DASH

## URL Analisada
```
http://144.22.222.241:8080/https://fq5r6s7t8u9v0wl.clarocdn.com.br/Content/Channel/SPOAEMHD/dsc1/manifest.mpd?0GKUSH_R4IN&DONTSTEAL
```

---

## 🔍 O QUE ESSA URL FAZ

Esta URL é um **servidor proxy** que faz o redirecionamento de uma requisição para um manifesto de streaming de vídeo da Claro/NET.

### Estrutura da URL:
- **Proxy Server**: `http://144.22.222.241:8080/`
- **URL Real**: `https://fq5r6s7t8u9v0wl.clarocdn.com.br/Content/Channel/SPOAEMHD/dsc1/manifest.mpd?0GKUSH_R4IN&DONTSTEAL`

---

## 📺 TIPO DE CONTEÚDO

**Manifesto MPD (MPEG-DASH)**
- Formato: `application/dash+xml`
- Tamanho: 19.467 bytes
- Tipo de streaming: **Dinâmico (Live TV)**
- Canal: **SPO AEM HD** (Sport TV/Canal esportivo)

---

## 🎥 CARACTERÍSTICAS DO STREAM

### Tipo de Transmissão:
- **Tipo**: Dinâmico (Live Stream)
- **Tempo de atualização**: A cada 2 segundos (`minimumUpdatePeriod="PT2.0S"`)
- **Buffer mínimo**: 2 segundos
- **Time-shift**: 2 minutos de gravação para retrocesso
- **Delay sugerido**: 2 segundos

### Data de Publicação:
- **publishTime**: 2025-12-23T12:44:01.231Z (última atualização)

---

## 📊 QUALIDADES DE VÍDEO DISPONÍVEIS

O stream oferece **5 qualidades diferentes** de vídeo adaptativo:

| ID | Resolução | Taxa de Bits | Frame Rate | Codec |
|----|-----------|--------------|------------|-------|
| stream_01 | 398x224 | 360 kbps | 29.97 fps | H.264 (avc1.64000d) |
| stream_02 | 640x360 | 750 kbps | 29.97 fps | H.264 (avc1.64001e) |
| stream_03 | 768x432 | 1100 kbps | 29.97 fps | H.264 (avc1.64001e) |
| stream_04 | 960x540 | 1800 kbps | 29.97 fps | H.264 (avc1.64001f) |
| stream_05 | **1280x720 (HD)** | **2500 kbps** | 29.97 fps | H.264 (avc1.640020) |

- **Aspect Ratio**: 16:9
- **Scan Type**: Progressive
- **Segmentos**: Aproximadamente 2 segundos por segmento (60 segmentos)

---

## 🔊 FAIXAS DE ÁUDIO DISPONÍVEIS

O stream oferece **4 faixas de áudio**:

### Áudio AC-3 (Dolby Digital):
1. **stream_08**: Inglês (eng) - 192 kbps - 48 kHz - AC-3
2. **stream_09**: Português (por) - 192 kbps - 48 kHz - AC-3

### Áudio AAC:
3. **stream_06**: Inglês (eng) - 96 kbps - 48 kHz - AAC (mp4a.40.2)
4. **stream_07**: Português (por) - 96 kbps - 48 kHz - AAC (mp4a.40.2)

---

## 🔐 PROTEÇÃO DRM (Digital Rights Management)

O conteúdo está **PROTEGIDO** com múltiplos sistemas de DRM:

### 1. **CENC (Common Encryption)**
- `schemeIdUri`: urn:mpeg:dash:mp4protection:2011
- Valor: cenc

### 2. **Microsoft PlayReady**
- UUID: `9a04f079-9840-4286-ab92-e65be0885f95`
- Algoritmo: AESCTR (AES Counter Mode)
- Tamanho da chave: 16 bytes
- Contém cabeçalho PlayReady codificado em Base64

### 3. **Google Widevine**
- UUID: `edef8ba9-79d6-4ace-a3c8-27dcd51d21ed`
- Contém PSSH (Protection System Specific Header)
- Key ID codificado: `bPVRtl7yM92n7wPO6L9ECEjj3JWbBg==`

**⚠️ IMPORTANTE**: Este conteúdo está protegido por DRM e requer licenças válidas para reprodução.

---

## 🌐 INFRAESTRUTURA DE REDE

### Headers HTTP Relevantes:

#### Servidor de Origem:
- **Servidor**: `vos04b-spolapvosc01o04`
- **POP de Origem**: `spolapsldmtd02` (São Paulo)
- **Request ID**: `f399f310c73fbe15b32d5ebc3011a4a5`

#### CDN CloudFront (AWS):
- **Status de Cache**: HIT (conteúdo em cache)
- **POP CloudFront**: `GRU1-P4` (Guarulhos, São Paulo)
- **CloudFront ID**: `f5nbXKCdG_ZnkoL_Ka5LmimCQ6pml4Yw5bfagwUAdvzwCkr8TflLOg==`
- **Via**: `1.1 40bc160d66cf54b01f9bf86ad9a1de2c.cloudfront.net`

#### Cache Control:
- **Cache-Control**: `max-age=1, public`
- **Expires**: 1 segundo após a requisição (conteúdo dinâmico)
- **ETag**: `"4c0b-c47952b8"`
- **Last-Modified**: 2025-12-23T12:44:01 GMT

#### CORS:
- **Access-Control-Allow-Origin**: `*` (aberto para qualquer origem)
- **Access-Control-Allow-Headers**: `*`

---

## 🎬 FORMATO DOS SEGMENTOS

### Template de Segmentos de Vídeo:
```
$RepresentationID$/Segment-$Time$.m4v
```
- Arquivo de inicialização: `$RepresentationID$/1765857919565_init.m4i`
- Timescale: 10.000.000 (10 MHz)
- Duração por segmento: ~2 segundos (20.020.000 unidades)

### Template de Segmentos de Áudio:
```
$RepresentationID$/Segment-$Time$.m4a
```
- Arquivo de inicialização: `$RepresentationID$/1765857919565_init.m4i`
- Timescale: 10.000.000 (10 MHz)
- Duração variável: 1,92s - 2,08s

---

## 📈 LINHA DO TEMPO DE SEGMENTOS

### Vídeo:
- **Início**: timestamp `4675561310552923` (timescale 10.000.000)
- **Duração**: 20.020.000 unidades (~2,002 segundos)
- **Repetições**: 59 segmentos consecutivos
- **Total**: ~120 segundos (2 minutos) de buffer

### Áudio:
- Segmentos com duração alternada entre 1,92s e 2,08s
- Sincronizado com os segmentos de vídeo
- Aproximadamente 60 segmentos disponíveis

---

## 🛠️ COMPATIBILIDADE

### Players Compatíveis:
- **dash.js** (JavaScript player)
- **Shaka Player** (Google)
- **ExoPlayer** (Android)
- **AVPlayer** (iOS/macOS) - com suporte a FairPlay
- **Bitmovin Player**
- **Video.js** com plugin DASH

### Requisitos:
- Suporte a MPEG-DASH
- Suporte a DRM (PlayReady ou Widevine)
- Conexão mínima recomendada: 2,5 Mbps (para HD)

---

## ⚠️ OBSERVAÇÕES IMPORTANTES

1. **Conteúdo Protegido**: O stream está protegido com múltiplos sistemas DRM
2. **Transmissão Ao Vivo**: É um stream dinâmico que atualiza a cada 2 segundos
3. **Time-Shift**: Permite retroceder até 2 minutos
4. **Proxy**: A URL usa um servidor proxy (144.22.222.241:8080)
5. **CDN**: O conteúdo é distribuído pela CloudFront (AWS)
6. **Região**: Servidores localizados em São Paulo, Brasil
7. **Canal**: SPO AEM HD (provavelmente canal esportivo)

---

## 🔗 URL FINAL REDIRECIONADA

O proxy adiciona um header especial que mostra a URL final acessada:

```
x-final-url: https://fq5r6s7t8u9v0wl.clarocdn.com.br/Content/Channel/SPOAEMHD/dsc1/manifest.mpd?0GKUSH_R4IN&DONTSTEAL
```

---

## 📝 RESUMO

Esta URL fornece acesso a um **stream de TV ao vivo em HD (720p)** com:
- ✅ Adaptive Bitrate Streaming (ABR)
- ✅ Múltiplas qualidades (360p a 720p)
- ✅ Áudio em múltiplos idiomas (PT/EN)
- ✅ Proteção DRM (PlayReady + Widevine)
- ✅ Time-shift de 2 minutos
- ✅ Distribuído via CDN (CloudFront)

**Tipo de Uso**: Streaming de TV ao vivo da operadora Claro/NET
