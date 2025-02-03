run-dev:
	@echo "Running the development environment"
	cd yahoo_connect && poetry run python -m src.main

build-yahoo:
	@echo "Building the yahoo container"
	docker build -t yahoo_connect yahoo_connect/.

build-target:
	@echo "Building the target container"
	docker build -t target target/.

build-avg:
	@echo "Building the avg container"
	docker build -t avg-target avg-target/.


stop-yahoo:
	@if [ $$(docker ps -a -q -f name=yahoo_connect) ]; then \
		docker stop yahoo_connect; \
		docker rm yahoo_connect; \
	fi

stop-target:
	@if [ $$(docker ps -a -q -f name=target) ]; then \
		docker stop target; \
		docker rm target; \
	fi

stop-avg:
	@if [ $$(docker ps -a -q -f name=avg-target) ]; then \
		docker stop avg-target; \
		docker rm avg-target; \
	fi


run-prod-yahoo: stop-yahoo build-yahoo
	docker run \
	-it --network redpanda-network \
	--env-file yahoo_connect/.env \
	--env-file yahoo_connect/.credentials.env \
	-v /Users/josemita87/Documents/prod_inside_out/prices-feature-pipeline/yahoo_connect/src/data:/src/data \
	--name yahoo_connect yahoo_connect

run-prod-target: stop-target build-target
	docker run \
	-it \
	--network redpanda-network \
	--env-file target/.env \
	--name target \
	-v /Users/josemita87/Documents/prod_inside_out/prices-feature-pipeline/target/src:/app/src \
	target
