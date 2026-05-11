#pragma once

#include "net/session.h"
#include "v2/io/io_engine.h"
#include "v2/gateway/battle_archive_store.h"
#include "v2/gateway/runtime.h"
#include "v2/gateway/session_adapter.h"

#include <cstdint>
#include <optional>
#include <mutex>
#include <memory>
#include <unordered_map>
#include <functional>
#include <vector>

namespace v2::gateway {

struct DemoServerOptions {
    std::optional<std::uint32_t> acceptor_core_id;
};

struct DemoServerIoCoreSnapshot {
    std::uint32_t core_id = 0;
    std::uint64_t active_sessions = 0;
    std::uint64_t accepted_sessions = 0;
    std::uint64_t outbound_dispatches = 0;
};

struct DemoServerDiagnostics {
    std::uint16_t local_port = 0;
    std::uint32_t io_core_count = 0;
    std::optional<std::uint32_t> acceptor_core_id;
    std::uint64_t total_active_sessions = 0;
    std::uint64_t total_accepted_sessions = 0;
    std::uint64_t total_outbound_dispatches = 0;
    std::vector<DemoServerIoCoreSnapshot> io_cores;
};

class DemoServer final : public DownstreamSessionWriteSink {
public:
    using SessionWriteTask = std::function<void()>;
    using SessionWriteScheduler = std::function<bool(SessionId, SessionWriteTask)>;

    DemoServer(std::uint16_t port,
               net::SessionOptions session_options = {},
               DemoServerOptions options = {},
               std::unique_ptr<v2::io::IoEngine> io_engine = std::make_unique<v2::io::AsioIoEngine>(1));

    void start();
    void stop();
    void deliver(SessionWrite write) override;
    void set_write_scheduler(SessionWriteScheduler scheduler);
    [[nodiscard]] std::uint16_t local_port() const;
    [[nodiscard]] std::uint32_t io_core_count() const;
    [[nodiscard]] std::optional<std::uint32_t> acceptor_core_id() const noexcept;
    [[nodiscard]] std::optional<std::uint32_t> session_io_core(SessionId session_id) const;
    [[nodiscard]] std::vector<DemoServerIoCoreSnapshot> io_core_snapshot() const;
    [[nodiscard]] DemoServerDiagnostics diagnostics() const;
    [[nodiscard]] std::string diagnostics_json() const;

private:
    void do_accept();
    void dispatch_write(SessionId session_id, SessionWriteTask task);

    std::uint16_t port_ = 0;
    net::SessionOptions session_options_;
    DemoServerOptions options_;
    std::unique_ptr<v2::io::IoEngine> io_engine_;
    std::unique_ptr<v2::io::IoAcceptor> acceptor_;
    v2::runtime::ActorSystem actor_system_;
    SessionAdapter adapter_;
    Runtime runtime_;
    std::unique_ptr<JsonFileBattleArchiveStore> archive_store_;
    v2::actor::ActorRef gateway_actor_;
    std::unordered_map<SessionId, std::shared_ptr<v2::io::IoSession>> sessions_;
    mutable std::mutex sessions_mutex_;
    mutable std::mutex io_core_snapshot_mutex_;
    std::unordered_map<SessionId, std::uint32_t> session_core_by_id_;
    std::unordered_map<std::uint32_t, DemoServerIoCoreSnapshot> io_core_snapshots_by_id_;
    mutable std::mutex scheduler_mutex_;
    SessionWriteScheduler write_scheduler_;
    SessionId next_session_id_ = 1;
};

}  // namespace v2::gateway
