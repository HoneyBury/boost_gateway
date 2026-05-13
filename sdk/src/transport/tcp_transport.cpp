// SDK v4.0.0: TCP transport implementing ITransport.
#include "boost_gateway/sdk/transport/transport.h"

#include <boost/asio.hpp>

#include <atomic>
#include <thread>

#ifdef __APPLE__
#include <sys/select.h>
#endif

namespace boost_gateway {
namespace sdk {
namespace transport {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

class TcpTransport final : public ITransport {
public:
    TcpTransport() : socket_(io_context_) {}

    bool connect(const std::string& host, std::uint16_t port,
                 std::chrono::milliseconds timeout) override {
        boost::system::error_code ec;
        auto deadline = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < deadline) {
            socket_.connect(
                tcp::endpoint(asio::ip::make_address(host), port), ec);
            if (!ec) {
                connected_ = true;
                io_thread_ = std::thread([this] { io_context_.run(); });
                return true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        return false;
    }

    void disconnect() override {
        connected_ = false;
        boost::system::error_code ec;
        socket_.close(ec);
        io_context_.stop();
        if (io_thread_.joinable()) io_thread_.join();
    }

    bool is_connected() const override { return connected_; }

    bool send(const std::vector<char>& data) override {
        boost::system::error_code ec;
        asio::write(socket_, asio::buffer(data.data(), data.size()), ec);
        return !ec;
    }

    std::vector<char> receive(std::chrono::milliseconds timeout) override {
        // Wait for data with select() for portable timeout.
        int fd = socket_.native_handle();
        fd_set read_fds;
        FD_ZERO(&read_fds);
        FD_SET(fd, &read_fds);
        auto secs = std::chrono::duration_cast<std::chrono::seconds>(timeout);
        auto usecs = std::chrono::duration_cast<std::chrono::microseconds>(
            timeout - secs);
        struct timeval tv {};
        tv.tv_sec = static_cast<long>(secs.count());
        tv.tv_usec = static_cast<long>(usecs.count());
        if (::select(fd + 1, &read_fds, nullptr, nullptr, &tv) <= 0) {
            return {};
        }

        // Read available bytes.
        boost::system::error_code ec;
        std::size_t available = 0;
        {
            boost::system::error_code ec2;
            socket_.available(ec2);
            if (!ec2) available = socket_.available();
        }
        if (available == 0) available = 4096;
        std::vector<char> buf(available);
        auto n = socket_.read_some(asio::buffer(buf.data(), buf.size()), ec);
        if (ec) return {};
        buf.resize(n);
        return buf;
    }

    void set_receive_callback(ReceiveCallback /*cb*/) override {
        // Async callback not supported in sync transport mode.
        // Future: use async_read + callback dispatch.
    }

private:
    boost::asio::io_context io_context_;
    tcp::socket socket_{io_context_};
    std::atomic<bool> connected_{false};
    std::thread io_thread_;
};

// Factory function for ConnectionPool
std::unique_ptr<ITransport> make_tcp_transport() {
    return std::make_unique<TcpTransport>();
}

}  // namespace transport
}  // namespace sdk
}  // namespace boost_gateway
