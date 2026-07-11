#include "multi_process_test.h"

#include "app/logging.h"

#include <boost/asio.hpp>

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include <thread>

#include <sys/types.h>
#include <sys/wait.h>
#include <signal.h>
#include <unistd.h>
#include <spawn.h>
#include <stdlib.h>

extern "C" char **environ;

namespace v2_test {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

// ─── ProcessGuard internals (native OS processes, no boost::process) ──

struct ProcessGuard::ChildHandle {
    pid_t pid = -1;
    bool terminated = false;
};

ProcessGuard::ProcessGuard(const std::string& binary,
                           const std::vector<std::string>& args) {
    child_ = std::make_unique<ChildHandle>();

    // Use fork+exec instead of posix_spawnp because on macOS 14+,
    // posix_spawnp returns EBADF when closing kernel-managed fds.
    std::vector<const char*> argv;
    argv.push_back(binary.c_str());
    for (const auto& arg : args) {
        argv.push_back(arg.c_str());
    }
    argv.push_back(nullptr);

    pid_t pid = fork();
    if (pid == 0) {
        // Child: set own process group, close all non-stdio fds, then exec
        setpgid(0, 0);
        for (int fd = 3; fd < 1024; ++fd) {
            ::close(fd);
        }
        execvp(binary.c_str(), const_cast<char* const*>(argv.data()));
        _exit(127);
    }
    if (pid < 0) {
        startup_error_ = std::string("fork failed: ") + strerror(errno);
        return;
    }

    child_->pid = pid;
    started_ = true;
}

ProcessGuard::~ProcessGuard() noexcept {
    try {
        terminate();
    } catch (...) {
    }
}

ProcessGuard::ProcessGuard(ProcessGuard&& other) noexcept
    : child_(std::move(other.child_)),
      started_(other.started_),
      startup_error_(std::move(other.startup_error_)) {
    other.started_ = false;
}

ProcessGuard& ProcessGuard::operator=(ProcessGuard&& other) noexcept {
    if (this != &other) {
        terminate();
        child_ = std::move(other.child_);
        started_ = other.started_;
        startup_error_ = std::move(other.startup_error_);
        other.started_ = false;
    }
    return *this;
}

void ProcessGuard::terminate() {
    if (!child_ || child_->terminated) return;
    child_->terminated = true;

    if (child_->pid <= 0) return;

    // 1. Graceful signal
    ::kill(child_->pid, SIGTERM);

    // 2. Wait up to 5 seconds
    for (int i = 0; i < 50; ++i) {
        int status = 0;
        if (waitpid(child_->pid, &status, WNOHANG) == child_->pid) {
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    // 3. Force kill
    ::kill(child_->pid, SIGKILL);
    waitpid(child_->pid, nullptr, 0);
}

bool ProcessGuard::is_running() const {
    if (!child_ || child_->terminated || !started_) return false;
    if (child_->pid <= 0) return false;
    int status = 0;
    pid_t result = waitpid(child_->pid, &status, WNOHANG);
    if (result == 0) return true;    // still running
    if (result == child_->pid) return false; // exited
    return false;  // error
}

std::optional<int> ProcessGuard::exit_code() const {
    if (!child_ || !started_) return std::nullopt;
    if (child_->pid <= 0) return std::nullopt;
    int status = 0;
    pid_t result = waitpid(child_->pid, &status, WNOHANG);
    if (result != child_->pid) return std::nullopt;
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return -WTERMSIG(status);
    return std::nullopt;
}

// ─── TestClient ────────────────────────────────────────────────────

TestClient::TestClient() : socket_(io_context_) {}

TestClient::~TestClient() { close(); }

void TestClient::connect(std::uint16_t port) {
    connect("127.0.0.1", port);
}

void TestClient::connect(const std::string& host, std::uint16_t port) {
    using namespace std::chrono_literals;
    for (int i = 0; i < 60; ++i) {
        boost::system::error_code ec;
        socket_.connect(
            tcp::endpoint(asio::ip::make_address(host), port), ec);
        if (!ec) return;
        std::this_thread::sleep_for(50ms);
    }
    throw std::runtime_error("TestClient::connect timeout on " +
                             host + ":" + std::to_string(port));
}

void TestClient::close() {
    boost::system::error_code ec;
    socket_.shutdown(tcp::socket::shutdown_both, ec);
    socket_.close(ec);
}

void TestClient::send(std::uint16_t message_id, std::uint32_t request_id,
                      const std::string& body) {
    std::string encoded =
        net::packet::encode(message_id, request_id, 0, body, 0);
    asio::write(socket_, asio::buffer(encoded));
}

net::packet::DecodedPacket TestClient::read() {
    return read(std::chrono::milliseconds(5000));
}

namespace {

void signal_and_join(std::thread& timeout_thread,
                     std::condition_variable& cv,
                     std::mutex& mtx,
                     bool& read_completed) {
    {
        std::lock_guard<std::mutex> lock(mtx);
        read_completed = true;
    }
    cv.notify_one();
    if (timeout_thread.joinable()) {
        timeout_thread.join();
    }
}

}  // namespace

net::packet::DecodedPacket TestClient::read(std::chrono::milliseconds timeout) {
    // Enforce a total deadline across both header and body reads.
    // We use a helper thread with a condition_variable because
    // asio::steady_timer::async_wait posts its handler to io_context_,
    // which is never polled — timer callbacks never fire.
    std::mutex mtx;
    std::condition_variable cv;
    bool read_completed = false;

    std::thread timeout_thread([&]() {
        std::unique_lock<std::mutex> lock(mtx);
        if (cv.wait_for(lock, timeout, [&] { return read_completed; })) {
            return;  // read completed before deadline
        }
        boost::system::error_code ignored;
        socket_.cancel(ignored);
    });

    boost::system::error_code ec;

    // Read the 4-byte length prefix
    net::packet::LengthHeader length_header{};
    asio::read(socket_, asio::buffer(length_header.data(), length_header.size()), ec);

    if (ec == asio::error::operation_aborted) {
        signal_and_join(timeout_thread, cv, mtx, read_completed);
        throw std::runtime_error("TestClient::read timeout");
    }
    if (ec) {
        signal_and_join(timeout_thread, cv, mtx, read_completed);
        throw std::runtime_error("TestClient::read error: " + ec.message());
    }

    std::uint32_t total_length = net::packet::decode_length(length_header);
    if (total_length > 64 * 1024 * 1024) {
        signal_and_join(timeout_thread, cv, mtx, read_completed);
        throw std::runtime_error("TestClient::read excessive payload length");
    }

    // Read the rest of the packet body (covered by the same deadline).
    std::vector<char> payload(total_length);
    asio::read(socket_, asio::buffer(payload.data(), payload.size()), ec);

    signal_and_join(timeout_thread, cv, mtx, read_completed);

    if (ec == asio::error::operation_aborted) {
        throw std::runtime_error("TestClient::read timeout");
    }
    if (ec) throw std::runtime_error("TestClient::read body error: " + ec.message());

    return net::packet::decode_payload(payload);
}

net::packet::DecodedPacket TestClient::exchange(
    std::uint16_t message_id, std::uint32_t request_id,
    const std::string& body) {
    send(message_id, request_id, body);
    return read();
}

net::packet::DecodedPacket TestClient::exchange(
    std::uint16_t message_id, std::uint32_t request_id,
    const std::string& body, std::chrono::milliseconds timeout) {
    send(message_id, request_id, body);
    return read(timeout);
}

net::packet::DecodedPacket TestClient::expect_message(std::uint16_t message_id) {
    return expect_message(message_id, std::chrono::milliseconds(5000));
}

net::packet::DecodedPacket TestClient::expect_message(
    std::uint16_t message_id, std::chrono::milliseconds timeout) {
    auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now());
        if (remaining.count() <= 0) break;
        try {
            auto packet = read(remaining);
            if (packet.message_id == message_id) return packet;
        } catch (const std::runtime_error&) {
            break;
        }
    }
    throw std::runtime_error("TestClient::expect_message timeout for msg_id=" +
                             std::to_string(message_id));
}

// ─── MultiProcessFixture ───────────────────────────────────────────

#ifdef V2_GATEWAY_BINARY
std::string MultiProcessFixture::gateway_binary() { return V2_GATEWAY_BINARY; }
#else
std::string MultiProcessFixture::gateway_binary() { return "v2_gateway_demo"; }
#endif

#ifdef V2_LOGIN_BINARY
std::string MultiProcessFixture::login_binary() { return V2_LOGIN_BINARY; }
#else
std::string MultiProcessFixture::login_binary() { return "v2_login_backend"; }
#endif

#ifdef V2_ROOM_BINARY
std::string MultiProcessFixture::room_binary() { return V2_ROOM_BINARY; }
#else
std::string MultiProcessFixture::room_binary() { return "v2_room_backend"; }
#endif

#ifdef V2_BATTLE_BINARY
std::string MultiProcessFixture::battle_binary() { return V2_BATTLE_BINARY; }
#else
std::string MultiProcessFixture::battle_binary() { return "v2_battle_backend"; }
#endif

#ifdef V2_LEADERBOARD_BINARY
std::string MultiProcessFixture::leaderboard_binary() { return V2_LEADERBOARD_BINARY; }
#else
std::string MultiProcessFixture::leaderboard_binary() { return "v2_leaderboard_backend"; }
#endif

ServiceProcess* MultiProcessFixture::find_service(const std::string& service_id) {
    for (auto& svc : services_) {
        if (svc.service_id == service_id) return &svc;
    }
    return nullptr;
}

const ServiceProcess* MultiProcessFixture::find_service(const std::string& service_id) const {
    for (const auto& svc : services_) {
        if (svc.service_id == service_id) return &svc;
    }
    return nullptr;
}

void MultiProcessFixture::SetUp() {
    services_.clear();
    startup_error_.clear();
    all_started_ = false;
    gateway_port_ = reserve_free_port();
    login_port_ = reserve_free_port();
    room_port_ = reserve_free_port();
    battle_port_ = reserve_free_port();
    leaderboard_port_ = reserve_free_port();
    setenv("CONFIG_PATH", "/tmp/boost_gateway_multi_process_no_config.json", 1);
    setenv("BOOST_DISABLE_REDIS_AUTO_CONNECT", "1", 1);
}

void MultiProcessFixture::TearDown() {
    stop_all();
    services_.clear();
    unsetenv("BOOST_DISABLE_REDIS_AUTO_CONNECT");
    unsetenv("CONFIG_PATH");
}

bool MultiProcessFixture::start_all() {
    if (all_started_) return true;

    // Start in order: login → room → battle → leaderboard → gateway
    if (!start_service("login")) return false;
    if (!start_service("room")) return false;
    if (!start_service("battle")) return false;
    if (!start_service("leaderboard")) return false;
    if (!start_service("gateway")) return false;

    all_started_ = true;
    return true;
}

void MultiProcessFixture::stop_all() {
    // Stop in reverse order
    stop_service("gateway");
    stop_service("leaderboard");
    stop_service("battle");
    stop_service("room");
    stop_service("login");
    all_started_ = false;
}

void MultiProcessFixture::stop_service(const std::string& service_id) {
    auto* svc = find_service(service_id);
    if (svc == nullptr) return;
    svc->process.terminate();
}

bool MultiProcessFixture::start_service(const std::string& service_id) {
    // Skip if already running
    auto* existing = find_service(service_id);
    if (existing != nullptr && existing->process.is_running()) {
        return true;
    }

    std::string binary;
    std::vector<std::string> args;
    std::uint16_t port = 0;

    if (service_id == "gateway") {
        binary = gateway_binary();
        port = gateway_port_;
        args = {
            "--port", std::to_string(gateway_port_),
            "--io-cores", "1",
            "--login-host", "127.0.0.1",
            "--login-port", std::to_string(login_port_),
            "--room-host", "127.0.0.1",
            "--room-port", std::to_string(room_port_),
            "--battle-host", "127.0.0.1",
            "--battle-port", std::to_string(battle_port_),
            "--leaderboard-host", "127.0.0.1",
            "--leaderboard-port", std::to_string(leaderboard_port_),
        };
    } else if (service_id == "login") {
        binary = login_binary();
        port = login_port_;
        args = {std::to_string(login_port_)};
    } else if (service_id == "room") {
        binary = room_binary();
        port = room_port_;
        args = {std::to_string(room_port_)};
    } else if (service_id == "battle") {
        binary = battle_binary();
        port = battle_port_;
        args = {std::to_string(battle_port_)};
    } else if (service_id == "leaderboard") {
        binary = leaderboard_binary();
        port = leaderboard_port_;
        args = {std::to_string(leaderboard_port_)};
    } else {
        startup_error_ = "unknown service: " + service_id;
        return false;
    }

    ProcessGuard pg(binary, args);
    if (!pg.started()) {
        startup_error_ = service_id + " failed to start: " + pg.startup_error();
        return false;
    }

    // Wait for the service to be ready
    if (!wait_for_port(port, kServiceStartTimeout)) {
        startup_error_ = service_id + " port " + std::to_string(port) +
                         " not ready within timeout";
        return false;
    }

    services_.push_back(ServiceProcess{
        .service_id = service_id,
        .port = port,
        .process = std::move(pg),
    });
    return true;
}

std::unique_ptr<TestClient> MultiProcessFixture::make_client() {
    auto client = std::make_unique<TestClient>();
    client->connect(gateway_port_);
    return client;
}

std::uint16_t MultiProcessFixture::reserve_free_port() {
    boost::asio::io_context io;
    tcp::acceptor acceptor(io, tcp::endpoint(tcp::v4(), 0));
    return acceptor.local_endpoint().port();
}

bool MultiProcessFixture::wait_for_port(std::uint16_t port,
                                         std::chrono::milliseconds timeout) {
    using namespace std::chrono_literals;
    auto deadline = std::chrono::steady_clock::now() + timeout;

    while (std::chrono::steady_clock::now() < deadline) {
        try {
            boost::asio::io_context io;
            tcp::socket sock(io);
            sock.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), port));
            return true;
        } catch (...) {
            std::this_thread::sleep_for(kServicePollInterval);
        }
    }
    return false;
}

}  // namespace v2_test
