# km-wiki-learner — common entry points
.PHONY: doctor daily learn prompt extract scan lint stats seed test install-cron install-systemd

doctor:           ## check this machine can run the loop, and what's missing
	./scripts/doctor.sh

daily:            ## run the full daily loop now
	./loop/daily.sh

learn:            ## on-demand deep dive: make learn TOPIC="KV cache"
	@test -n "$(TOPIC)" || (echo 'usage: make learn TOPIC="..."' && exit 1)
	KM_TOPIC="$(TOPIC)" ./loop/daily.sh

prompt:           ## show the exact prompt the loop sends: make prompt [P=learn] [ARGS="..."]
	python3 tools/render.py prompts/$(or $(P),daily).md $(ARGS)

extract:          ## turn PDFs/images/docx in vault/Raw into readable text
	python3 tools/extract.py

scan:             ## print the vault work report (JSON)
	python3 tools/vault.py scan

lint:             ## validate vault structure and frontmatter
	python3 tools/vault.py lint

stats:            ## refresh the Home.md dashboard
	python3 tools/vault.py stats

seed:             ## create a seed note: make seed TITLE="Some Concept"
	@test -n "$(TITLE)" || (echo 'usage: make seed TITLE="..."' && exit 1)
	python3 tools/vault.py seed "$(TITLE)"

test:             ## run the toolkit test suite
	python3 -m unittest discover tests

install-cron:     ## schedule the loop via crontab (default 05:30)
	./scripts/install-cron.sh $(TIME)

install-systemd:  ## schedule the loop via systemd user timer (default 05:30)
	./scripts/install-systemd.sh $(TIME)
