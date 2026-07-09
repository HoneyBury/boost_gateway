#pragma once

// HighResTimer — RAII helper for high-resolution timing.
//
// On POSIX systems this is a no-op; the system timer resolution is
// already sufficient for real-time service workloads. The class is
// kept for API stability across platforms.

namespace v2::platform {

class HighResTimer {
public:
    HighResTimer() noexcept = default;

    HighResTimer(const HighResTimer&) = delete;
    HighResTimer& operator=(const HighResTimer&) = delete;

    ~HighResTimer() noexcept = default;
};

}  // namespace v2::platform
