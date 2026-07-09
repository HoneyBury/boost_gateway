#include "app/process_supervisor.h"

#include "app/logging.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <system_error>

#include <csignal>
#include <cerrno>
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>
#include <spawn.h>

extern "C" char** environ;

namespace app {

// ===================================================================
// Platform-independent helpers
// ===================================================================

namespace {

// RAII wrapper for TCP socket on POSIX.
class TcpSocket {
public:
    TcpSocket() : sock_(-1) {}
    ~TcpSocket() { if (sock_ >= 0) ::close(sock_); }

    bool connect(const std::string& host, std::uint16_t port, int timeout_ms) {
        sock_ = socket(AF_INET, SOCK_STREAM, 0);
        if (sock_ < 0) return false;

        // Set non-blocking.
        int flags = fcntl(sock_, F_GETFL, 0);
        fcntl(sock_, F_SETFL, flags | O_NONBLOCK);

        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(port);
        inet_pton(AF_INET, host.c_str(), &addr.sin_addr);

        ::connect(sock_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));

        // Wait with select().
        fd_set write_set;
        FD_ZERO(&write_set);
        FD_SET(sock_, &write_set);

        timeval tv{};
        tv.tv_sec = timeout_ms / 1000;
        tv.tv_usec = (timeout_ms % 1000) * 1000;

        int ret = select(sock_ + 1, nullptr, &write_set, nullptr, &tv);
        if (ret <= 0) return false;

        int so_error = 0;
        socklen_t len = sizeof(so_error);
        getsockopt(sock_, SOL_SOCKET, SO_ERROR, &so_error, &len);
        return so_error == 0;
    }

private:
    int sock_;
};

}  // anonymous namespace

// ===================================================================
// Constructor / Destructor
// ===================================================================

ProcessSupervisor::ProcessSupervisor() {
    LOG_INFO("ProcessSupervisor created");
}

ProcessSupervisor::~ProcessSupervisor() {
    stop_all();
    LOG_INFO("ProcessSupervisor destroyed");
}

// ===================================================================
// Configuration
// ===================================================================

void ProcessSupervisor::add_process(const ProcessConfig& config) {
    auto state = std::make_shared<ProcessState>();
    state->config = config;
    state->pid = -1;
    processes_.push_back(std::move(state));
    LOG_INFO("ProcessSupervisor: registered process '{}' ({})",
             config.name, config.executable);
}

void ProcessSupervisor::set_crash_callback(CrashCallback cb) {
    crash_callback_ = std::move(cb);
}

// ===================================================================
// Health check
// ===================================================================

bool ProcessSupervisor::health_check(const ProcessConfig& config) {
    if (config.health_check_port == 0) {
        return true;
    }

    int timeout_ms = static_cast<int>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            config.health_check_timeout).count());

    TcpSocket sock;
    return sock.connect("127.0.0.1", config.health_check_port, timeout_ms);
}

// ===================================================================
// Start all processes
// ===================================================================

bool ProcessSupervisor::start_all() {
    LOG_INFO("ProcessSupervisor: starting all {} process(es)", processes_.size());

    bool all_ok = true;

    for (auto& state : processes_) {
        if (state->running.load()) {
            LOG_WARN("ProcessSupervisor: '{}' is already running", state->config.name);
            continue;
        }

        state->running.store(true);
        state->stopping.store(false);
        state->restart_attempts = 0;

        bool started = spawn_posix(state->config, *state);

        if (!started) {
            LOG_ERROR("ProcessSupervisor: failed to start '{}'", state->config.name);
            state->running.store(false);
            all_ok = false;
            continue;
        }

        LOG_INFO("ProcessSupervisor: started '{}' (PID {})",
                 state->config.name,
                 state->pid);

        state->monitor_thread = std::thread(&ProcessSupervisor::monitor_routine,
                                             this, state);
    }

    return all_ok;
}

// ===================================================================
// Stop all
// ===================================================================

void ProcessSupervisor::stop_all() {
    LOG_INFO("ProcessSupervisor: stopping all processes");
    supervisor_stopping_.store(true);

    for (auto& state : processes_) {
        if (!state->running.load()) continue;
        state->stopping.store(true);
        terminate_process(state->config, *state);
    }

    for (auto& state : processes_) {
        if (state->monitor_thread.joinable()) {
            state->monitor_thread.join();
        }
    }

    LOG_INFO("ProcessSupervisor: all processes stopped");
}

// ===================================================================
// Wait
// ===================================================================

void ProcessSupervisor::wait() {
    LOG_INFO("ProcessSupervisor: waiting for all processes to exit");

    bool any_running = true;
    while (any_running) {
        any_running = false;
        for (auto& state : processes_) {
            if (state->running.load()) {
                any_running = true;
                break;
            }
        }
        if (any_running) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }

    for (auto& state : processes_) {
        if (state->monitor_thread.joinable()) {
            state->monitor_thread.join();
        }
    }

    LOG_INFO("ProcessSupervisor: all processes have exited");
}

// ===================================================================
// Restart single process
// ===================================================================

