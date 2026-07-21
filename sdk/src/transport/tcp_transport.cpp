// SDK v4.2.0: TCP transport implementing ITransport.
#include "boost_gateway/sdk/transport/transport.h"

#include <boost/asio.hpp>

#include <atomic>
#include <condition_variable>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>

#ifdef __APPLE__
#include <sys/select.h>
#endif

namespace boost_gateway {
namespace sdk {
namespace transport {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

class TransportExecutor {
public:
    TransportExecutor() : state_(std::make_shared<State>()), worker_([state = state_] {
        for (;;) {
            std::function<void()> task;
            {
                std::unique_lock<std::mutex> lock(state->mutex);
                state->cv.wait(lock, [&] { return state->stopping || !state->tasks.empty(); });
                if (state->stopping) return;
                task = std::move(state->tasks.front());
                state->tasks.pop_front();
            }
            task();
        }
    }) {}

    ~TransportExecutor() { shutdown(); }

    void post(std::function<void()> task) {
        {
            std::lock_guard<std::mutex> lock(state_->mutex);
            if (state_->stopping) return;
            state_->tasks.push_back(std::move(task));
        }
        state_->cv.notify_one();
    }

    void shutdown() {
        {
            std::lock_guard<std::mutex> lock(state_->mutex);
            state_->stopping = true;
            state_->tasks.clear();
        }
        state_->cv.notify_all();
        if (!worker_.joinable()) return;
        if (worker_.get_id() == std::this_thread::get_id()) worker_.detach();
        else worker_.join();
    }

private:
    struct State {
        std::mutex mutex;
        std::condition_variable cv;
        std::deque<std::function<void()>> tasks;
        bool stopping = false;
    };
    std::shared_ptr<State> state_;
    std::thread worker_;
};

class TcpTransport final : public ITransport {
    struct State {
        State() : socket(io_context) {}
        asio::io_context io_context;
        tcp::socket socket;
        std::mutex socket_mutex;
        std::mutex callback_mutex;
        std::function<void(const std::string&)> async_receive_callback;
        std::atomic<bool> connected{false};
        std::atomic<bool> shutting_down{false};
        std::atomic<bool> callbacks_enabled{true};
    };

public:
    TcpTransport() : state_(std::make_shared<State>()) {}

    ~TcpTransport() override {
        state_->callbacks_enabled = false;
        state_->shutting_down = true;
        close_connection(state_);
        executor_.shutdown();
        join_read_thread();
    }

    bool connect(const std::string& host, std::uint16_t port,
                 std::chrono::milliseconds timeout) override {
        disconnect();
        return connect_state(state_, host, port, timeout);
    }

    void disconnect() override {
        close_connection(state_);
        join_read_thread();
    }

    bool is_connected() const override { return state_->connected; }

    bool send(const std::vector<char>& data) override {
        return send_state(state_, data);
    }

    std::vector<char> receive(std::chrono::milliseconds timeout) override {
        return receive_state(state_, timeout);
    }

    void set_receive_callback(ReceiveCallback /*cb*/) override {}

    void async_connect(const std::string& host, std::uint16_t port,
                       std::function<void(bool)> callback) override {
        auto state = state_;
        executor_.post([this, state, host, port, callback = std::move(callback)]() {
            close_connection(state);
            join_read_thread();
            bool ok = connect_state(state, host, port, std::chrono::seconds(5));
            if (ok) start_async_read(state);
            if (state->callbacks_enabled && callback) callback(ok);
        });
    }

    void async_send(const std::string& data,
                    std::function<void(bool)> callback) override {
        auto state = state_;
        executor_.post([state, data, callback = std::move(callback)]() {
            std::vector<char> buf(data.begin(), data.end());
            bool ok = send_state(state, buf);
            if (state->callbacks_enabled && callback) callback(ok);
        });
    }

