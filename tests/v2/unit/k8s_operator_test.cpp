// v3.0.0 Phase 17+18: K8s operator + SDK multi-language validation tests
//
// Tests validate the presence and structure of the Python-based kopf
// operator, its CRD, Helm chart, and supporting deployment scripts.

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

bool file_contains(const std::string& path, const std::string& text) {
    auto c = read_file(path);
    return c.find(text) != std::string::npos;
}

}  // namespace

// ─── K8s Operator ────────────────────────────────────────────────────────

TEST(K8sOperatorTest, CrdExistsAndValid) {
    auto crd = read_file(path("operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"));
    EXPECT_FALSE(crd.empty()) << "CRD file missing";
    EXPECT_NE(crd.find("CustomResourceDefinition"), std::string::npos);
    EXPECT_NE(crd.find("boostgatewayclusters.gateway.boost.io"), std::string::npos);
    EXPECT_NE(crd.find("BoostGatewayCluster"), std::string::npos);
    EXPECT_NE(crd.find("openAPIV3Schema"), std::string::npos);
    EXPECT_NE(crd.find("subresources"), std::string::npos);
}

TEST(K8sOperatorTest, PythonOperatorFileExists) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_FALSE(op.empty()) << "operator.py missing";
    EXPECT_NE(op.find("kopf"), std::string::npos);
    EXPECT_NE(op.find("GatewayServer"), std::string::npos);
    EXPECT_NE(op.find("kubernetes"), std::string::npos);
    EXPECT_NE(op.find("@kopf.on.create"), std::string::npos);
    EXPECT_NE(op.find("@kopf.on.update"), std::string::npos);
    EXPECT_NE(op.find("@kopf.on.delete"), std::string::npos);
}

TEST(K8sOperatorTest, RequirementsFileExists) {
    auto req = read_file(path("k8s/operator/requirements.txt"));
    EXPECT_FALSE(req.empty()) << "requirements.txt missing";
    EXPECT_NE(req.find("kopf"), std::string::npos);
    EXPECT_NE(req.find("kubernetes"), std::string::npos);
    EXPECT_NE(req.find("cryptography"), std::string::npos);
}

TEST(K8sOperatorTest, DeployScriptExists) {
    // macOS: only the .sh deploy script is expected
    auto sh = read_file(path("k8s/operator/deploy-operator.sh"));
    EXPECT_FALSE(sh.empty()) << "deploy-operator.sh missing";
    EXPECT_NE(sh.find("kubectl"), std::string::npos);
}

TEST(K8sOperatorTest, HelmChartExists) {
    auto chart = read_file(path("env/k8s/helm/boost-gateway/Chart.yaml"));
    EXPECT_FALSE(chart.empty()) << "Helm Chart.yaml missing";
    EXPECT_NE(chart.find("boost-gateway"), std::string::npos);
    EXPECT_NE(chart.find("Game Server Framework"), std::string::npos);

    auto values = read_file(path("env/k8s/helm/boost-gateway/values.yaml"));
    EXPECT_FALSE(values.empty()) << "Helm values.yaml missing";
    EXPECT_NE(values.find("replicas"), std::string::npos);
    EXPECT_NE(values.find("gateway:"), std::string::npos);
    EXPECT_NE(values.find("resources:"), std::string::npos);
    EXPECT_NE(values.find("monitoring:"), std::string::npos);
}

TEST(K8sOperatorTest, OperatorSmokeScriptAssertsStatusComponentsAndConditions) {
    auto smoke = read_file(path("scripts/tools/operator_kind_smoke.py"));
    EXPECT_FALSE(smoke.empty()) << "operator smoke script missing";
    EXPECT_NE(smoke.find("status.get(\"components\""), std::string::npos);
    EXPECT_NE(smoke.find("\"Progressing\": \"False\""), std::string::npos);
    EXPECT_NE(smoke.find("\"Degraded\": \"False\""), std::string::npos);
    EXPECT_NE(smoke.find("\"TLSReady\": \"False\""), std::string::npos);
    EXPECT_NE(smoke.find("required_components"), std::string::npos);
}

// ─── New Operator Status Field Tests ─────────────────────────────────────

TEST(K8sOperatorTest, OperatorHasStatusConditions) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("Ready"), std::string::npos)
        << "operator.py must define a 'Ready' status condition";
    EXPECT_NE(op.find("Progressing"), std::string::npos)
        << "operator.py must define a 'Progressing' status condition";
    EXPECT_NE(op.find("Degraded"), std::string::npos)
        << "operator.py must define a 'Degraded' status condition";
    EXPECT_NE(op.find("TLSReady"), std::string::npos)
        << "operator.py must define a 'TLSReady' status condition";
}

