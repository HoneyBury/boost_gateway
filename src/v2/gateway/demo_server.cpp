#include "v2/gateway/demo_server.h"

#include "app/logging.h"

#include <nlohmann/json.hpp>

#include <utility>

namespace v2::gateway {

DemoServer::DemoServer(std::uint16_t port,
                       net::SessionOptions session_options,
                       DemoServerOptions options,
                       std::unique_ptr<v2::io::IoEngine> io_engine)
    : port_(port),
      session_options_(std::move(session_options)),
      options_(std::move(options)),
      io_engine_(std::move(io_engine)),
      adapter_(actor_system_, this),
      runtime_(actor_system_, adapter_) {
    set_write_scheduler([this](SessionId session_id, SessionWriteTask task) {
        std::shared_ptr<v2::io::IoSession> session;
        {
            std::scoped_lock lock(sessions_mutex_);
            const auto it = sessions_.find(session_id);
            if (it == sessions_.end()) {
                return false;
            }
            session = it->second;
        }
        if (io_engine_ == nullptr || !session || !task) {
            return false;
        }
        io_engine_->dispatch_to_core(session->owning_core_id(), std::move(task));
        return true;
    });
    gateway_actor_ = runtime_.create_gateway_actor();
    adapter_.bind_gateway(gateway_actor_);
    archive_store_ = std::make_unique<JsonFileBattleArchiveStore>("v2_archive");
    runtime_.set_archive_sink(archive_store_.get());
}

void DemoServer::start() {
    acceptor_ = io_engine_->listen("127.0.0.1",
                                   port_,
                                   session_options_,
                                   v2::io::IoListenOptions{.fixed_core_id = options_.acceptor_core_id});
    LOG_INFO("v2 demo server listening on 127.0.0.1:{}", acceptor_->local_port());
    do_accept();
    io_engine_->run();
}

void DemoServer::stop() {
    {
        std::scoped_lock lock(sessions_mutex_);
        acceptor_.reset();
        for (auto& [session_id, session] : sessions_) {
            (void)session_id;
            session->close();
        }
        sessions_.clear();
        session_core_by_id_.clear();
    }
    {
        std::scoped_lock lock(io_core_snapshot_mutex_);
        for (auto& [core_id, snapshot] : io_core_snapshots_by_id_) {
            (void)core_id;
            snapshot.active_sessions = 0;
        }
    }
    io_engine_->stop();
}

void DemoServer::set_write_scheduler(SessionWriteScheduler scheduler) {
    std::scoped_lock lock(scheduler_mutex_);
    write_scheduler_ = std::move(scheduler);
}

void DemoServer::dispatch_write(SessionId session_id, SessionWriteTask task) {
    if (!task) {
        return;
    }

    SessionWriteScheduler scheduler;
    {
        std::scoped_lock lock(scheduler_mutex_);
        scheduler = write_scheduler_;
    }

    if (scheduler && scheduler(session_id, task)) {
        const auto core_id = session_io_core(session_id);
        if (core_id.has_value()) {
            std::scoped_lock lock(io_core_snapshot_mutex_);
            auto& snapshot = io_core_snapshots_by_id_[*core_id];
            snapshot.core_id = *core_id;
            ++snapshot.outbound_dispatches;
        }
        return;
    }

    task();
}

void DemoServer::deliver(SessionWrite write) {
    std::shared_ptr<v2::io::IoSession> session;
    {
        std::scoped_lock lock(sessions_mutex_);
        const auto it = sessions_.find(write.envelope.session_id);
        if (it == sessions_.end()) {
            return;
        }
        session = it->second;
    }

    dispatch_write(
        write.envelope.session_id,
        [session, envelope = std::move(write.envelope)]() mutable {
            session->send(envelope.protocol_message_id,
                          envelope.request_id,
                          envelope.error_code,
                          std::move(envelope.body),
                          envelope.flags);
        });
}

std::uint16_t DemoServer::local_port() const {
    return acceptor_ == nullptr ? 0 : acceptor_->local_port();
}

std::uint32_t DemoServer::io_core_count() const {
    return io_engine_ == nullptr ? 0U : io_engine_->num_io_cores();
}

std::optional<std::uint32_t> DemoServer::acceptor_core_id() const noexcept {
    return acceptor_ == nullptr ? std::nullopt : std::optional<std::uint32_t>(acceptor_->owning_core_id());
}

std::optional<std::uint32_t> DemoServer::session_io_core(SessionId session_id) const {
    std::scoped_lock lock(sessions_mutex_);
    const auto it = session_core_by_id_.find(session_id);
    if (it == session_core_by_id_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::vector<DemoServerIoCoreSnapshot> DemoServer::io_core_snapshot() const {
    std::vector<DemoServerIoCoreSnapshot> snapshots;
    const auto core_count = io_core_count();
    snapshots.reserve(core_count);
    for (std::uint32_t core_id = 0; core_id < core_count; ++core_id) {
        snapshots.push_back(DemoServerIoCoreSnapshot{
            .core_id = core_id,
            .active_sessions = 0,
            .accepted_sessions = 0,
            .outbound_dispatches = 0,
        });
    }

    std::scoped_lock lock(io_core_snapshot_mutex_);
    for (const auto& [core_id, snapshot] : io_core_snapshots_by_id_) {
        if (core_id < snapshots.size()) {
            snapshots[core_id] = snapshot;
        } else {
            snapshots.push_back(snapshot);
        }
    }
    return snapshots;
}

DemoServerDiagnostics DemoServer::diagnostics() const {
    DemoServerDiagnostics result;
    result.local_port = local_port();
    result.io_core_count = io_core_count();
    result.acceptor_core_id = acceptor_core_id();
    result.io_cores = io_core_snapshot();
    for (const auto& snapshot : result.io_cores) {
        result.total_active_sessions += snapshot.active_sessions;
        result.total_accepted_sessions += snapshot.accepted_sessions;
        result.total_outbound_dispatches += snapshot.outbound_dispatches;
    }
    return result;
}

std::string DemoServer::diagnostics_json() const {
    const auto snapshot = diagnostics();
    nlohmann::json doc;
    doc["local_port"] = snapshot.local_port;
    doc["io_core_count"] = snapshot.io_core_count;
    doc["acceptor_core_id"] = snapshot.acceptor_core_id.has_value()
                                  ? nlohmann::json(*snapshot.acceptor_core_id)
                                  : nlohmann::json(nullptr);
    doc["total_active_sessions"] = snapshot.total_active_sessions;
    doc["total_accepted_sessions"] = snapshot.total_accepted_sessions;
    doc["total_outbound_dispatches"] = snapshot.total_outbound_dispatches;

    nlohmann::json io_cores = nlohmann::json::array();
    for (const auto& core : snapshot.io_cores) {
        io_cores.push_back({
            {"core_id", core.core_id},
            {"active_sessions", core.active_sessions},
            {"accepted_sessions", core.accepted_sessions},
            {"outbound_dispatches", core.outbound_dispatches},
        });
    }
    doc["io_cores"] = std::move(io_cores);
    return doc.dump();
}

void DemoServer::do_accept() {
    if (!acceptor_) {
        return;
    }
    acceptor_->async_accept([this](std::unique_ptr<v2::io::IoSession> session) {
        if (!session) {
            return;
        }

        const auto session_id = next_session_id_++;
        auto session_ref = std::shared_ptr<v2::io::IoSession>(std::move(session));
        const auto session_core = session_ref->owning_core_id();
        session_ref->set_packet_handler(
            [this, session_id](v2::io::IoSession::PacketMessage message) {
                (void)adapter_.handle_incoming(ClientEnvelope{
                    .session_id = session_id,
                    .protocol_message_id = message.message_id,
                    .request_id = message.request_id,
                    .error_code = message.error_code,
                    .flags = message.flags,
                    .body = std::move(message.body),
                });
            });
        session_ref->set_close_handler([this, session_id]() {
            runtime_.on_session_closed(session_id);
            std::optional<std::uint32_t> session_core_id;
            {
                std::scoped_lock lock(sessions_mutex_);
                sessions_.erase(session_id);
                const auto core_it = session_core_by_id_.find(session_id);
                if (core_it != session_core_by_id_.end()) {
                    session_core_id = core_it->second;
                    session_core_by_id_.erase(core_it);
                }
            }
            if (session_core_id.has_value()) {
                std::scoped_lock lock(io_core_snapshot_mutex_);
                auto it = io_core_snapshots_by_id_.find(*session_core_id);
                if (it != io_core_snapshots_by_id_.end() && it->second.active_sessions > 0) {
                    --it->second.active_sessions;
                }
            }
        });

        {
            std::scoped_lock lock(sessions_mutex_);
            sessions_.emplace(session_id, session_ref);
            session_core_by_id_.emplace(session_id, session_core);
            sessions_.at(session_id)->start();
        }
        {
            std::scoped_lock lock(io_core_snapshot_mutex_);
            auto& snapshot = io_core_snapshots_by_id_[session_core];
            snapshot.core_id = session_core;
            ++snapshot.active_sessions;
            ++snapshot.accepted_sessions;
        }

        do_accept();
    });
}

}  // namespace v2::gateway
