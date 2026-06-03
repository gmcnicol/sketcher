.PHONY: sketch webapp-dev webapp-build model-builder-test compose-config $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))

SKETCH_INPUT := $(word 2,$(MAKECMDGOALS))
SKETCH_OUTPUT := $(word 3,$(MAKECMDGOALS))
SKETCH_INPUT_PATH := $(abspath $(SKETCH_INPUT))
SKETCH_OUTPUT_PATH := $(abspath $(SKETCH_OUTPUT))

sketch:
	@if [ -z "$(SKETCH_INPUT)" ] || [ -z "$(SKETCH_OUTPUT)" ]; then \
		echo "Usage: make sketch input.svg output.svg ARGS=\"...\""; \
		exit 2; \
	fi
	cd model-builder && uv run sketch "$(SKETCH_INPUT_PATH)" "$(SKETCH_OUTPUT_PATH)" $(ARGS)

webapp-dev:
	cd webapp && npm run dev

webapp-build:
	cd webapp && npm run build

model-builder-test:
	cd model-builder && uv run pytest

compose-config:
	docker compose config

$(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS)):
	@:

%:
	@:
