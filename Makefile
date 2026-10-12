# zdozier.com build commands. Run `make help` for a list.
#
#   make all       fetch from the APIs, then merge, build, and print the CV PDF
#   make preview   rebuild from a scratch copy of data/ and serve it (data/ stays untouched)
#   make offline   rebuild from the committed data/ snapshot (no network; rewrites data/*.json)
#   make serve     preview the built site at http://localhost:8000

PYTHON ?= python3

# Steps must run in order even if someone calls make with -j.
.NOTPARALLEL:

.PHONY: all offline preview fetch merge build pdf test serve clean help

all: fetch merge build pdf

offline: merge build pdf

# Merge and build from a copy of data/ in tmp/ (gitignored), so a local preview
# never leaves changed data/*.json behind to clash with the weekly bot commit.
PREVIEW_DATA = tmp/preview/data

preview:
	rm -rf tmp/preview
	mkdir -p tmp/preview
	cp -R data $(PREVIEW_DATA)
	$(PYTHON) scripts/merge.py --data-dir $(PREVIEW_DATA)
	$(PYTHON) scripts/build.py --data-dir $(PREVIEW_DATA)
	$(PYTHON) scripts/pdf.py
	$(MAKE) serve

fetch:
	$(PYTHON) scripts/fetch.py

merge:
	$(PYTHON) scripts/merge.py

build:
	$(PYTHON) scripts/build.py

pdf:
	$(PYTHON) scripts/pdf.py

test:
	$(PYTHON) -m unittest discover tests

serve:
	@echo "Serving _site/ at http://localhost:8000 (Ctrl-C to stop)"
	$(PYTHON) -m http.server 8000 -d _site

# Removes build output and Python caches only. data/ is never touched.
clean:
	rm -rf _site
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete

help:
	@echo "make all       fetch + merge + build + pdf (needs network)"
	@echo "make preview   merge + build + pdf from a scratch copy of data/, then serve (data/ unchanged)"
	@echo "make offline   merge + build + pdf from the committed data/ snapshot (rewrites data/*.json)"
	@echo "make fetch     query ORCID, OpenAlex, Crossref, Europe PMC, NIH RePORTER, GitHub"
	@echo "make merge     data/raw + content/*.yml -> data/*.json"
	@echo "make build     data + content + templates -> _site/"
	@echo "make pdf       _site/cv/index.html -> _site/cv.pdf (needs Google Chrome)"
	@echo "make test      run the unit tests"
	@echo "make serve     preview _site/ at http://localhost:8000"
	@echo "make clean     delete _site/ and Python caches"
