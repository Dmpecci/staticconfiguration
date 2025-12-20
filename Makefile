test:
	pytest

test-all:
	pytest -m "slow or stress or not slow"

test-slow:
	pytest -m slow

test-stress:
	pytest -m stress
