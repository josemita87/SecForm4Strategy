


#Running docker containers for redpanda cluster 
start-redpanda:
	docker-compose -f redpanda.yml up -d
stop-redpanda:
	docker-compose -f redpanda.yml down

#Building docker containers for sec-4 pipeline
build-sec:
	docker-compose -f sec-feature-pipeline.yml build

#Running sec-4 pipeline using dockercompose
run-sec:build-sec
	docker-compose -f sec-feature-pipeline.yml up 
#Stopping sec-4 pipeline using dockercompose
stop-sec:
	docker-compose -f sec-4.yml down