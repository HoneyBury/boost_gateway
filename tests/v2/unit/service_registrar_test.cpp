#include "app/logging.h"
#include "v2/service/service_registrar.h"

#include <gtest/gtest.h>

#include <chrono>

namespace {

using namespace std::chrono_literals;

TEST(ServiceRegistrarTest, StopDoesNotWaitForFullHeartbeatInterval) {
    app::logging::init("service_registrar_test");

    v3::cluster::ClusterRouter router;
    v2::service::ServiceRegistrar registrar(
        router,
        "login",
        "127.0.0.1",
        19001,
        "",
        5s);

    registrar.start();

    const auto started_at = std::chrono::steady_clock::now();
    registrar.stop();
    const auto elapsed = std::chrono::steady_clock::now() - started_at;

    EXPECT_LT(elapsed, 1s);
    EXPECT_FALSE(registrar.is_registered());
}

}  // namespace