bool ProcessSupervisor::restart_process(const std::string& name) {
    for (auto& state : processes_) {
        if (state->config.name != name) continue;

        state->stopping.store(true);
        terminate_process(state->config, *state);

        if (state->monitor_thread.joinable()) {
            state->monitor_thread.join();
        }

        state->running.store(false);
        state->stopping.store(false);
        state->restart_attempts = 0;

        bool started = spawn_posix(state->config, *state);

        if (!started) {
            LOG_ERROR("ProcessSupervisor: failed to restart '{}'", name);
            return false;
        }

        state->running.store(true);
        state->monitor_thread = std::thread(&ProcessSupervisor::monitor_routine,
                                             this, state);
        LOG_INFO("ProcessSupervisor: restarted '{}' (PID {})", name, state->pid);
        return true;
    }

    LOG_WARN("ProcessSupervisor: process '{}' not found for restart", name);
    return false;
}

// ===================================================================
// is_running
// ===================================================================

bool ProcessSupervisor::is_running(const std::string& name) const {
    for (const auto& state : processes_) {
        if (state->config.name == name) {
            return state->running.load();
        }
    }
    return false;
}

// ===================================================================
// restart_count
// ===================================================================

int ProcessSupervisor::restart_count(const std::string& name) const {
    for (const auto& state : processes_) {
        if (state->config.name == name) {
            return state->restart_attempts;
        }
    }
    return 0;
}

// ===================================================================
// Monitor routine
// ===================================================================

void ProcessSupervisor::monitor_routine(std::shared_ptr<ProcessState> state) {
    const auto& config = state->config;
    LOG_DEBUG("ProcessSupervisor: monitor started for '{}'", config.name);

    while (!state->stopping.load() && !supervisor_stopping_.load()) {
        int status = 0;
        pid_t result = waitpid(state->pid, &status, WNOHANG);

        if (result == state->pid) {
            int exit_code = -1;
            if (WIFEXITED(status)) {
                exit_code = WEXITSTATUS(status);
            } else if (WIFSIGNALED(status)) {
                exit_code = -WTERMSIG(status);
            }

            LOG_WARN("ProcessSupervisor: '{}' exited with code {}",
                     config.name, exit_code);

            state->running.store(false);

            if (crash_callback_) {
                crash_callback_(config.name, exit_code);
            }

            if (!state->stopping.load() && !supervisor_stopping_.load()) {
                state->restart_attempts++;

                if (config.max_restarts > 0 &&
                    state->restart_attempts > config.max_restarts) {
                    LOG_ERROR("ProcessSupervisor: '{}' exceeded max restarts ({})",
                              config.name, config.max_restarts);
                    break;
                }

                LOG_INFO("ProcessSupervisor: restarting '{}' in {} ms (attempt {}/{})",
                         config.name, config.restart_delay_ms,
                         state->restart_attempts,
                         config.max_restarts > 0 ? config.max_restarts : -1);

                std::this_thread::sleep_for(
                    std::chrono::milliseconds(config.restart_delay_ms));

                if (state->stopping.load() || supervisor_stopping_.load()) break;

                bool restarted = spawn_posix(config, *state);
                if (restarted) {
                    state->running.store(true);
                    LOG_INFO("ProcessSupervisor: restarted '{}' (PID {})",
                             config.name, state->pid);
                } else {
                    LOG_ERROR("ProcessSupervisor: restart failed for '{}'",
                              config.name);
                    break;
                }
            } else {
                break;
            }
        } else if (result < 0) {
            LOG_ERROR("ProcessSupervisor: waitpid failed for '{}'", config.name);
            break;
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }

    state->running.store(false);
    state->pid = -1;

    LOG_DEBUG("ProcessSupervisor: monitor stopped for '{}'", config.name);
}

// ===================================================================
// terminate_process
// ===================================================================

void ProcessSupervisor::terminate_process(
    const ProcessConfig& config, ProcessState& state) {

    LOG_INFO("ProcessSupervisor: terminating '{}'", config.name);

    if (state.pid <= 0) return;

    // 1. Graceful signal the whole process group.
    const pid_t process_group = -state.pid;
    ::kill(process_group, SIGTERM);

    // 2. Wait up to 5 seconds.
    for (int i = 0; i < 50; ++i) {
        int status = 0;
        pid_t result = waitpid(state.pid, &status, WNOHANG);
        if (result == state.pid) {
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    // 3. Force kill the whole process group.
    LOG_WARN("ProcessSupervisor: force killing '{}' (PID {})", config.name, state.pid);
    ::kill(process_group, SIGKILL);
    waitpid(state.pid, nullptr, 0);
}

// ===================================================================
// Process spawning (POSIX)
// ===================================================================

bool ProcessSupervisor::spawn_posix(
    const ProcessConfig& config, ProcessState& state) {

    std::vector<const char*> argv;
    argv.push_back(config.executable.c_str());
    for (const auto& arg : config.args) {
        argv.push_back(arg.c_str());
    }
    argv.push_back(nullptr);

    pid_t pid = fork();
    if (pid == 0) {
        // Child: set own process group, close non-stdio fds, exec.
        setpgid(0, 0);
        for (int fd = 3; fd < 1024; ++fd) {
            ::close(fd);
        }
        execvp(config.executable.c_str(), const_cast<char* const*>(argv.data()));
        _exit(127);
    }

    if (pid < 0) {
        LOG_ERROR("ProcessSupervisor: fork failed for '{}' (error {})",
                  config.name, strerror(errno));
        return false;
    }

    state.pid = pid;

    LOG_DEBUG("ProcessSupervisor: spawned '{}' (PID {})", config.name, state.pid);
    return true;
}

}  // namespace app
