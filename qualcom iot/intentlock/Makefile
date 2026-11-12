install:
	pip install -e .[dev]

lint:
	ruff check src tests scripts

format:
	black --check src tests scripts

typecheck:
	mypy src

test:
	pytest

test-unit:
	pytest tests/unit

test-integration:
	pytest tests/integration

test-system:
	pytest tests/system

smoke:
	python scripts/dev_check_audio.py
	python scripts/dev_check_imu.py
	python scripts/dev_check_sync.py

clean:
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache src/*.egg-info
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