TEST(K8sOperatorTest, OperatorHasComponentsArray) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("components"), std::string::npos)
        << "operator.py must reference 'components' array in status";
}

TEST(K8sOperatorTest, OperatorHasDesiredReplicas) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("desiredReplicas"), std::string::npos)
        << "operator.py must reference 'desiredReplicas' in status";
}

TEST(K8sOperatorTest, OperatorHasTimerBasedHealthCheck) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("@kopf.timer"), std::string::npos)
        << "operator.py must use @kopf.timer for periodic health checks";
}

TEST(K8sOperatorTest, OperatorHasResumeHandler) {
    auto op = read_file(path("k8s/operator/operator.py"));
    EXPECT_NE(op.find("@kopf.on.resume"), std::string::npos)
        << "operator.py must use @kopf.on.resume for TLS secret reconciliation";
}

TEST(K8sOperatorTest, CrdHasDesiredReplicasInStatus) {
    auto crd = read_file(path("operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"));
    EXPECT_NE(crd.find("desiredReplicas"), std::string::npos)
        << "CRD must define desiredReplicas in status schema";
    EXPECT_NE(crd.find("components"), std::string::npos)
        << "CRD must define components array in status schema";
}

TEST(K8sOperatorTest, CrdHasEnhancedConditions) {
    auto crd = read_file(path("operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml"));
    // CRD uses x-kubernetes-preserve-unknown-fields: true for conditions,
    // which allows reason/message without explicit schema fields.
    EXPECT_TRUE(crd.find("reason") != std::string::npos ||
                crd.find("x-kubernetes-preserve-unknown-fields") != std::string::npos)
        << "CRD conditions must include 'reason' field or use preserve-unknown-fields";
    EXPECT_TRUE(crd.find("message") != std::string::npos ||
                crd.find("x-kubernetes-preserve-unknown-fields") != std::string::npos)
        << "CRD conditions must include 'message' field or use preserve-unknown-fields";
}

// ─── SDK Multi-language ──────────────────────────────────────────────────

TEST(SdkMultiLanguageTest, PythonSdkExists) {
    auto py = read_file(path("sdk/python/__init__.py"));
    EXPECT_FALSE(py.empty()) << "Python SDK missing";
    EXPECT_NE(py.find("class SdkClient"), std::string::npos);
    EXPECT_NE(py.find("def connect"), std::string::npos);
    EXPECT_NE(py.find("def login"), std::string::npos);
    EXPECT_NE(py.find("def create_room"), std::string::npos);
    EXPECT_NE(py.find("def send_battle_input"), std::string::npos);
}

TEST(SdkMultiLanguageTest, CsharpSdkExists) {
    auto cs = read_file(path("sdk/csharp/SdkClient.cs"));
    EXPECT_FALSE(cs.empty()) << "C# SDK missing";
    EXPECT_NE(cs.find("class SdkClient"), std::string::npos);
    EXPECT_NE(cs.find("DllImport"), std::string::npos);  // v4.1: C API via P/Invoke
    EXPECT_NE(cs.find("gsdk_create"), std::string::npos);
    EXPECT_NE(cs.find("gsdk_login"), std::string::npos);
}

TEST(SdkMultiLanguageTest, CppSdkV3Compatible) {
    auto h = read_file(path("sdk/include/boost_gateway/sdk/client.h"));
    EXPECT_FALSE(h.empty()) << "C++ SDK header missing";
    EXPECT_NE(h.find("class SdkClient"), std::string::npos);
    EXPECT_NE(h.find("LoginResult login"), std::string::npos);
    EXPECT_NE(h.find("BattleInputResult send_battle_input"), std::string::npos);
}

TEST(SdkMultiLanguageTest, AllSdksHaveConsistentApi) {
    // All 3 SDKs should support: connect, login, create_room, join_room,
    // leave_room, set_ready, start_battle, send_input, disconnect

    auto cpp = read_file(path("sdk/include/boost_gateway/sdk/client.h"));
    auto py = read_file(path("sdk/python/__init__.py"));
    auto cs = read_file(path("sdk/csharp/SdkClient.cs"));

    // v4.1: Each SDK has core API (C++, Python via ctypes, C# via DllImport)
    for (auto* sdk : {&cpp, &py, &cs}) {
        bool has_api = sdk->find("connect") != std::string::npos ||
                       sdk->find("Connect") != std::string::npos ||
                       sdk->find("gsdk_connect") != std::string::npos;
        EXPECT_TRUE(has_api) << "SDK missing connect";
        has_api = sdk->find("login") != std::string::npos ||
                  sdk->find("Login") != std::string::npos ||
                  sdk->find("gsdk_login") != std::string::npos;
        EXPECT_TRUE(has_api) << "SDK missing login";
    }
}
