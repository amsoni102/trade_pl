# Start and stop the local research website.
# make start runs a small pipeline first, then serves http://127.0.0.1:8000

.PHONY: help start stop status restart
.ONESHELL:
.SHELLFLAGS := -eu -c

ROOT          := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
HOST          := 127.0.0.1
PORT          := 8000
UNIVERSE_SIZE := 400
RUN_DIR       := $(ROOT)/.run
PID           := $(RUN_DIR)/web.pid
LOG           := $(RUN_DIR)/web.log
PIPELINE_LOG  := $(RUN_DIR)/pipeline.log

ifeq ($(wildcard $(ROOT)/.venv/bin/python),)
PYTHON := python3
else
PYTHON := $(ROOT)/.venv/bin/python
endif

export PYTHONPATH := $(ROOT)/src

.DEFAULT_GOAL := help

# Print the website pid, or nothing. Pid file first, then the listener on PORT.
define FIND_WEB
pid=""
if [ -f "$(PID)" ]; then
	cand=$$(cat "$(PID)" 2>/dev/null || true)
	if [ -n "$$cand" ] && [ -r "/proc/$$cand/cmdline" ]; then
		cmd=$$(tr "\0" " " < "/proc/$$cand/cmdline")
		case "$$cmd" in
			*trade_pl.web.app*|*trade-pl-web*) pid="$$cand" ;;
		esac
	fi
fi
if [ -z "$$pid" ] && command -v ss >/dev/null 2>&1; then
	cand=$$(ss -lptn "sport = :$(PORT)" 2>/dev/null | sed -n "s/.*pid=\([0-9][0-9]*\).*/\1/p" | head -n 1)
	if [ -n "$$cand" ] && [ -r "/proc/$$cand/cmdline" ]; then
		cmd=$$(tr "\0" " " < "/proc/$$cand/cmdline")
		case "$$cmd" in
			*trade_pl.web.app*|*trade-pl-web*) pid="$$cand" ;;
		esac
	fi
fi
endef

help:
	@echo "trade_pl local website"
	@echo ""
	@echo "  make start     run pipeline (universe=$(UNIVERSE_SIZE); multi-market), then start site"
	@echo "  make stop      stop the site"
	@echo "  make status    show whether the site is running"
	@echo "  make restart   stop, then start (re-runs pipeline)"
	@echo ""
	@echo "Override size:  make start UNIVERSE_SIZE=40"
	@echo "Open http://$(HOST):$(PORT) after start."
	@echo "Logs: .run/pipeline.log  .run/web.log"

start:
	@mkdir -p "$(RUN_DIR)"
	cd "$(ROOT)"
	$(FIND_WEB)
	if [ -n "$$pid" ]; then
		echo "$$pid" > "$(PID)"
		echo "Already running (pid $$pid) — http://$(HOST):$(PORT)"
		exit 0
	fi
	rm -f "$(PID)"
	if ! $(PYTHON) -c "import trade_pl.web.app, trade_pl.run" >/dev/null 2>"$(LOG)"; then
		echo "Dependencies are not installed for $(PYTHON)."
		echo "  python3 -m venv .venv && .venv/bin/pip install -e ."
		echo "Details: $(LOG)"
		exit 1
	fi
	echo "===== $$(date -Is) pipeline --universe-size $(UNIVERSE_SIZE) =====" | tee "$(PIPELINE_LOG)"
	echo "Running pipeline (this can take a few minutes)…"
	if ! $(PYTHON) -m trade_pl.run --universe-size $(UNIVERSE_SIZE) 2>&1 | tee -a "$(PIPELINE_LOG)"; then
		echo "Pipeline failed. See .run/pipeline.log"
		exit 1
	fi
	echo "===== $$(date -Is) start web =====" >> "$(LOG)"
	setsid $(PYTHON) -m trade_pl.web.app >> "$(LOG)" 2>&1 < /dev/null &
	echo $$! > "$(PID)"
	pid=$$(cat "$(PID)")
	i=0
	while [ "$$i" -lt 40 ]; do
		if $(PYTHON) -c "import urllib.request; urllib.request.urlopen('http://$(HOST):$(PORT)/api/health', timeout=1).read()" >/dev/null 2>&1; then
			echo "Started (pid $$pid) — http://$(HOST):$(PORT)"
			echo "Logs: .run/pipeline.log  .run/web.log"
			exit 0
		fi
		if ! kill -0 "$$pid" 2>/dev/null; then
			echo "Failed to start. Last log lines:"
			tail -n 30 "$(LOG)"
			rm -f "$(PID)"
			exit 1
		fi
		i=$$((i + 1))
		sleep 0.5
	done
	echo "Process $$pid is up but http://$(HOST):$(PORT)/api/health did not respond yet."
	echo "Log: .run/web.log"
	exit 1

stop:
	@cd "$(ROOT)"
	$(FIND_WEB)
	if [ -z "$$pid" ]; then
		rm -f "$(PID)"
		echo "Website is not running"
		exit 0
	fi
	kill "$$pid" 2>/dev/null || true
	i=0
	while kill -0 "$$pid" 2>/dev/null && [ "$$i" -lt 25 ]; do
		i=$$((i + 1))
		sleep 0.2
	done
	if kill -0 "$$pid" 2>/dev/null; then
		kill -9 "$$pid" 2>/dev/null || true
	fi
	rm -f "$(PID)"
	echo "Stopped (pid $$pid)"

status:
	@cd "$(ROOT)"
	$(FIND_WEB)
	if [ -n "$$pid" ]; then
		mkdir -p "$(RUN_DIR)"
		echo "$$pid" > "$(PID)"
		echo "Running (pid $$pid) — http://$(HOST):$(PORT)"
	else
		rm -f "$(PID)"
		echo "Not running"
	fi

restart: stop start
