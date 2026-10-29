


# Built from the repo root so the shared clients package is in the build context
build-training:
	docker build -f Dockerfile -t training ..


stop-training:
	@if [ $$(docker ps -a -q -f name=training) ]; then \
		docker stop training; \
		docker rm training; \
	fi

#Running docker containers for sec-4 pipeline

run-prod-training: stop-training build-training
	docker run -it --network redpanda-network \
	--env-file .env \
	--env-file .credentials.env \
	--name training \
	-v $(CURDIR)/src:/repo/training/src \
	training

