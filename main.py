#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def run_stream_collect(cmd: list[str]) -> tuple[int, str]:
    """Executa comando exibindo stdout em tempo real e coletando para retorno.
    Stderr herda do processo pai (mostra progresso do yt-dlp se houver).
    Retorna (returncode, stdout_text).
    """
    out_lines: list[str] = []
    # bufsize=1 e text=True para leitura line-buffered
    p = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=None)
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


def ytdlp_common_args(force_ipv4: bool | None, socket_timeout: int | None, cookies_from_browser: str | None, proxy: str | None, retries: int | None) -> list[str]:
    args: list[str] = []
    if force_ipv4:
        args += ["--force-ipv4"]
    if socket_timeout is not None:
        args += ["--socket-timeout", str(socket_timeout)]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    if proxy:
        args += ["--proxy", proxy]
    if retries is not None:
        args += ["--retries", str(retries)]
    return args


def maybe_print_cmd(verbose: bool, cmd: list[str]):
    if verbose:
        print("[cmd] ", " ".join(cmd))


def cmd_list(url: str, limit: int | None, out_path: str | None, verbose: bool = False,
             force_ipv4: bool | None = None, socket_timeout: int | None = None,
             cookies_from_browser: str | None = None, proxy: str | None = None,
             retries: int | None = None):
    url = clean_url(url)
    url = normalize_channel_url(url)
    name = detect_name(url)
    outfile = ensure_under_data(out_path, name)
    print(f"[info] Buscando URLs rapidamente (flat) em: {url}")
    args_flat = ["yt-dlp", "--flat-playlist"] + ytdlp_common_args(force_ipv4, socket_timeout, cookies_from_browser, proxy, retries)
    if limit:
        args_flat += ["--playlist-end", str(limit)]
    args_flat += ["--print", "%(webpage_url)s", url]
    maybe_print_cmd(verbose, args_flat)
    rc_flat, out_flat = run_stream_collect(args_flat) if verbose else (lambda cp: (cp.returncode, cp.stdout))(run(args_flat))
    if rc_flat != 0:
        print("[warn] Falha em modo rápido. Tentando modo completo (pode demorar)…")
        args_full = ["yt-dlp", "--skip-download"] + ytdlp_common_args(force_ipv4, socket_timeout, cookies_from_browser, proxy, retries)
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
        lines = [l for l in raw.splitlines() if l.strip()]
        lines.sort(reverse=True)
        outfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[ok] Salvo: {outfile} ({len(lines)} entradas)")
        return

    urls = [u.strip() for u in out_flat.splitlines() if u.strip().startswith("http")]
    if verbose:
        print(f"[info] {len(urls)} URLs coletadas. Resolvendo datas por vídeo…")

    lines: list[str] = []
    for i, u in enumerate(urls, start=1):
        per_args = [
            "yt-dlp",
            "--skip-download",
            "--print",
            "%(upload_date)s %(webpage_url)s",
            u,
        ]
        per_args = per_args[:1] + ytdlp_common_args(force_ipv4, socket_timeout, cookies_from_browser, proxy, retries) + per_args[1:]
        if verbose:
            print(f"[info] ({i}/{len(urls)}) {u}")
            maybe_print_cmd(verbose, per_args)
            rc, per_out = run_stream_collect(per_args)
            if rc != 0:
                print(f"[warn] Falha ao obter data: {u}")
                continue
        else:
            cp = run(per_args)
            if cp.returncode != 0:
                continue
            per_out = cp.stdout
        for l in per_out.splitlines():
            if l.strip():
                lines.append(l.strip())

    lines.sort(reverse=True)
    outfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] Salvo: {outfile} ({len(lines)} entradas)")


