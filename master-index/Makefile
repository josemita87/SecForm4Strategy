format:
	poetry run autopep8 --in-place src/source-parser.py


run-dev:
	poetry run python src/main.py



build:
	docker build -t master-index .

run-prod: build
	docker run \
	-it --network redpanda-network \
	--env-file .env \
	--name master-index master-index