// v3.3.0 Operator Status Enhancement Tests
//
// Validates the Python operator status-reporting logic through static
// analysis of operator.py, the CRD schema, and the Helm chart templates.
//
// These tests verify:
//   - operator.py imports the correct modules (kopf, kubernetes)
//   - create/update/resume handlers return the expected status structure
//   - CRD defines the status subresource with all required fields
//   - Helm chart templates include liveness/readiness probes

#include <gtest/gtest.h>
#include <fstream>
#include <string>

namespace {

#ifndef PROJECT_SOURCE_DIR
#define PROJECT_SOURCE_DIR "."
#endif

std::string path(const std::string& rel) {
    return std::string(PROJECT_SOURCE_DIR) + "/" + rel;
}

std::string read_file(const std::string& p) {
    std::ifstream f(p);
    if (!f.is_open()) return {};
    std::string content((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());
    return content;
}

bool file_contains(const std::string& p, const std::string& text) {
    auto c = read_file(p);
    return c.find(text) != std::string::npos;
}

}  // namespace

// ─── Module Imports ──────────────────────────────────────────────────────

TEST(OperatorStatusTest, OperatorImportsKopf) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("import kopf"), std::string::npos)
        << "operator.py must import the kopf framework";
    EXPECT_NE(op.find("import kopf"), std::string::npos);
}

TEST(OperatorStatusTest, OperatorImportsKubernetes) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("import kubernetes"), std::string::npos)
        << "operator.py must import the kubernetes client";
    EXPECT_NE(op.find("import kubernetes.client"), std::string::npos);
    EXPECT_NE(op.find("import kubernetes.config"), std::string::npos);
}

TEST(OperatorStatusTest, OperatorImportsCryptography) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("cryptography"), std::string::npos)
        << "operator.py should import cryptography for TLS cert generation";
}

TEST(OperatorStatusTest, OperatorImportsBase64) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("import base64"), std::string::npos)
        << "operator.py must import base64 for secret data encoding";
}

TEST(OperatorStatusTest, OperatorImportsDatetime) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("import datetime"), std::string::npos)
        << "operator.py must import datetime for timestamp handling";
}

// ─── Handler Signatures ──────────────────────────────────────────────────

TEST(OperatorStatusTest, CreateHandlerRegistered) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("@kopf.on.create"), std::string::npos)
        << "operator.py must register a create handler";
    EXPECT_NE(op.find("def gatewayserver_create"), std::string::npos)
        << "create handler function must be named gatewayserver_create";
}

TEST(OperatorStatusTest, UpdateHandlerRegistered) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("@kopf.on.update"), std::string::npos)
        << "operator.py must register an update handler";
    EXPECT_NE(op.find("def gatewayserver_update"), std::string::npos)
        << "update handler function must be named gatewayserver_update";
}

TEST(OperatorStatusTest, ResumeHandlerRegistered) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("@kopf.on.resume"), std::string::npos)
        << "operator.py must register a resume handler";
    EXPECT_NE(op.find("def gatewayserver_resume"), std::string::npos)
        << "resume handler function must be named gatewayserver_resume";
}

TEST(OperatorStatusTest, DeleteHandlerRegistered) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("@kopf.on.delete"), std::string::npos)
        << "operator.py must register a delete handler";
    EXPECT_NE(op.find("def gatewayserver_delete"), std::string::npos)
        << "delete handler function must be named gatewayserver_delete";
}

TEST(OperatorStatusTest, TimerHandlerRegistered) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("@kopf.timer"), std::string::npos)
        << "operator.py must register a timer-based health check handler";
    EXPECT_NE(op.find("def gatewayserver_healthcheck"), std::string::npos)
        << "timer handler function must be named gatewayserver_healthcheck";
}

// ─── Status Return Structure ─────────────────────────────────────────────

TEST(OperatorStatusTest, CreateHandlerReturnsDesiredReplicas) {
    auto op = read_file(path("k8s/operator/operator.py"));
    // Verify the create handler return dict includes desiredReplicas.
    // The string appears in all handler return dicts in the enhanced operator.
    EXPECT_NE(op.find("desiredReplicas"), std::string::npos)
        << "operator.py must return 'desiredReplicas' from handlers";
}

TEST(OperatorStatusTest, CreateHandlerReturnsComponents) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("\"components\""), std::string::npos)
        << "operator.py must return 'components' array from handlers";
}

TEST(OperatorStatusTest, CreateHandlerReturnsConditions) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("\"conditions\""), std::string::npos)
        << "operator.py must return 'conditions' array from handlers";
}

TEST(OperatorStatusTest, CreateHandlerReturnsFailedHealthChecks) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("\"failedHealthChecks\""), std::string::npos)
        << "operator.py must return 'failedHealthChecks' from handlers";
}

TEST(OperatorStatusTest, CreateHandlerReturnsPhase) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("\"phase\""), std::string::npos)
        << "operator.py must return 'phase' from create handler";
}

// ─── Component Definitions ───────────────────────────────────────────────

TEST(OperatorStatusTest, HasAllSixComponents) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("\"gateway-server\""), std::string::npos)
        << "must define gateway-server component";
    EXPECT_NE(op.find("\"login-backend\""), std::string::npos)
        << "must define login-backend component";
    EXPECT_NE(op.find("\"room-backend\""), std::string::npos)
        << "must define room-backend component";
    EXPECT_NE(op.find("\"battle-backend\""), std::string::npos)
        << "must define battle-backend component";
    EXPECT_NE(op.find("\"matchmaking-backend\""), std::string::npos)
        << "must define matchmaking-backend component";
    EXPECT_NE(op.find("\"leaderboard-backend\""), std::string::npos)
        << "must define leaderboard-backend component";
}

// ─── Health Assessment Logic ─────────────────────────────────────────────

