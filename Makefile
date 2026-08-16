# km-wiki-learner — common entry points
.PHONY: doctor daily learn prompt extract scan lint stats seed test unlock install-cron install-systemd

# On Windows `python3` is on PATH but is a Microsoft Store shortcut that runs
# nothing, so the name has to be tested rather than trusted. Override with
# `make PY=/path/to/python ...` if you want a specific interpreter.
PY ?= $(shell for c in python3 python py; do if command -v $$c >/dev/null 2>&1 && $$c -c '' >/dev/null 2>&1; then echo $$c; break; fi; done)
export KM_PYTHON = $(PY)

doctor:           ## check this machine can run the loop, and what's missing
	./scripts/doctor.sh

unlock:           ## clear a stuck lock left by an interrupted run
	@if [ -d loop/state/lock ]; then \
		pid=$$(cat loop/state/lock/pid 2>/dev/null || echo unknown); \
		if ps -p "$$pid" -o command= 2>/dev/null | grep -q daily.sh; then \
			echo "還在跑（pid $$pid）。真的要停就: kill $$pid"; exit 1; \
		fi; \
		rm -rf loop/state/lock && echo "已清除殘留的鎖（原持有者 pid $$pid）"; \
	else echo "沒有鎖，不用清"; fi

daily:            ## run the full daily loop now
	./loop/daily.sh

learn:            ## on-demand deep dive: make learn TOPIC="KV cache"
	@test -n "$(TOPIC)" || (echo 'usage: make learn TOPIC="..."' && exit 1)
	KM_TOPIC="$(TOPIC)" ./loop/daily.sh

prompt:           ## show the exact prompt the loop sends: make prompt [P=learn] [ARGS="..."]
	$(PY) tools/render.py prompts/$(or $(P),daily).md $(ARGS)

extract:          ## turn PDFs/images/docx in vault/Raw into readable text
	$(PY) tools/extract.py

scan:             ## print the vault work report (JSON)
	$(PY) tools/vault.py scan

lint:             ## validate vault structure and frontmatter
	$(PY) tools/vault.py lint

stats:            ## refresh the Home.md dashboard
	$(PY) tools/vault.py stats

seed:             ## create a seed note: make seed TITLE="Some Concept"
	@test -n "$(TITLE)" || (echo 'usage: make seed TITLE="..."' && exit 1)
	$(PY) tools/vault.py seed "$(TITLE)"

test:             ## run the toolkit test suite
	$(PY) -m unittest discover tests

install-cron:     ## schedule the loop via crontab (default 05:30)
	./scripts/install-cron.sh $(TIME)

install-systemd:  ## schedule the loop via systemd user timer (default 05:30)
	./scripts/install-systemd.sh $(TIME)
