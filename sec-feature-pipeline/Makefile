# Formatting commands
format-master:
	cd master-index && poetry run autopep8 --in-place src/main.py 

format-scraper:
	cd scraper && poetry run autopep8 --in-place src/parser.py src/transaction.py

format-enricher:
	cd api-enricher && poetry run autopep8 --in-place src/main.py src/api_handler.py

format-feature:
	cd kafka-to-store && poetry run autopep8 --in-place src/main.py src/feature_store.py

format-all: format-master format-scraper format-enricher format-feature



#Running dev commands
run-dev-master:
	cd master-index && poetry run python src/main.py

run-dev-scraper:
	cd scraper && poetry run python src/main.py


#Building docker containers for sec-4 pipeline
build-master:
	docker build -t master-index master-index/.

build-scraper:
	docker build -t scraper scraper/.

build-enricher:
	docker build -t api-enricher api-enricher/.

build-feature:
	docker build -t kafka-to-store kafka-to-store/.



#Stopping docker containers for sec-4 pipeline
stop-master:
	@if [ $$(docker ps -a -q -f name=master-index) ]; then \
		docker stop master-index; \
		docker rm master-index; \
	fi

stop-scraper:
	@if [ $$(docker ps -a -q -f name=scraper) ]; then \
		docker stop scraper; \
		docker rm scraper; \
	fi

stop-enricher:
	@if [ $$(docker ps -a -q -f name=api-enricher) ]; then \
		docker stop api-enricher; \
		docker rm api-enricher; \
	fi

stop-feature:
	@if [ $$(docker ps -a -q -f name=kafka-to-store) ]; then \
		docker stop kafka-to-store; \
		docker rm kafka-to-store; \
	fi


#Running docker containers for sec-4 pipeline

run-prod-master: stop-master build-master
	docker run \
	-it --network redpanda-network \
	--env-file master-index/.env \
	--name master-index master-index
	
run-prod-scraper: stop-scraper build-scraper
	docker run \
	-it --network redpanda-network \
	--env-file scraper/.env \
	--name scraper scraper

run-prod-enricher: stop-enricher build-enricher
	docker run \
	-it --network redpanda-network \
	--env-file api-enricher/.env \
	--name api-enricher api-enricher

run-prod-feature: stop-feature build-feature
	docker run \
	-it --network redpanda-network \
	--env-file kafka-to-store/.env \
	--env-file kafka-to-store/.credentials.env \
	-v /Users/josemita87/Documents/prod_inside_out/sec-feature-pipeline/kafka-to-store/src:/src \
	--name kafka-to-store kafka-to-store
	

#run-prod-all: run-prod-master run-prod-scraper run-prod-enricher run-prod-feature
run-prod-all: run-prod-scraper run-prod-enricher run-prod-feature


#Running container intreactively
run-interactive-enricher: stop-enricher build-enricher
	docker run -it api-enricher /bin/bash