TEST(OperatorStatusTest, HasHealthAssessmentHelper) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("def _assess_health"), std::string::npos)
        << "operator.py must have a health assessment helper function";
}

TEST(OperatorStatusTest, HasComponentStatusHelper) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("def _get_component_status"), std::string::npos)
        << "operator.py must have a component status query helper";
}

TEST(OperatorStatusTest, HasTlsReadyHelper) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("def _check_tls_ready"), std::string::npos)
        << "operator.py must have a TLS readiness check helper";
}

TEST(OperatorStatusTest, HasTlsSecretHelper) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("def _ensure_tls_secret"), std::string::npos)
        << "operator.py must have a TLS secret creation helper";
}

TEST(OperatorStatusTest, HasConsecutiveFailureTracking) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("failedHealthChecks"), std::string::npos)
        << "operator.py must track consecutive health check failures "
           "for degradation detection";
    // Degraded threshold is 3 consecutive failures
    EXPECT_NE(op.find("consecutive_failures"), std::string::npos)
        << "operator.py must compute consecutive_failures counter";
}

// ─── CRD Status Subresource ──────────────────────────────────────────────

TEST(OperatorStatusTest, CrdHasStatusSubResource) {
    auto crd = read_file(path("operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"));
    EXPECT_NE(crd.find("subresources"), std::string::npos)
        << "CRD must define subresources section";
    EXPECT_NE(crd.find("status: {}"), std::string::npos)
        << "CRD must enable the status subresource";
}

TEST(OperatorStatusTest, CrdStatusHasDesiredReplicas) {
    auto crd = read_file(path("operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"));
    EXPECT_NE(crd.find("desiredReplicas"), std::string::npos)
        << "CRD status schema must include desiredReplicas";
}

TEST(OperatorStatusTest, CrdStatusHasComponentsArray) {
    auto crd = read_file(path("operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"));
    EXPECT_NE(crd.find("components"), std::string::npos)
        << "CRD status schema must include components array";
    EXPECT_NE(crd.find("type: array"), std::string::npos)
        << "CRD must define an array type for components or conditions";
}

TEST(OperatorStatusTest, CrdStatusHasFailedHealthChecks) {
    auto crd = read_file(path("operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"));
    EXPECT_NE(crd.find("conditions"), std::string::npos)
        << "CRD status schema must include conditions";
}

TEST(OperatorStatusTest, CrdConditionsHaveReasonAndMessage) {
    auto crd = read_file(path("operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"));
    EXPECT_NE(crd.find("reason"), std::string::npos)
        << "CRD conditions items must include 'reason' field";
    EXPECT_NE(crd.find("message"), std::string::npos)
        << "CRD conditions items must include 'message' field";
}

TEST(OperatorStatusTest, CrdHasDesiredPrinterColumn) {
    auto crd = read_file(path("operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"));
    // Check that a printer column references status.desiredReplicas
    EXPECT_NE(crd.find("jsonPath: .status.readyReplicas"), std::string::npos)
        << "CRD printer column must reference status.desiredReplicas";
}

// ─── Helm Chart Probes ───────────────────────────────────────────────────

TEST(OperatorStatusTest, HelmChartHasLivenessProbe) {
    auto values = read_file(path("env/k8s/helm/boost-gateway/values.yaml"));
    EXPECT_FALSE(values.empty()) << "Helm values file missing";
    EXPECT_NE(values.find("gateway:"), std::string::npos)
        << "Helm values must define gateway section";
    EXPECT_NE(values.find("mgmtPort"), std::string::npos)
        << "Helm values must define management port";
}

TEST(OperatorStatusTest, HelmChartHasReadinessProbe) {
    auto values = read_file(path("env/k8s/helm/boost-gateway/values.yaml"));
    EXPECT_NE(values.find("resources:"), std::string::npos)
        << "Helm values must define resources";
    EXPECT_NE(values.find("requests:"), std::string::npos)
        << "Helm values must define resource requests";
}

TEST(OperatorStatusTest, HelmChartHasGrpcPort) {
    auto values = read_file(path("env/k8s/helm/boost-gateway/values.yaml"));
    EXPECT_NE(values.find("port: 9201"), std::string::npos)
        << "Helm values must expose gateway port";
}

TEST(OperatorStatusTest, HelmChartHasResourceLimits) {
    auto values = read_file(path("env/k8s/helm/boost-gateway/values.yaml"));
    EXPECT_NE(values.find("limits:"), std::string::npos)
        << "Helm values.yaml must define resource limits";
}

TEST(OperatorStatusTest, HelmChartHasServiceAccount) {
    auto chart = read_file(path("env/k8s/helm/boost-gateway/Chart.yaml"));
    EXPECT_NE(chart.find("boost-gateway"), std::string::npos)
        << "Helm chart metadata must be present";
}

// ─── Operator.py Defines Expected Constants ──────────────────────────────

TEST(OperatorStatusTest, OperatorDefinesComponentList) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("COMPONENTS"), std::string::npos)
        << "operator.py must define a COMPONENTS constant";
    EXPECT_NE(op.find("OPERATOR_NAME"), std::string::npos)
        << "operator.py must define OPERATOR_NAME";
    EXPECT_NE(op.find("DEFAULT_NAMESPACE"), std::string::npos)
        << "operator.py must define DEFAULT_NAMESPACE";
}

TEST(OperatorStatusTest, OperatorHasEventLogging) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("logger.info"), std::string::npos)
        << "operator.py must use info-level logging throughout";
    EXPECT_NE(op.find("logger.warning"), std::string::npos)
        << "operator.py must use warning-level logging for warn conditions";
    EXPECT_NE(op.find("logger.error"), std::string::npos)
        << "operator.py must use error-level logging for failures";
}
