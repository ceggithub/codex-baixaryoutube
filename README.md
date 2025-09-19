codex-baixaryoutube (CLI)
==============================

Ferramenta de linha de comando para:

- Principal: gerar lista de vídeos (canal ou playlist) com data e URL
  - Formato das linhas: `YYYYMMDD URL` (ordem cronológica reversa)
  - Arquivo gerado sempre em `data/<nome>.txt`
- Secundário: baixar legendas em português de cada vídeo listado
  - Prioriza legendas manuais; se não houver, usa automáticas
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

  O arquivo será salvo em `codex-baixaryoutube/data/<nome>.txt`, onde `<nome>` é derivado
  automaticamente do canal/playlist (ou use `--out` para trocar o nome dentro de `data/`).

- Ver o progresso (verbose):

  ```bash
  python codex-baixaryoutube/main.py -v list URL
  # ou apenas
  python codex-baixaryoutube/main.py -v URL
  ```

- Baixar legendas para cada vídeo a partir da lista gerada:

  ```bash
  python codex-baixaryoutube/main.py subs caminho/para/sua_lista.txt
  ```

  As legendas são salvas em `codex-baixaryoutube/data/<nome-da-lista>/<video_id>.txt`.

- Com progresso detalhado:

  ```bash
  python codex-baixaryoutube/main.py -v subs data/<nome>.txt
  ```

Comandos detalhados
-------------------

- Listar (atalhos: `list`, `listar` ou passar apenas a URL):

  ```bash
  python codex-baixaryoutube/main.py list URL [--limit N] [--out NOME.txt]
  ```

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
