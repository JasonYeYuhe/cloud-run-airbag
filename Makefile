.PHONY: demo test install bench
demo:        ## run the live local demo (target-app + agent + dashboard)
	./run-local.sh

install:     ## install slim local deps into .venv-demo
	python3 -m venv .venv-demo && .venv-demo/bin/pip install -r agent/requirements-local.txt

test:        ## run the mock self-heal test
	cd agent && AIRBAG_BACKEND=mock python -m pytest -q

bench:       ## run Airbag-Bench and print the v2 baseline scorecard (see docs/AIRBAG_BENCH.md)
	cd agent && python tests/bench/run_bench.py