def srt_to_text(srt_path: Path) -> str:
    txt_lines: list[str] = []
    for line in srt_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if re.match(r"^\d+$", line):
            continue
        if "-->" in line:
            continue
        if not line.strip():
            txt_lines.append("")
            continue
        txt_lines.append(line)
    # Colapsa linhas em branco múltiplas
    out: list[str] = []
    last_blank = False
    for l in txt_lines:
        if l.strip() == "":
            if not last_blank:
                out.append("")
            last_blank = True
        else:
            out.append(l)
            last_blank = False
    return "\n".join(out).strip() + "\n"


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
             retries: int | None = None):
    # Procura lista: caminho informado ou, se não existir e for relativo, tenta em data/
    list_path = Path(list_file)
    if not list_path.exists() and not list_path.is_absolute():
        candidate = data_dir() / list_path
        if candidate.exists():
            list_path = candidate
    if not list_path.exists():
        print(f"[err] Lista não encontrada: {list_file}", file=sys.stderr)
        sys.exit(1)

    # Diretório de saída sempre dentro de data/ por padrão
    outdir = Path(out_dir) if out_dir else data_dir() / list_path.stem
    if not outdir.is_absolute():
        outdir = data_dir() / outdir
    tmpdir = outdir / ".tmp"
    outdir.mkdir(parents=True, exist_ok=True)
    tmpdir.mkdir(parents=True, exist_ok=True)

    urls: list[str] = []
    for line in list_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split()
        url = parts[-1]
        urls.append(url)

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
            "pt",
            "--convert-subs",
            "srt",
            "-P",
            str(tmpdir),  # garante escrita no tmpdir
            "--output",
            f"{vid}.%(ext)s",
            url,
        ]
        args = args[:1] + ytdlp_common_args(force_ipv4, socket_timeout, cookies_from_browser, proxy, retries) + args[1:]
        if verbose:
            maybe_print_cmd(verbose, args)
            rc, _ = run_stream_collect(args)
            if rc != 0:
                print(f"[warn] Falha em legendas: {url}")
                continue
        else:
            cp = run(args)
            if cp.returncode != 0:
                print(f"[warn] Falha em legendas: {url}: {(cp.stderr or '').strip()}")
                continue
        manual = tmpdir / f"{vid}.pt.srt"
        auto = tmpdir / f"{vid}.auto.pt.srt"
        chosen = manual if manual.exists() else auto if auto.exists() else None
        if not chosen:
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
    p.add_argument("--proxy", default=None, help="Proxy HTTP/HTTPS (ex.: http://usuario:senha@host:porta)")
    p.add_argument("--retries", type=int, default=None, help="Número de tentativas do yt-dlp em erros transitórios")
    sub = p.add_subparsers(dest="cmd", required=False)

    l = sub.add_parser("list", help="Gerar lista (YYYYMMDD URL)")
    l.add_argument("url", help="URL do canal (/videos) ou playlist")
    l.add_argument("--limit", type=int, default=None, help="Limite de itens")
    l.add_argument("--out", default=None, help="Arquivo de saída .txt")

    l2 = sub.add_parser("listar", help="Alias de 'list'")
    l2.add_argument("url")
    l2.add_argument("--limit", type=int, default=None)
    l2.add_argument("--out", default=None)

    s = sub.add_parser("subs", help="Baixar legendas PT para cada vídeo de uma lista")
    s.add_argument("list_file", help="Arquivo de lista gerado pelo comando 'list' ou URL único")
    s.add_argument("--out-dir", default=None, help="Diretório de saída (padrão: nome do arquivo de lista)")

    s2 = sub.add_parser("legendas", help="Alias de 'subs'")
    s2.add_argument("list_file")
    s2.add_argument("--out-dir", default=None)

    # Modo padrão: se nenhum subcomando for passado e houver apenas um argumento, trata como URL e executa 'list'.
    p.add_argument("positional", nargs="*", help=argparse.SUPPRESS)
    return p


def main():
    p = build_parser()
    args = p.parse_args()

    # Caso padrão: apenas uma URL passada sem subcomando
    verbose = getattr(args, "verbose", False)
    common = dict(
        force_ipv4=args.force_ipv4,
        socket_timeout=args.socket_timeout,
        cookies_from_browser=args.cookies_from_browser,
        proxy=args.proxy,
        retries=args.retries,
    )
    if getattr(args, "cmd", None) is None and len(args.positional) == 1:
        url = args.positional[0]
        if url.startswith("http://") or url.startswith("https://"):
            return cmd_list(url=clean_url(url), limit=None, out_path=None, verbose=verbose, **common)

    if args.cmd in ("list", "listar"):
        return cmd_list(args.url, getattr(args, "limit", None), getattr(args, "out", None), verbose=verbose, **common)
    elif args.cmd in ("subs", "legendas"):
        return cmd_subs(args.list_file, args.out_dir, verbose=verbose, **common)

    p.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
