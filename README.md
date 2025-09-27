codex-baixaryoutube (CLI)
==============================

Ferramenta de linha de comando para:

- Principal: gerar rapidamente um arquivo com URLs e, em seguida, um arquivo com URL+data
  - Fase 1 (rápida): `data/<nome>.urls.txt` com uma URL por linha
  - Fase 2: `data/<nome>.txt` com duas colunas: `URL YYYYMMDD` (gravado incrementalmente e, ao final, ordenado por data desc)
- Secundário: baixar legendas em português de cada vídeo listado
  - Prioriza legendas manuais; se não houver, usa automáticas
  - Prioridade de idioma: pt > en
  - Converte .srt ou .vtt para texto simples (não requer ffmpeg)
  - Saída: `data/<nome-da-lista>/<video_id>.txt`

Pré-requisitos
--------------

- Python 3.10+
- Virtualenv (opcional, recomendado)
- yt-dlp: `pip install -r requirements.txt`

Ambiente (venv)
---------------

1) Criar e ativar um ambiente virtual:

   - Linux/macOS:

     ```bash
     cd codex-baixaryoutube
     python3 -m venv .venv
     source .venv/bin/activate
     ```

   - Windows (PowerShell):

     ```powershell
     cd codex-baixaryoutube
     py -m venv .venv
     .venv\Scripts\Activate.ps1
     ```

2) Instalar dependências:

   ```bash
   pip install -r requirements.txt
   ```

Uso rápido (CLI Python)
-----------------------

- Gerar lista passando a URL do canal (página /videos) ou de uma playlist:

  ```bash
  python codex-baixaryoutube/main.py "https://www.youtube.com/@canal/videos"
  # ou
  python codex-baixaryoutube/main.py "https://www.youtube.com/playlist?list=PLxxxx"
  ```

  Os arquivos serão salvos em `codex-baixaryoutube/data/` como:
  - `<nome>.urls.txt` (primeiro, só URLs)
  - `<nome>.txt` (depois, `URL YYYYMMDD`)
  O `<nome>` é derivado automaticamente do canal/playlist (ou use `--out` para trocar o nome dentro de `data/`).

- Ver o progresso (verbose):

  ```bash
  python codex-baixaryoutube/main.py -v list URL
  # ou apenas
  python codex-baixaryoutube/main.py -v URL
  ```

- Baixar legendas para cada vídeo a partir de um arquivo de lista:

  ```bash
  # Pode ser o .urls.txt (apenas URLs) ou o .txt (URL+data)
  python codex-baixaryoutube/main.py subs caminho/para/sua_lista.txt
  ```

  As legendas são salvas em `codex-baixaryoutube/data/<nome-da-lista>/<video_id>.txt`.

- Limpar arquivos gerados (data/):

  ```bash
  python codex-baixaryoutube/main.py clean  # pergunta confirmação
  python codex-baixaryoutube/main.py clean -y  # sem confirmação
  ```

- Com progresso detalhado:

  ```bash
  python codex-baixaryoutube/main.py -v subs data/<nome>.txt
  ```

Opções avançadas (rede/cookies)
-------------------------------

- `--force-ipv4`: força IPv4 nas requisições do yt-dlp (útil em redes que quebram IPv6).
- `--socket-timeout N`: define timeout de socket em segundos (ex.: 5 para falhar rápido).
- `--cookies-from-browser chrome|edge|firefox`: usa cookies do navegador para contornar telas de consentimento/login.
- `--proxy URL`: configura proxy HTTP/HTTPS (ex.: `http://user:pass@host:port`). Por padrão o script já envia `--proxy ""`, portanto use esta flag apenas quando quiser um proxy específico.
- `--retries N`: ajusta número de tentativas do yt-dlp.
- `--no-proxy`: mantém compatibilidade com versões anteriores (proxies já são removidos por padrão).
- `--workers N`: número de threads para resolver datas em paralelo (padrão: 4).
- `--retry429 N` e `--retry429-initial-delay S`: retentativas com backoff exponencial quando o YouTube responder HTTP 429 (Too Many Requests).
- O script força o cliente `android` (`--extractor-args "youtube:player_client=android"`) nas chamadas ao yt-dlp para contornar o bloqueio SABR do YouTube. Warnings sobre `po_token` podem aparecer, mas as datas/legendas seguem sendo obtidas normalmente.

Exemplos:

```bash
python -u codex-baixaryoutube/main.py -v --no-proxy --force-ipv4 --socket-timeout 5 list "URL" --limit 5
python -u codex-baixaryoutube/main.py -v --cookies-from-browser chrome list "URL"
python -u codex-baixaryoutube/main.py -v --proxy "http://user:pass@host:port" list "URL"
```

Comandos detalhados
-------------------

- Listar (atalhos: `list`, `listar` ou passar apenas a URL):

  ```bash
  python codex-baixaryoutube/main.py list URL [--limit N] [--out NOME.txt] [--workers N] [--no-prompt]
  ```

  Dica de desempenho: o comando cria primeiro `<nome>.urls.txt` imediatamente,
  depois grava `data/<nome>.txt` de forma incremental a cada data encontrada e,
  ao final, reordena por data desc. Você pode acelerar usando `--workers 8`.

- Baixar legendas (atalhos: `subs`, `legendas`):

  ```bash
  python codex-baixaryoutube/main.py subs LISTA.txt [--out-dir DIRETORIO]
  ```

Observações
-----------

- Para canais, use a URL da aba de vídeos (termina com `/videos`). Se você
  informar apenas a raiz do canal (`/@nome`), o programa completa para `/videos`.
- Em playlists extensas, use `--limit` para reduzir a quantidade processada.
- As legendas são convertidas para texto simples, removendo numeração e timestamps.
- Todas as saídas ficam sob `codex-baixaryoutube/data/`.
- Proxies de ambiente (`http_proxy`/`https_proxy`) são ignorados por padrão, evitando travamentos comuns em redes corporativas/WSL2. Quando precisar de proxy, informe-o explicitamente com `--proxy URL`.
- Caso o YouTube passe a exigir `GVS PO Token` para alguns vídeos, siga as instruções do aviso gerado pelo yt-dlp ou consulte a [wiki](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide).
- Ao final do comando `list`, será exibida uma pergunta interativa para baixar
  as legendas imediatamente. O padrão é "não". Para desabilitar a pergunta
  (ex.: uso em scripts), inclua `--no-prompt`.
- O conversor de legendas evita duplicações comuns em VTT/SRT (roll-up), removendo
  timestamps, índices, tags simples e linhas repetidas em janelas próximas.
