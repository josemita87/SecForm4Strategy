# Build the master Docker image
build-master:
	docker build -t master-index ../sec-feature-pipeline/master-index

# Build the scraper Docker image
build-scraper:
	docker build -t scraper ../sec-feature-pipeline/scraper

# Build the feature Docker image
build-feature:
	docker build -t feature ../sec-feature-pipeline/kafka-to-store

# Build the inference Docker image
build-inference: 
	docker build -t inference .


# Stop and remove any existing master container
stop-master:
	-docker rm -f master || true

# Stop and remove any existing scraper container
stop-scraper:
	-docker rm -f scraper || true

# Stop and remove any existing feature container
stop-feature:
	-docker rm -f feature || true

stop-inference:
	-docker rm -f inference || true


# Run the master Docker container
run-infer-master: stop-master build-master
	docker run \
		-it --network redpanda-network \
		--env-file .env-inference-master.env \
		--name master master-index

# Run the scraper Docker container
run-infer-scraper: stop-scraper build-scraper
	docker run \
		-it --network redpanda-network \
		--env-file .env-inference-scraper.env \
		--name scraper scraper

run-infer-feature: stop-feature build-feature
	docker run \
		-it --network redpanda-network \
		--env-file .env-inference-feature.env \
		--name feature feature
 
 
run-infer-predict: stop-inference build-inference
	docker run \
		-it --network redpanda-network \
		--env-file .env \
		--env-file .credentials.env \
		--name inference inference