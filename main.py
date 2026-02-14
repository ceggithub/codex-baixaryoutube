#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import html
import subprocess
import sys
from pathlib import Path
import shutil


PROXY_ENV_VARS = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "ALL_PROXY",
)


def env_without_proxies(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of the environment without standard proxy variables."""
    env = dict(base_env or os.environ)
    for key in PROXY_ENV_VARS:
        env.pop(key, None)
    return env


GLOBAL_ENV = env_without_proxies()


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=False, env=GLOBAL_ENV)


def run_stream_collect(cmd: list[str]) -> tuple[int, str]:
    """Executa comando exibindo stdout em tempo real e coletando para retorno.
    Stderr herda do processo pai (mostra progresso do yt-dlp se houver).
    Retorna (returncode, stdout_text).
    """
    out_lines: list[str] = []
    # bufsize=1 e text=True para leitura line-buffered
    p = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=None, env=GLOBAL_ENV)
    assert p.stdout is not None
    try:
        for line in p.stdout:
            sys.stdout.write(line)
            out_lines.append(line)
    finally:
        p.stdout.close()
        rc = p.wait()
    return rc, "".join(out_lines)


def sanitize(name: str) -> str:
    name = re.sub(r"[\s\t\r\n]+", " ", name).strip()
    name = re.sub(r"[^\w\-\s\.]+", "_", name)
    name = name.replace(" ", "_")
    return name or "videos"


def normalize_channel_url(url: str) -> str:
    # Se for URL de canal e não terminar com /videos, tenta completar
    if re.search(r"youtube\.com/@[A-Za-z0-9_\-\.]+/?$", url):
        return url.rstrip("/") + "/videos"
    return url


def clean_url(url: str) -> str:
    """Remove quebras de linha e espaços acidentais dentro da URL.
    Se detectar whitespace interno, avisa no stdout e retorna versão compactada.
    """
    original = url
    trimmed = url.strip()
    if re.search(r"\s", trimmed):
        print("[warn] URL contém quebras de linha/espaços; normalizando…")
        trimmed = re.sub(r"\s+", "", trimmed)
    return trimmed


def detect_name(url: str) -> str:
    # Tenta consultar o título da playlist ou nome do canal usando yt-dlp
    probe = run([
        "yt-dlp",
        "--flat-playlist",
        "-I",
        "1",
        "--print",
        "%(playlist_title)s\t%(channel)s",
        url,
    ])
    if probe.returncode == 0 and probe.stdout.strip():
        pt, ch = (probe.stdout.strip().split("\t") + [""])[:2]
        if pt and pt.lower() != "none":
            return sanitize(pt)
        if ch and ch.lower() != "none":
            return sanitize(ch)
    # Heurísticas de fallback
    m = re.search(r"list=([A-Za-z0-9_\-]+)", url)
    if m:
        return f"playlist_{m.group(1)}"
    m = re.search(r"/@([^/]+)/?", url)
    if m:
        return f"channel_{sanitize(m.group(1))}"
    return "videos"


def data_dir() -> Path:
    base = Path(__file__).resolve().parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure_under_data(path_like: str | None, default_name: str) -> Path:
    base = data_dir()
    if not path_like:
        return base / f"{default_name}.txt"
    p = Path(path_like)
    # Se for caminho absoluto, respeita; se relativo, joga para data/
    return p if p.is_absolute() else base / p


def cmd_clean(yes: bool = False, verbose: bool = False):
    base = data_dir()
    if not yes and sys.stdin.isatty():
        try:
            ans = input(f"Tem certeza que deseja limpar '{base}'? Isso removerá arquivos gerados. [y/N]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes", "s", "sim"):
            print("[info] Limpeza cancelada.")
            return
    removed = 0
    for p in base.iterdir():
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed += 1
        except Exception as e:
            if verbose:
                print(f"[warn] Não foi possível remover {p}: {e}")
    print(f"[ok] Limpeza concluída. Itens removidos: {removed}")


def ytdlp_common_args(
    force_ipv4: bool | None,
    socket_timeout: int | None,
    cookies_from_browser: str | None,
    proxy: str | None,
    retries: int | None,
    player_client: str | None = None,
) -> list[str]:
    # Sempre ignora configurações globais do yt-dlp para ambiente reprodutível
    args: list[str] = ["--ignore-config"]
    if force_ipv4:
        args += ["--force-ipv4"]
    if socket_timeout is not None:
        args += ["--socket-timeout", str(socket_timeout)]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    if proxy:
        args += ["--proxy", proxy]
    # Se proxy for string vazia, ainda queremos passar explicitamente para desativar proxies do yt-dlp
    if proxy == "":
        args += ["--proxy", ""]
    if player_client:
        args += ["--extractor-args", f"youtube:player_client={player_client}"]
    if retries is not None:
        args += ["--retries", str(retries)]
    return args


def maybe_print_cmd(verbose: bool, cmd: list[str]):
    if verbose:
        print("[cmd] ", " ".join(cmd))


def cmd_list(url: str, limit: int | None, out_path: str | None, verbose: bool = False,
             force_ipv4: bool | None = None, socket_timeout: int | None = None,
             cookies_from_browser: str | None = None, proxy: str | None = None,
             retries: int | None = None,
             prompt_subs: bool = True,
             workers: int = 4,
             retry429: int | None = 2,
             retry429_initial_delay: float = 1.0):
    url = clean_url(url)
    url = normalize_channel_url(url)
    name = detect_name(url)
    outfile = ensure_under_data(out_path, name)
    urls_file = outfile.with_name(outfile.stem + ".urls.txt")
    print(f"[info] Buscando URLs rapidamente (flat) em: {url}")
    args_flat = ["yt-dlp", "--flat-playlist"] + ytdlp_common_args(force_ipv4, socket_timeout, cookies_from_browser, proxy, retries)
    if limit:
        args_flat += ["--playlist-end", str(limit)]
    args_flat += ["--print", "%(webpage_url)s", url]
    maybe_print_cmd(verbose, args_flat)
    rc_flat, out_flat = run_stream_collect(args_flat) if verbose else (lambda cp: (cp.returncode, cp.stdout))(run(args_flat))
    if rc_flat != 0:
        print("[warn] Falha em modo rápido. Tentando modo completo (pode demorar)…")
        args_full = ["yt-dlp", "--skip-download"] + ytdlp_common_args(
            force_ipv4,
            socket_timeout,
            cookies_from_browser,
            proxy,
            retries,
            player_client="android",
        )
        if limit:
            args_full += ["--playlist-end", str(limit)]
        args_full += ["--print", "%(upload_date)s %(webpage_url)s", url]
        maybe_print_cmd(verbose, args_full)
        if verbose:
            rc, stdout = run_stream_collect(args_full)
            if rc != 0:
                sys.exit(rc)
            raw = stdout
        else:
            cp = run(args_full)
            if cp.returncode != 0:
                print(cp.stderr or cp.stdout, file=sys.stderr)
                sys.exit(cp.returncode)
            raw = cp.stdout
        # Constrói duas saídas: 1) somente URLs, 2) URL DATE
        pairs: list[tuple[str, str]] = []  # (url, date)
        for l in raw.splitlines():
            l = l.strip()
            if not l:
                continue
            m = re.match(r"^(\d{8})\s+(https?://\S+)$", l)
            if m:
                date, urlv = m.group(1), m.group(2)
                pairs.append((urlv, date))
        # URLs
        only_urls = [u for u, _ in pairs]
        urls_file.write_text("\n".join(only_urls) + "\n", encoding="utf-8")
        print(f"[ok] Salvo URLs: {urls_file} ({len(only_urls)} entradas)")
        # URL DATE ordenado por data desc
        pairs.sort(key=lambda t: t[1], reverse=True)
        lines2 = [f"{u} {d}" for (u, d) in pairs]
        outfile.write_text("\n".join(lines2) + "\n", encoding="utf-8")
        print(f"[ok] Salvo URL+DATA: {outfile} ({len(lines2)} entradas)")
        # Pergunta por legendas ao final (se aplicável)
        if prompt_subs and sys.stdin.isatty():
            try:
                ans = input("Deseja baixar as legendas agora? [s/N]: ").strip().lower()
            except EOFError:
                ans = ""
            if ans in ("s", "y", "sim", "yes"):
                cmd_subs(str(outfile), None, verbose=verbose,
                         force_ipv4=force_ipv4, socket_timeout=socket_timeout,
                         cookies_from_browser=cookies_from_browser, proxy=proxy, retries=retries)
        return

    urls = [u.strip() for u in out_flat.splitlines() if u.strip().startswith("http")]
    # Grava imediatamente o arquivo apenas com URLs
    urls_file.write_text("\n".join(urls) + "\n", encoding="utf-8")
    print(f"[ok] Salvo URLs: {urls_file} ({len(urls)} entradas)")
    if verbose:
        print(f"[info] {len(urls)} URLs coletadas. Resolvendo datas por vídeo…")

    # Prepara arquivo incremental para URL+DATA
    try:
        outfile.write_text("", encoding="utf-8")  # zera arquivo anterior
    except Exception:
        pass
    if verbose:
        print(f"[info] Gravando incrementalmente em: {outfile}")

    def run_with_backoff(cmd: list[str]) -> subprocess.CompletedProcess:
        retries_left = max(0, retry429 or 0)
        delay = max(0.1, retry429_initial_delay)
        last = run(cmd)
        def is_429(cp: subprocess.CompletedProcess) -> bool:
            txt = (cp.stderr or "") + "\n" + (cp.stdout or "")
            txt_low = txt.lower()
            return cp.returncode != 0 and ("429" in txt_low or "too many requests" in txt_low)
        while retries_left > 0 and is_429(last):
            time.sleep(delay)
            delay *= 2
            last = run(cmd)
            retries_left -= 1
        return last

    pairs: list[tuple[str, str]] = []  # (url, date)

    def worker(u: str) -> tuple[str, str] | None:
        per_args = [
            "yt-dlp",
            "--skip-download",
            "--print",
            "%(upload_date)s %(webpage_url)s",
            u,
        ]
        per_args = per_args[:1] + ytdlp_common_args(
            force_ipv4,
            socket_timeout,
            cookies_from_browser,
            proxy,
            retries,
            player_client="android",
        ) + per_args[1:]
        if verbose:
            print(f"[info] [queue] {u}")
            maybe_print_cmd(verbose, per_args)
        cp = run_with_backoff(per_args)
        if cp.returncode != 0 and verbose:
            print(f"[warn] Falha ao obter data: {u}")
        out = cp.stdout or ""
        for l in out.splitlines():
            l = l.strip()
            if not l:
                continue
            m = re.match(r"^(\d{8})\s+(https?://\S+)$", l)
            if m:
                date, urlv = m.group(1), m.group(2)
                return (urlv, date)
        return None

    max_workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_map = {ex.submit(worker, u): u for u in urls}
        done_count = 0
        for fut in as_completed(future_map):
            done_count += 1
            res = fut.result()
            if res is None:
                continue
            urlv, date = res
            pairs.append((urlv, date))
            # Append incremental as tasks finish
            try:
                with outfile.open("a", encoding="utf-8") as f:
                    f.write(f"{urlv} {date}\n")
            except Exception:
                pass
            if verbose:
                print(f"[info] [done {done_count}/{len(urls)}] {urlv} {date}")

    # Ordena por data desc e grava URL DATE
    pairs.sort(key=lambda t: t[1], reverse=True)
    lines2 = [f"{u} {d}" for (u, d) in pairs]
    try:
        outfile.write_text("\n".join(lines2) + "\n", encoding="utf-8")
    except Exception:
        pass
    print(f"[ok] Salvo URL+DATA (ordenado): {outfile} ({len(lines2)} entradas)")
    # Pergunta por legendas ao final (se aplicável)
    if prompt_subs and sys.stdin.isatty():
        try:
            ans = input("Deseja baixar as legendas agora? [s/N]: ").strip().lower()
        except EOFError:
            ans = ""
        if ans in ("s", "y", "sim", "yes"):
            cmd_subs(str(outfile), None, verbose=verbose,
                     force_ipv4=force_ipv4, socket_timeout=socket_timeout,
                     cookies_from_browser=cookies_from_browser, proxy=proxy, retries=retries,
                     retry429=retry429, retry429_initial_delay=retry429_initial_delay)


def srt_to_text(srt_path: Path) -> str:
    lines = srt_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    out_texts: list[str] = []
    window: list[str] = []  # sliding window para deduplicação recente
    cue_buf: list[str] = []
    in_note = False

    def flush_cue():
        nonlocal cue_buf, window, out_texts
        if not cue_buf:
            return
        # Limpa e normaliza linhas de um único cue
        clean_lines: list[str] = []
        for l in cue_buf:
            t = re.sub(r"<[^>]+>", "", l)  # remove tags simples
            t = re.sub(r"\s+", " ", t).strip()
            if not t:
                continue
            if clean_lines and t == clean_lines[-1]:
                continue
            clean_lines.append(t)
        cue_buf = []
        if not clean_lines:
            return
        # Heurística de roll-up: escolha a linha mais longa do cue
        cue_text = max(clean_lines, key=len)
        cue_text = html.unescape(cue_text).strip()
        if not cue_text:
            return
        # Dedup: evita repetir a mesma legenda em janelas recentes
        recent = window[-5:]
        if cue_text in recent:
            return
        # Evita duplicar exatamente a última linha
        if out_texts and cue_text == out_texts[-1]:
            return
        out_texts.append(cue_text)
        window.append(cue_text)
        if len(window) > 10:
            window = window[-10:]

    for raw in lines + [""]:  # sentinela para flush final
        line = raw.rstrip("\n")
        if in_note:
            if not line.strip():
                in_note = False
            continue
        # Ignora cabeçalhos/meta
        if re.match(r"^\s*WEBVTT", line):
            continue
        if re.match(r"^\s*NOTE", line):
            in_note = True
            continue
        if re.match(r"^\s*(Kind:|Language:)", line):
            continue
        # Ignora contador SRT e timestamps
        if re.match(r"^\s*\d+\s*$", line):
            continue
        if "-->" in line:
            continue
        # Delimita cues por linha em branco
        if not line.strip():
            flush_cue()
            continue
        cue_buf.append(line)

    return "\n".join(out_texts).strip() + "\n"


def extract_id(url: str) -> str:
    m = re.search(r"[?&]v=([A-Za-z0-9_\-]{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"youtube\.com/shorts/([A-Za-z0-9_\-]{6,})", url)
    if m:
        return m.group(1)
    return sanitize(url)[:16]


def cmd_subs(list_file: str, out_dir: str | None, verbose: bool = False,
             force_ipv4: bool | None = None, socket_timeout: int | None = None,
             cookies_from_browser: str | None = None, proxy: str | None = None,
             retries: int | None = None,
             retry429: int | None = 2,
             retry429_initial_delay: float = 1.0):
    urls: list[str] = []
    single_url = clean_url(list_file)
    is_single_url = single_url.startswith("http://") or single_url.startswith("https://")
    list_stem = ""

    if is_single_url:
        urls = [single_url]
        list_stem = f"single_{extract_id(single_url)}"
    else:
        # Procura lista: caminho informado ou, se não existir e for relativo, tenta em data/
        list_path = Path(list_file)
        if not list_path.exists() and not list_path.is_absolute():
            candidate = data_dir() / list_path
            if candidate.exists():
                list_path = candidate
        if not list_path.exists():
            print(f"[err] Lista não encontrada: {list_file}", file=sys.stderr)
            sys.exit(1)
        list_stem = list_path.stem
        for line in list_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parts = line.split()
            url = next((t for t in parts if t.startswith("http://") or t.startswith("https://")), parts[-1])
            urls.append(url)

    # Diretório de saída sempre dentro de data/ por padrão
    outdir = Path(out_dir) if out_dir else data_dir() / list_stem
    if not outdir.is_absolute():
        outdir = data_dir() / outdir
    tmpdir = outdir / ".tmp"
    outdir.mkdir(parents=True, exist_ok=True)
    tmpdir.mkdir(parents=True, exist_ok=True)

    print(f"[info] Baixando legendas de {len(urls)} vídeos → {outdir}")
    for idx, url in enumerate(urls, start=1):
        vid = extract_id(url)
        if verbose:
            print(f"[info] ({idx}/{len(urls)}) URL: {url} → {vid}")
        # Força caminho de legendas ao tmpdir e tenta manual > auto
        args = [
            "yt-dlp",
            "--skip-download",
            "--write-sub",
            "--write-auto-sub",
            "--sub-lang",
            "pt,en",
            "-P",
            str(tmpdir),  # garante escrita no tmpdir
            "--output",
            f"{vid}.%(ext)s",
            url,
        ]
        args = args[:1] + ytdlp_common_args(
            force_ipv4,
            socket_timeout,
            cookies_from_browser,
            proxy,
            retries,
            player_client="android",
        ) + args[1:]
        if verbose:
            maybe_print_cmd(verbose, args)
        # Executa com backoff para HTTP 429
        retries_left = max(0, retry429 or 0)
        delay = max(0.1, retry429_initial_delay)
        cp = run(args)
        def is_429(cp: subprocess.CompletedProcess) -> bool:
            txt = (cp.stderr or "") + "\n" + (cp.stdout or "")
            low = txt.lower()
            return cp.returncode != 0 and ("429" in low or "too many requests" in low)
        while retries_left > 0 and is_429(cp):
            if verbose:
                print(f"[info] HTTP 429 detectado. Repetindo após {delay:.1f}s…")
            time.sleep(delay)
            delay *= 2
            cp = run(args)
            retries_left -= 1
        rc = cp.returncode
        # Preferência: manual PT > auto PT > manual EN > auto EN; prefere .srt, senão .vtt
        candidates = [
            tmpdir / f"{vid}.pt.srt",
            tmpdir / f"{vid}.pt.vtt",
            tmpdir / f"{vid}.auto.pt.srt",
            tmpdir / f"{vid}.auto.pt.vtt",
            tmpdir / f"{vid}.en.srt",
            tmpdir / f"{vid}.en.vtt",
            tmpdir / f"{vid}.auto.en.srt",
            tmpdir / f"{vid}.auto.en.vtt",
        ]
        chosen = next((p for p in candidates if p.exists()), None)
        if not chosen:
            if rc != 0:
                print(f"[warn] Falha em legendas: {url}")
            else:
                print(f"[warn] Sem legendas: {url}")
            continue
        txt = srt_to_text(chosen)
        (outdir / f"{vid}.txt").write_text(txt, encoding="utf-8")
        try:
            chosen.unlink()
        except Exception:
            pass
        print(f"[ok] {vid}.txt")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Gera lista (data URL) de canal/playlist e baixa legendas PT (manual>auto). "
            "Uso rápido: main.py URL | main.py list URL | main.py subs LISTA.txt"
        )
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Exibe progresso detalhado e comandos do yt-dlp")
    p.add_argument("--force-ipv4", action="store_true", help="Força IPv4 nas requisições do yt-dlp")
    p.add_argument("--socket-timeout", type=int, default=None, help="Timeout de socket (segundos) para o yt-dlp")
    p.add_argument("--cookies-from-browser", default=None, help="Usa cookies do navegador (ex.: chrome, edge, firefox)")
    p.add_argument("--proxy", default=None, help="Proxy HTTP/HTTPS (ex.: http://usuario:senha@host:porta). Passe string vazia para desativar proxies do yt-dlp")
    p.add_argument("--retries", type=int, default=None, help="Número de tentativas do yt-dlp em erros transitórios")
    p.add_argument("--no-proxy", action="store_true", help="Ignora variáveis de proxy do ambiente para subprocessos (recomendado em WSL/redes corporativas)")
    p.add_argument("--no-prompt", action="store_true", help="Não perguntar por download de legendas ao final do 'list'")
    p.add_argument("--workers", type=int, default=4, help="Número de downloads em paralelo para resolver datas")
    p.add_argument("--retry429", type=int, default=2, help="Tentativas extras em caso de HTTP 429 (Too Many Requests)")
    p.add_argument("--retry429-initial-delay", type=float, default=1.0, help="Atraso inicial (s) para backoff exponencial em 429")
    sub = p.add_subparsers(dest="cmd", required=False)

    l = sub.add_parser("list", help="Gerar listas (.urls.txt imediato, .txt incremental com URL YYYYMMDD)")
    l.add_argument("url", help="URL do canal (/videos) ou playlist")
    l.add_argument("--limit", type=int, default=None, help="Limite de itens")
    l.add_argument("--out", default=None, help="Arquivo de saída .txt")

    l2 = sub.add_parser("listar", help="Alias de 'list'")
    l2.add_argument("url")
    l2.add_argument("--limit", type=int, default=None)
    l2.add_argument("--out", default=None)

    s = sub.add_parser("subs", help="Baixar legendas PT/EN para cada vídeo de uma lista (preferência: manual PT > auto PT > manual EN > auto EN)")
    s.add_argument("list_file", help="Arquivo de lista gerado pelo comando 'list' ou URL único")
    s.add_argument("--out-dir", default=None, help="Diretório de saída (padrão: nome do arquivo de lista)")

    s2 = sub.add_parser("legendas", help="Alias de 'subs'")
    s2.add_argument("list_file")
    s2.add_argument("--out-dir", default=None)

    # Limpeza de artefatos em data/
    c = sub.add_parser("clean", help="Limpa arquivos e diretórios gerados em data/")
    c.add_argument("-y", "--yes", action="store_true", help="Não perguntar confirmação")

    # Modo padrão: se nenhum subcomando for passado e houver apenas um argumento, trata como URL e executa 'list'.
    p.add_argument("positional", nargs="*", help=argparse.SUPPRESS)
    return p


def main():
    p = build_parser()
    args = p.parse_args()

    # Caso padrão: apenas uma URL passada sem subcomando
    verbose = getattr(args, "verbose", False)
    # Ambiente dos subprocessos: por padrão, removemos proxies do ambiente.
    global GLOBAL_ENV
    effective_proxy = args.proxy if args.proxy is not None else ""
    if getattr(args, "no_proxy", False):
        effective_proxy = ""
    GLOBAL_ENV = env_without_proxies()

    common = dict(
        force_ipv4=args.force_ipv4,
        socket_timeout=args.socket_timeout,
        cookies_from_browser=args.cookies_from_browser,
        proxy=effective_proxy,
        retries=args.retries,
    )
    if getattr(args, "cmd", None) is None and len(args.positional) == 1:
        url = args.positional[0]
        if url.startswith("http://") or url.startswith("https://"):
            return cmd_list(url=clean_url(url), limit=None, out_path=None, verbose=verbose, prompt_subs=not args.no_prompt, workers=args.workers, retry429=args.retry429, retry429_initial_delay=args.retry429_initial_delay, **common)

    if args.cmd in ("list", "listar"):
        return cmd_list(args.url, getattr(args, "limit", None), getattr(args, "out", None), verbose=verbose, prompt_subs=not args.no_prompt, workers=args.workers, retry429=args.retry429, retry429_initial_delay=args.retry429_initial_delay, **common)
    elif args.cmd in ("subs", "legendas"):
        return cmd_subs(args.list_file, args.out_dir, verbose=verbose, retry429=args.retry429, retry429_initial_delay=args.retry429_initial_delay, **common)
    elif args.cmd == "clean":
        return cmd_clean(yes=getattr(args, "yes", False), verbose=verbose)

    p.print_help()
    sys.exit(2)

if __name__ == "__main__":
    main()
