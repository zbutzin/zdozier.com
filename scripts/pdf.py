#!/usr/bin/env python3
"""Print the CV page to _site/cv.pdf with headless Google Chrome.

The page is served from _site/ by a temporary local web server on 127.0.0.1
(so the root-relative stylesheet loads exactly as on the live site) and
printed with the print styles in static/css/site.css: US Letter, 0.75in
margins, "Dozier CV, page N" in the bottom margin.

Chrome is found via CHROME_PATH, then the standard macOS location, then
google-chrome, google-chrome-stable, chromium, chromium-browser on PATH.
If Chrome is missing the script warns and exits 0, except when CI=true,
where it exits 1.

Usage:
  python3 scripts/pdf.py                    # _site/cv/index.html -> _site/cv.pdf
  python3 scripts/pdf.py --site /tmp/site   # another build output

Runs on Python 3.8 and 3.12 (stdlib only).
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PATH_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def log(msg):
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()


def find_chrome(environ=None):
    environ = os.environ if environ is None else environ
    env = (environ.get("CHROME_PATH") or "").strip()
    if env:
        if os.path.isfile(env) and os.access(env, os.X_OK):
            return env
        found = shutil.which(env)
        if found:
            return found
        log("pdf: warning: CHROME_PATH=%s is not an executable; looking elsewhere" % env)
    if os.path.isfile(MAC_CHROME):
        return MAC_CHROME
    for name in PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve(directory):
    handler = functools.partial(_QuietHandler, directory=directory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def count_pages(data):
    return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))


def chrome_command(chrome, url, pdf_path, profile_dir):
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--hide-scrollbars",
        "--user-data-dir=" + profile_dir,
        "--no-pdf-header-footer",
        "--print-to-pdf-no-header",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=10000",
        "--print-to-pdf=" + pdf_path,
        url,
    ]
    if sys.platform.startswith("linux"):
        # GitHub's Ubuntu runners restrict the user namespaces Chrome's sandbox
        # needs. The page printed here is our own build output.
        cmd[1:1] = ["--no-sandbox", "--disable-dev-shm-usage"]
    return cmd


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print the CV page to cv.pdf with headless Chrome.")
    ap.add_argument("--site", default=os.path.join(ROOT, "_site"), help="build output directory")
    ap.add_argument("--out", default=None, help="PDF path (default: <site>/cv.pdf)")
    ap.add_argument("--page", default="/cv/", help="page to print (default /cv/)")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args(argv)

    site = os.path.abspath(args.site)
    out = os.path.abspath(args.out or os.path.join(site, "cv.pdf"))
    ci = os.environ.get("CI", "").lower() == "true"
    page_file = os.path.join(site, args.page.strip("/"), "index.html")
    if not os.path.exists(page_file):
        log("pdf: error: %s not found. Run python3 scripts/build.py first." % page_file)
        return 1

    chrome = find_chrome()
    if not chrome:
        log("pdf: Google Chrome was not found (set CHROME_PATH to its path). cv.pdf was not written.")
        return 1 if ci else 0

    httpd = serve(site)
    url = "http://127.0.0.1:%d%s" % (httpd.server_address[1], args.page)
    tmp = tempfile.mkdtemp(prefix="zdozier-pdf-")
    tmp_pdf = os.path.join(tmp, "cv.pdf")
    try:
        cmd = chrome_command(chrome, url, tmp_pdf, os.path.join(tmp, "profile"))
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            log("pdf: error: Chrome did not finish within %d seconds" % args.timeout)
            return 1
        data = b""
        if os.path.exists(tmp_pdf):
            with open(tmp_pdf, "rb") as fh:
                data = fh.read()
        if not data.startswith(b"%PDF") or len(data) < 1000:
            log("pdf: error: Chrome did not write a PDF (exit %s)." % proc.returncode)
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
            for line in tail:
                log("  " + line)
            return 1
        out_dir = os.path.dirname(out)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        shutil.move(tmp_pdf, out)
        pages = count_pages(data)
        log("pdf: wrote %s (%d pages, %d KB) with %s"
            % (os.path.relpath(out, os.getcwd()) if out.startswith(os.getcwd()) else out,
               pages, len(data) // 1024, os.path.basename(chrome)))
        print(pages)
        return 0
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
