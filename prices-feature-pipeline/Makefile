


run-dev:
	@echo "Running the development environment"
	cd yahoo_connect && poetry run python -m src.main

build-yahoo:
	@echo "Building the yahoo container"
	docker build -t yahoo_connect yahoo_connect/.

stop-yahoo:
	@if [ $$(docker ps -a -q -f name=yahoo_connect) ]; then \
		docker stop yahoo_connect; \
		docker rm yahoo_connect; \
	fi

run-prod-yahoo: stop-yahoo build-yahoo
	docker run \
	-it --network redpanda-network \
	--env-file yahoo_connect/.env \
	--name yahoo_connect yahoo_connect