    void set_async_receive_callback(
        std::function<void(const std::string&)> callback) override {
        std::lock_guard<std::mutex> lock(state_->callback_mutex);
        state_->async_receive_callback = std::move(callback);
    }

private:
    static void close_connection(const std::shared_ptr<State>& state) {
        state->connected = false;
        std::lock_guard<std::mutex> lock(state->socket_mutex);
        boost::system::error_code ec;
        state->socket.close(ec);
        state->io_context.stop();
    }

    static bool connect_state(const std::shared_ptr<State>& state,
                              const std::string& host, std::uint16_t port,
                              std::chrono::milliseconds timeout) {
        if (state->shutting_down) return false;
        const auto endpoint = tcp::endpoint(asio::ip::make_address(host), port);
        auto deadline = std::chrono::steady_clock::now() + timeout;
        while (!state->shutting_down && std::chrono::steady_clock::now() < deadline) {
            boost::system::error_code ec;
            {
                std::lock_guard<std::mutex> lock(state->socket_mutex);
                state->io_context.restart();
                state->socket = tcp::socket(state->io_context);
                state->socket.connect(endpoint, ec);
                if (ec) {
                    boost::system::error_code close_ec;
                    state->socket.close(close_ec);
                }
            }
            if (!ec) {
                state->connected = true;
                return true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        return false;
    }

    static bool send_state(const std::shared_ptr<State>& state,
                           const std::vector<char>& data) {
        std::lock_guard<std::mutex> lock(state->socket_mutex);
        if (!state->connected || !state->socket.is_open()) return false;
        boost::system::error_code ec;
        asio::write(state->socket, asio::buffer(data.data(), data.size()), ec);
        if (ec) state->connected = false;
        return !ec;
    }

    static std::vector<char> receive_state(const std::shared_ptr<State>& state,
                                           std::chrono::milliseconds timeout) {
        std::lock_guard<std::mutex> lock(state->socket_mutex);
        if (!state->connected || !state->socket.is_open()) return {};

        int fd = state->socket.native_handle();
        fd_set read_fds;
        FD_ZERO(&read_fds);
        FD_SET(fd, &read_fds);
        auto secs = std::chrono::duration_cast<std::chrono::seconds>(timeout);
        auto usecs = std::chrono::duration_cast<std::chrono::microseconds>(timeout - secs);
        struct timeval tv {};
        tv.tv_sec = static_cast<long>(secs.count());
        tv.tv_usec = static_cast<long>(usecs.count());
        if (::select(fd + 1, &read_fds, nullptr, nullptr, &tv) <= 0) return {};

        boost::system::error_code ec;
        auto available = state->socket.available(ec);
        if (ec) {
            state->connected = false;
            return {};
        }
        if (available == 0) available = 4096;
        std::vector<char> buf(available);
        auto n = state->socket.read_some(asio::buffer(buf.data(), buf.size()), ec);
        if (ec) {
            state->connected = false;
            return {};
        }
        buf.resize(n);
        return buf;
    }

    void start_async_read(const std::shared_ptr<State>& state) {
        join_read_thread();
        async_read_thread_ = std::thread([state] {
            while (!state->shutting_down && state->connected) {
                auto data = receive_state(state, std::chrono::milliseconds(100));
                if (data.empty()) continue;
                std::function<void(const std::string&)> callback;
                {
                    std::lock_guard<std::mutex> lock(state->callback_mutex);
                    callback = state->async_receive_callback;
                }
                if (state->callbacks_enabled && callback) {
                    callback(std::string(data.begin(), data.end()));
                }
            }
        });
    }

    void join_read_thread() {
        if (!async_read_thread_.joinable()) return;
        if (async_read_thread_.get_id() == std::this_thread::get_id()) {
            async_read_thread_.detach();
        } else {
            async_read_thread_.join();
        }
    }

    std::shared_ptr<State> state_;
    TransportExecutor executor_;
    std::thread async_read_thread_;
};

// Factory function for ConnectionPool
std::unique_ptr<ITransport> make_tcp_transport() {
    return std::make_unique<TcpTransport>();
}

}  // namespace transport
}  // namespace sdk
}  // namespace boost_gateway
