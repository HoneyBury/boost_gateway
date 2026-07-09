#include "v2/test/fault_injector.h"

#include <boost/asio.hpp>
#include <boost/system/error_code.hpp>

#include <array>
#include <algorithm>
#include <limits>
#include <memory>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <utility>

namespace v2::test {

namespace {

using tcp = boost::asio::ip::tcp;

constexpr std::size_t kBufferSize = 4096;

}  // anonymous namespace

// ============================================================================
// NetworkPartitionSimulator::Impl
// ============================================================================

struct NetworkPartitionSimulator::Impl {
    Impl(uint16_t listen_port, std::string target_host, uint16_t target_port)
        : listen_port_(listen_port)
        , target_host_(std::move(target_host))
        , target_port_(target_port)
        , rng_(std::random_device{}())
        , drop_dist_(0.0, 1.0) {}

    ~Impl() { stop(); }

    void start() {
        if (running_.load()) {
            return;
        }
        running_.store(true);
        total_bytes_.store(0);

        boost::system::error_code ec;
        acceptor_ = std::make_unique<tcp::acceptor>(io_);
        acceptor_->open(tcp::v4(), ec);
        if (ec) {
            running_.store(false);
            acceptor_.reset();
            return;
        }
        acceptor_->set_option(tcp::acceptor::reuse_address(true), ec);
        if (ec) {
            running_.store(false);
            acceptor_.reset();
            return;
        }
        acceptor_->bind(tcp::endpoint(tcp::v4(), listen_port_), ec);
        if (ec) {
            running_.store(false);
            acceptor_.reset();
            return;
        }
        acceptor_->listen(boost::asio::socket_base::max_listen_connections, ec);
        if (ec) {
            running_.store(false);
            acceptor_.reset();
            return;
        }
        acceptor_->non_blocking(true, ec);
        if (ec) {
            running_.store(false);
            acceptor_->close();
            acceptor_.reset();
            return;
        }

        listen_port_ = acceptor_->local_endpoint().port();

        acceptor_thread_ = std::make_unique<std::thread>([this]() {
            acceptor_loop();
        });
    }

    void stop() {
        running_.store(false);
        if (acceptor_) {
            boost::system::error_code ec;
            acceptor_->cancel(ec);
            acceptor_->close(ec);
        }
        close_active_sockets();
        if (acceptor_thread_ && acceptor_thread_->joinable()) {
            acceptor_thread_->join();
            acceptor_thread_.reset();
        }
        acceptor_.reset();
        std::vector<std::thread> relay_threads;
        {
            std::lock_guard<std::mutex> lock(connection_mutex_);
            relay_threads.swap(relay_threads_);
        }
        for (auto& relay_thread : relay_threads) {
            if (relay_thread.joinable()) {
                relay_thread.join();
            }
        }
        std::lock_guard<std::mutex> lock(connection_mutex_);
        active_sockets_.clear();
    }

    uint16_t listen_port_{0};
    std::string target_host_;
    uint16_t target_port_{0};

    std::atomic<bool> running_{false};
    std::atomic<std::size_t> drop_after_bytes_{
        std::numeric_limits<std::size_t>::max()};
    std::atomic<double> drop_rate_{0.0};
    std::atomic<std::size_t> total_bytes_{0};

private:
    void acceptor_loop() {
        while (running_.load()) {
            boost::system::error_code ec;

            auto client = std::make_shared<tcp::socket>(io_);
            if (!acceptor_) {
                break;
            }
            acceptor_->accept(*client, ec);
            if (ec == boost::asio::error::would_block ||
                ec == boost::asio::error::try_again) {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
                continue;
            }
            if (ec || !running_.load()) {
                break;
            }

            // Resolve the upstream target.
            auto upstream = std::make_shared<tcp::socket>(io_);
            tcp::resolver resolver(io_);
            auto endpoints = resolver.resolve(
                target_host_, std::to_string(target_port_), ec);
            if (ec || endpoints.empty()) {
                boost::system::error_code ignore;
                client->close(ignore);
                continue;
            }

            boost::asio::connect(*upstream, endpoints, ec);
            if (ec) {
                boost::system::error_code ignore;
                client->close(ignore);
                continue;
            }

            register_socket(client);
            register_socket(upstream);

            // Spawn two relay threads — one for each direction.
            // Each holds a shared_ptr to both sockets so that both sockets
            // remain alive until both relay threads have exited.
            auto client_holder = client;
            auto upstream_holder = upstream;

            {
                std::lock_guard<std::mutex> lock(connection_mutex_);
                relay_threads_.emplace_back([this, client, upstream]() {
                    relay_direction(*client, *upstream, client, upstream);
                });
            }

            {
                std::lock_guard<std::mutex> lock(connection_mutex_);
                relay_threads_.emplace_back(
                    [this, source = std::move(upstream),
                     dest = std::move(client_holder),
                     closer_a = std::move(client),
                     closer_b = std::move(upstream_holder)]() {
                        relay_direction(*source, *dest, closer_a, closer_b);
                    });
            }
        }
    }

    void relay_direction(tcp::socket& source, tcp::socket& dest,
                         std::shared_ptr<tcp::socket> closer_a,
                         std::shared_ptr<tcp::socket> closer_b) {
        std::array<char, kBufferSize> buf;
        boost::system::error_code ec;

        while (running_.load()) {
            auto len = source.read_some(boost::asio::buffer(buf), ec);
            if (ec) {
                break;
            }

            // Check the drop-after-bytes threshold. Only track total bytes
            // when the threshold is actually configured (not max).
            auto threshold = drop_after_bytes_.load();
            if (threshold != std::numeric_limits<std::size_t>::max()) {
                auto prior = total_bytes_.fetch_add(len);
                if (prior >= threshold) {
                    continue;  // Exceeded threshold — drop this chunk.
                }
            }

            // Check random drop rate.
            auto rate = drop_rate_.load();
            if (rate > 0.0) {
                std::lock_guard<std::mutex> lock(drop_mutex_);
                if (drop_dist_(rng_) < rate) {
                    continue;  // Randomly dropped.
                }
            }

            boost::asio::write(dest, boost::asio::buffer(buf, len), ec);
            if (ec) {
                break;
            }
        }

        // Close both sockets so the relay thread running in the opposite
        // direction also exits.
        close_socket(closer_a);
        close_socket(closer_b);
        unregister_socket(closer_a);
        unregister_socket(closer_b);
    }

    void close_active_sockets() {
        std::vector<std::shared_ptr<tcp::socket>> sockets;
        {
            std::lock_guard<std::mutex> lock(connection_mutex_);
            sockets = active_sockets_;
        }
        for (const auto& socket : sockets) {
            close_socket(socket);
        }
    }

    void close_socket(const std::shared_ptr<tcp::socket>& socket) {
        if (!socket) {
            return;
        }

        boost::system::error_code ec;
        socket->cancel(ec);
        socket->shutdown(tcp::socket::shutdown_both, ec);
        socket->close(ec);
    }

    void register_socket(const std::shared_ptr<tcp::socket>& socket) {
        std::lock_guard<std::mutex> lock(connection_mutex_);
        active_sockets_.push_back(socket);
    }

    void unregister_socket(const std::shared_ptr<tcp::socket>& socket) {
        if (!socket) {
            return;
        }

        std::lock_guard<std::mutex> lock(connection_mutex_);
        active_sockets_.erase(
            std::remove_if(
                active_sockets_.begin(),
                active_sockets_.end(),
                [&](const std::shared_ptr<tcp::socket>& candidate) {
                    return candidate.get() == socket.get();
                }),
            active_sockets_.end());
    }

    // I/O objects must be declared before the thread that uses them.
    boost::asio::io_context io_;
    std::unique_ptr<tcp::acceptor> acceptor_;
    std::unique_ptr<std::thread> acceptor_thread_;
    std::mutex connection_mutex_;
    std::vector<std::shared_ptr<tcp::socket>> active_sockets_;
    std::vector<std::thread> relay_threads_;

    std::mt19937 rng_;
    std::uniform_real_distribution<double> drop_dist_;
    std::mutex drop_mutex_;
};

// ============================================================================
// NetworkPartitionSimulator  (public delegation to Impl)
// ============================================================================

NetworkPartitionSimulator::NetworkPartitionSimulator(
    uint16_t listen_port,
    const std::string& target_host,
    uint16_t target_port)
    : impl_(std::make_unique<Impl>(listen_port, target_host, target_port)) {}

NetworkPartitionSimulator::~NetworkPartitionSimulator() = default;

void NetworkPartitionSimulator::start() { impl_->start(); }
void NetworkPartitionSimulator::stop() { impl_->stop(); }

void NetworkPartitionSimulator::set_drop_after_bytes(std::size_t bytes) {
    impl_->drop_after_bytes_.store(bytes);
}

void NetworkPartitionSimulator::set_drop_rate(double rate) {
    impl_->drop_rate_.store(rate);
}

uint16_t NetworkPartitionSimulator::listen_port() const {
    return impl_->listen_port_;
}

bool NetworkPartitionSimulator::is_running() const {
    return impl_->running_.load();
}

}  // namespace v2::test
