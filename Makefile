.PHONY: sketch serve $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))

SKETCH_INPUT := $(word 2,$(MAKECMDGOALS))
SKETCH_OUTPUT := $(word 3,$(MAKECMDGOALS))

sketch:
	@if [ -z "$(SKETCH_INPUT)" ] || [ -z "$(SKETCH_OUTPUT)" ]; then \
		echo "Usage: make sketch input.svg output.svg"; \
		exit 2; \
	fi
	uv run python sketch_svg.py "$(SKETCH_INPUT)" "$(SKETCH_OUTPUT)" $(ARGS)

serve:
	uv run python -m http.server 5173

$(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS)):
	@:

%:
	@:
