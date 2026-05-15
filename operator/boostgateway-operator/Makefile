IMG ?= ghcr.io/honeybury/boostgateway-operator:v0.1.0

.PHONY: test
test:
	go test ./...

.PHONY: test-envtest
test-envtest:
	go test ./internal/controller -run TestEnvtest

.PHONY: build
build:
	go build ./...

.PHONY: run
run:
	go run ./main.go

.PHONY: docker-build
docker-build:
	docker build -t $(IMG) .

.PHONY: install
install:
	kubectl apply -f config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml
	kubectl apply -k config/default

.PHONY: install-sample
install-sample:
	kubectl apply -f config/samples/gateway_v1alpha1_boostgatewaycluster.yaml

.PHONY: uninstall-sample
uninstall-sample:
	kubectl delete -f config/samples/gateway_v1alpha1_boostgatewaycluster.yaml --ignore-not-found

.PHONY: kind-smoke
kind-smoke:
	bash ../../scripts/operator_kind_smoke.sh
